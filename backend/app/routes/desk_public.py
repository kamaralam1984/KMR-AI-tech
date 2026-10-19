"""API used by the desktop assistant (`/desk/v1`). Authenticated by a desktop token.

Rules every handler follows:
- A desktop token works only here (an extension token does not), acts as its user, and the user's
  current membership is checked on every call.
- Nothing the person types and nothing read from their screen is stored. Only counts and outcomes
  are audited.
- A context that holds a sensitive window or what still looks like personal data is rejected whole.
- The server only DRAFTS steps. It checks each against what the client really sent and the policy,
  and the app checks the policy again before anything is done. The server never acts on a computer.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.auth.deps import get_engine, get_rate_limiter
from app.config import Settings, get_settings
from app.db.audit import record_audit
from app.db.models import AuditEvent, DeskStep, Organization, User
from app.db.tenant import tenant_session
from app.desktop.policy import sensitive_window
from app.desktop.providers import DeskProvider
from app.desktop.schemas import ChatIn, ChatOut, MeOut, ReportIn
from app.desktop.service import resolve_language, to_steps
from app.diagnosis.anthropic_provider import ProviderError
from app.extension.policy import find_violations
from app.extension.service import ExtensionIdentity
from app.routes.extension_public import authenticate
from app.scanning.sanitize import sanitize_text
from app.security.rate_limit import RateLimiter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/desk/v1", tags=["desktop"])

NOTICE = (
    "Only redacted text from the windows you chose was used. Redaction is pattern-based and can "
    "miss things. Nothing is changed on your computer unless you allow a step."
)
_PROVIDER_ERRORS = {
    "no_api_key": (503, "The AI provider is not configured on this server."),
    "provider_unavailable": (502, "The assistant is temporarily unavailable. Try again later."),
    "provider_timeout": (502, "The assistant took too long to answer. Try again later."),
    "provider_rejected": (502, "The assistant rejected the request."),
    "invalid_output": (
        502,
        "The assistant gave an answer that could not be used. Nothing was shown.",
    ),
}


async def desk_identity(
    request: Request,
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
    limiter: RateLimiter = Depends(get_rate_limiter),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> ExtensionIdentity:
    return await authenticate(
        request, engine, limiter, "desktop", settings.desk_requests_per_minute
    )


def get_provider(request: Request) -> DeskProvider:
    provider: DeskProvider | None = getattr(request.app.state, "desk_provider", None)
    if provider is None:
        raise HTTPException(503, "The assistant is not available.")
    return provider


def _label(provider: DeskProvider) -> str:
    return "Built-in checks (not AI)" if provider.name == "rules" else f"AI model {provider.model}"


async def _used_today(db: AsyncSession, org_id: uuid.UUID) -> int:
    since = datetime.now(UTC) - timedelta(hours=24)
    count = await db.scalar(
        select(func.count())
        .select_from(AuditEvent)
        .where(
            AuditEvent.org_id == org_id,
            AuditEvent.action == "desk.chat",
            AuditEvent.created_at >= since,
        )
    )
    return int(count or 0)


@router.get("/me", response_model=MeOut)
async def me(
    who: ExtensionIdentity = Depends(desk_identity),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
    provider: DeskProvider = Depends(get_provider),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> MeOut:
    async with tenant_session(engine, user_id=who.user_id, org_id=who.org_id) as db:
        org = await db.scalar(select(Organization.name).where(Organization.id == who.org_id))
        user = await db.scalar(select(User.display_name).where(User.id == who.user_id))
        used = await _used_today(db, who.org_id)
    return MeOut(
        org_name=org or "",
        user_name=user or "",
        provider_label=_label(provider),
        is_ai=provider.name != "rules",
        daily_chat_limit=settings.desk_chat_daily_per_org,
        daily_chat_used=used,
    )


async def _reject(
    engine: AsyncEngine, who: ExtensionIdentity, reason: str, details: dict[str, Any]
) -> None:
    async with tenant_session(engine, user_id=who.user_id, org_id=who.org_id) as db:
        await record_audit(
            db,
            action="desk.rejected",
            outcome="denied",
            actor_user_id=who.user_id,
            org_id=who.org_id,
            details={"reason": reason, **details},
        )


@router.post("/chat", response_model=ChatOut)
async def chat(
    body: ChatIn,
    who: ExtensionIdentity = Depends(desk_identity),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
    provider: DeskProvider = Depends(get_provider),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> ChatOut:
    async with tenant_session(engine, user_id=who.user_id, org_id=who.org_id) as db:
        used = await _used_today(db, who.org_id)
    if used >= settings.desk_chat_daily_per_org:
        raise HTTPException(429, "Your organization reached its daily assistant limit.")

    windows = body.look.windows if body.look else []
    for window in windows:
        why = sensitive_window(window.app, window.title)
        if why:
            await _reject(engine, who, "sensitive_window", {"why": why})
            raise HTTPException(422, f"A window was not read: {why}. Nothing was used.")
    payload = body.model_dump(mode="json", exclude={"client"})
    violations = find_violations(payload)
    if violations:
        await _reject(
            engine, who, "redaction_failed", {"fields": [f"{p}: {r}" for p, r in violations[:10]]}
        )
        listed = "; ".join(f"{p} ({r})" for p, r in violations[:5])
        raise HTTPException(
            422,
            f"Rejected: this looks like it still holds personal data or secrets in {listed}. "
            "Nothing was used.",
        )

    language = resolve_language(body.language, body.message)
    try:
        draft, usage = await provider.reply(body, language)
    except ProviderError as exc:
        status, message = _PROVIDER_ERRORS.get(exc.code, (502, "The assistant failed."))
        raise HTTPException(status, message) from exc

    steps = to_steps(draft, body.look)
    async with tenant_session(engine, user_id=who.user_id, org_id=who.org_id) as db:
        db.add_all(
            DeskStep(
                id=s.id, org_id=who.org_id, user_id=who.user_id, token_id=who.token_id, kind=s.kind
            )
            for s in steps
        )
        await record_audit(
            db,
            action="desk.chat",
            outcome="success",
            actor_user_id=who.user_id,
            org_id=who.org_id,
            details={
                "windows": len(windows),
                "nodes": sum(len(w.nodes) for w in windows),
                "steps": len(steps),
                "provider": provider.name,
                "language": language,
                "redacted_counts": body.client.redacted_counts,
                "blocked_windows": body.client.blocked_windows,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
            },
        )
    return ChatOut(
        reply=sanitize_text(draft.reply, 4000),
        language=language,
        steps=steps,
        provider_label=_label(provider),
        is_ai=provider.name != "rules",
        notice=NOTICE,
    )


@router.post("/steps/{step_id}/report", status_code=204)
async def report_step(
    step_id: uuid.UUID,
    body: ReportIn,
    who: ExtensionIdentity = Depends(desk_identity),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> None:
    """The app says how a step ended: once, and only for a step this user was offered."""
    async with tenant_session(engine, user_id=who.user_id, org_id=who.org_id) as db:
        step = await db.scalar(
            select(DeskStep).where(
                DeskStep.id == step_id,
                DeskStep.org_id == who.org_id,
                DeskStep.user_id == who.user_id,
            )
        )
        if step is None:
            raise HTTPException(404, "Step not found.")
        if step.outcome is not None:
            raise HTTPException(409, "This step already has an outcome.")
        await db.execute(
            update(DeskStep)
            .where(DeskStep.id == step_id)
            .values(outcome=body.outcome, reported_at=datetime.now(UTC))
        )
        await record_audit(
            db,
            action=f"desk.step_{body.outcome}",
            outcome="success" if body.outcome == "executed" else "denied",
            actor_user_id=who.user_id,
            org_id=who.org_id,
            target_type="desk_step",
            target_id=step_id,
            details={"kind": step.kind},
        )
