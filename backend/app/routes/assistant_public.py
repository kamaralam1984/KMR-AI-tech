"""Visitor-facing assistant API (no account; authenticated by an install key and its origin list).

Rules that every handler here follows:
- Visitor text is untrusted data. It is searched or computed on, never obeyed.
- Nothing a visitor types is stored, except a hand-off the visitor has previewed and consented to.
- Request bodies are never logged (the access log records only method, path and status).
- Answers are quoted from the tenant's own content or computed exactly. No AI model is called, and
  responses say so.
"""

import hmac
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.assistant import forms as form_logic
from app.assistant import learning, readiness
from app.assistant.privacy import content_terms, redact_personal
from app.assistant.retrieval import rank
from app.assistant.schemas import (
    AskIn,
    ConfirmIn,
    ConfirmVerifyIn,
    FormValues,
    GuideOut,
    HandoffPreviewIn,
    HandoffSubmitIn,
    LearningIn,
    PassageOut,
)
from app.assistant.service import (
    Visitor,
    candidates,
    expiry,
    parse_consent,
    resolve_key,
    sign,
)
from app.auth.deps import allow, client_ip, digest, get_engine, get_rate_limiter, secret_key
from app.config import Settings, get_settings
from app.db.models import FormDefinitionRow, Handoff, LearningConsent
from app.db.tenant import tenant_session
from app.security.rate_limit import RateLimiter

router = APIRouter(prefix="/public/v1", tags=["assistant-public"])

NO_AI = "Answers are quoted from this website's own content. No AI model is used."
HANDOFF_NOTICE = (
    "Only the text shown below is sent to this website's support team. Phone numbers, emails, "
    "card-like numbers and ID numbers are masked, but names and addresses cannot be detected "
    "automatically, so please read it and remove anything you do not want to share."
)
CONFIRM_PHRASES = {
    "declaration": "I CONFIRM THIS DECLARATION",
    "submission": "I CONFIRM TO SUBMIT",
    "payment": "I CONFIRM THE PAYMENT",
}


async def visitor(
    request: Request,
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
    limiter: RateLimiter = Depends(get_rate_limiter),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> Visitor:
    key = request.headers.get("x-kmr-install-key", "")
    origin = (request.headers.get("origin") or "").rstrip("/").lower()
    if not key or len(key) > 200:
        raise HTTPException(401, "An install key is required.")
    if not origin:
        raise HTTPException(403, "This assistant only works inside a web page.")
    resolved = await resolve_key(engine, key)
    if resolved is None:
        raise HTTPException(401, "The install key is not valid.")
    found, enabled = resolved
    if not enabled:
        raise HTTPException(403, "This assistant is turned off.")
    if origin not in found.allowed_origins:
        raise HTTPException(403, "This website is not allowed to use this assistant.")
    request.state.cors_origin = origin  # only now may the browser read the answer

    install_tag = f"assistant:{found.install_id}"
    if not await allow(
        limiter, install_tag, settings.assistant_requests_per_minute_per_install, 60
    ):
        raise HTTPException(429, "This assistant is busy. Please try again in a minute.")
    visitor_tag = f"{install_tag}:{digest(client_ip(request))}"
    if not await allow(
        limiter, visitor_tag, settings.assistant_requests_per_minute_per_visitor, 60
    ):
        raise HTTPException(429, "You are sending requests too fast. Please wait a minute.")
    return found


def _need(v: Visitor, module: str) -> None:
    if module not in v.modules:
        raise HTTPException(404, "This feature is not turned on for this website.")


@router.get("/config")
async def config(
    v: Visitor = Depends(visitor),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> dict[str, object]:
    forms: list[dict[str, str]] = []
    if "forms" in v.modules:
        async with tenant_session(engine, org_id=v.org_id) as db:
            rows = await db.scalars(
                select(FormDefinitionRow).where(
                    FormDefinitionRow.install_id == v.install_id, FormDefinitionRow.enabled
                )
            )
            forms = [{"id": str(f.id), "name": f.name, "category": f.category} for f in rows]
    return {
        "name": v.name,
        "modules": list(v.modules),
        "notice": NO_AI,
        "consent_text": v.consent_text,
        "forms": forms,
        "supported_form_categories": list(form_logic.SUPPORTED_CATEGORIES),
        "learning": {
            "age_bands": list(learning.AGE_BANDS),
            "languages": list(learning.LANGS),
            "image_input": False,
            "production_ready": readiness.status().production_ready,
            "readiness_blockers": readiness.status().blockers,
            "consent_required": v.consent_required,
            "status": readiness.status().message,
        }
        if "learning" in v.modules
        else None,
    }


# ----------------------------------------------------------------------------- 11A guide


@router.post("/guide/ask", response_model=GuideOut)
async def guide_ask(
    body: AskIn,
    v: Visitor = Depends(visitor),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> GuideOut:
    _need(v, "guide")
    question = redact_personal(body.question, 300)
    terms = content_terms(question)
    async with tenant_session(engine, org_id=v.org_id) as db:
        cands = await candidates(db, install_id=v.install_id, kind="site", terms=terms)
    passages = rank(question, cands)
    if not passages:
        return GuideOut(
            found=False,
            answer=None,
            passages=[],
            message="I could not find this on this website, so I will not guess. "
            "Try different words, or contact the website's support.",
        )
    return GuideOut(
        found=True,
        answer=passages[0].text,
        passages=[
            PassageOut(text=p.text, title=p.title, source_url=p.url, match=p.coverage)
            for p in passages
        ],
        message="This is quoted from the page(s) listed as sources.",
    )


# ----------------------------------------------------------------------------- 11B forms


async def _form(engine: AsyncEngine, v: Visitor, form_id: uuid.UUID) -> form_logic.FormDefinition:
    async with tenant_session(engine, org_id=v.org_id) as db:
        row = await db.scalar(
            select(FormDefinitionRow).where(
                FormDefinitionRow.id == form_id,
                FormDefinitionRow.install_id == v.install_id,
                FormDefinitionRow.enabled,
            )
        )
    if row is None:
        raise HTTPException(404, "Form not found.")
    return form_logic.FormDefinition.model_validate(row.definition)


@router.get("/forms/{form_id}")
async def form_definition(
    form_id: uuid.UUID,
    v: Visitor = Depends(visitor),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> dict[str, object]:
    _need(v, "forms")
    defn = await _form(engine, v, form_id)
    return {
        **defn.model_dump(mode="json"),
        "notice": "I only help you check and understand this form. I never submit it for you.",
        "scope": form_logic.EXCLUDED_NOTE,
    }


@router.post("/forms/{form_id}/check")
async def form_check(
    form_id: uuid.UUID,
    body: FormValues,
    v: Visitor = Depends(visitor),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> dict[str, object]:
    """Stateless: the values are checked and thrown away."""
    _need(v, "forms")
    defn = await _form(engine, v, form_id)
    return form_logic.review(defn, body.values)


@router.post("/forms/{form_id}/confirm")
async def form_confirm(
    form_id: uuid.UUID,
    body: ConfirmIn,
    v: Visitor = Depends(visitor),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> dict[str, object]:
    """Issues a short-lived token proving the visitor confirmed ONE action on THIS exact data.

    The assistant itself never performs the action. The website decides what to do with the token
    and must verify it for the same action and data hash.
    """
    _need(v, "forms")
    defn = await _form(engine, v, form_id)
    if body.phrase.strip().upper() != CONFIRM_PHRASES[body.action]:
        raise HTTPException(422, f"To confirm, type exactly: {CONFIRM_PHRASES[body.action]}")
    result = form_logic.review(defn, body.values)
    if not result["ready_for_review"]:
        raise HTTPException(409, "Fix the missing or invalid fields before confirming anything.")
    if body.action == "payment" and not defn.payment_note:
        raise HTTPException(409, "This form has no payment step to confirm.")
    if body.action == "declaration":
        declarations = {f.key for f in defn.fields if f.type == "declaration"}
        if body.field_key not in declarations:
            raise HTTPException(422, "Name the declaration field you are confirming.")
    elif body.field_key is not None:
        raise HTTPException(422, "field_key applies only to declarations.")
    token = form_logic.make_confirmation(
        secret_key(settings),
        install_id=str(v.install_id),
        form_id=str(form_id),
        action=body.action,
        data_hash=str(result["values_hash"]),
        field_key=body.field_key,
    )
    return {
        "action": body.action,
        "token": token,
        "values_hash": result["values_hash"],
        "expires_in_seconds": form_logic.CONFIRMATION_TTL_SECONDS,
        "performed": False,
        "message": "Your confirmation is recorded in this token only. Nothing has been "
        "submitted, declared or paid by the assistant.",
    }


@router.post("/confirmations/verify")
async def confirmation_verify(
    body: ConfirmVerifyIn,
    v: Visitor = Depends(visitor),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> dict[str, object]:
    _need(v, "forms")
    ok, reason = form_logic.verify_confirmation(
        secret_key(settings),
        body.token,
        install_id=str(v.install_id),
        form_id=str(body.form_id),
        action=body.action,
        data_hash=body.values_hash,
        field_key=body.field_key,
    )
    return {"valid": ok, "reason": reason}


# ----------------------------------------------------------------------------- hand-off


def _handoff_text(summary: str) -> str:
    return redact_personal(summary, 2000)


def _contact(contact: str | None) -> str | None:
    if contact is None or not contact.strip():
        return None
    value = contact.strip()
    ok = form_logic._EMAIL.match(value) or form_logic.normalize_phone(value)
    if not ok:
        raise HTTPException(
            422, "Give an email address or a 10-digit mobile number, or leave it empty."
        )
    return value


@router.post("/handoff/preview")
async def handoff_preview(
    body: HandoffPreviewIn,
    v: Visitor = Depends(visitor),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> dict[str, object]:
    redacted = _handoff_text(body.summary)
    contact = _contact(body.contact)
    token = sign(secret_key(settings), "handoff", str(v.install_id), redacted, contact or "")
    return {
        "text": redacted,
        "contact": contact,
        "token": token,
        "notice": HANDOFF_NOTICE,
        "consent_text": v.consent_text
        or "I agree to send the text above to this website's support team.",
        "retention_days": v.retention_days,
        "sent": False,
    }


@router.post("/handoff", status_code=201)
async def handoff_submit(
    body: HandoffSubmitIn,
    request: Request,
    v: Visitor = Depends(visitor),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
    limiter: RateLimiter = Depends(get_rate_limiter),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> dict[str, object]:
    if not body.consent:
        raise HTTPException(422, "You must agree before anything is sent.")
    contact = _contact(body.contact)
    expected = sign(secret_key(settings), "handoff", str(v.install_id), body.text, contact or "")
    if not _same(expected, body.token) or _handoff_text(body.text) != body.text:
        raise HTTPException(409, "The text changed after you previewed it. Preview it again.")
    if not await allow(
        limiter,
        f"handoff:{v.install_id}:{digest(client_ip(request))}",
        settings.assistant_handoffs_per_hour_per_visitor,
        3600,
    ):
        raise HTTPException(429, "Too many requests to support. Please try again later.")
    now = datetime.now(UTC)
    async with tenant_session(engine, org_id=v.org_id) as db:
        await db.execute(text("SELECT assistant_purge_expired()"))
        handoff = Handoff(
            org_id=v.org_id,
            install_id=v.install_id,
            summary=body.text,
            contact=contact,
            consent_at=now,
            expires_at=expiry(v.retention_days),
        )
        db.add(handoff)
        await db.flush()
    return {"sent": True, "id": str(handoff.id), "retention_days": v.retention_days}


def _same(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)


# ----------------------------------------------------------------------------- 11C learning


async def _check_consent(
    request: Request,
    v: Visitor,
    band: str,
    engine: AsyncEngine,
    settings: Settings,
    language: str,
) -> None:
    """A learner under 18 needs a consent the installer recorded: signed, unexpired, unrevoked,
    for this install, and not younger than the band being used. Nothing about the learner is read
    from or stored by this check."""
    refused = HTTPException(403, learning.consent_message(language))
    token = request.headers.get("x-kmr-consent", "")
    consent_id = parse_consent(secret_key(settings), token, v.install_id) if token else None
    if consent_id is None:
        raise refused
    async with tenant_session(engine, org_id=v.org_id) as db:
        row = await db.scalar(
            select(LearningConsent).where(
                LearningConsent.id == consent_id, LearningConsent.install_id == v.install_id
            )
        )
    if (
        row is None
        or row.revoked_at is not None
        or row.expires_at <= datetime.now(UTC)
        or learning.AGE_BANDS.index(band) > learning.AGE_BANDS.index(row.age_band)
    ):
        raise refused


@router.post("/learning/ask")
async def learning_ask(
    body: LearningIn,
    request: Request,
    v: Visitor = Depends(visitor),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> dict[str, object]:
    _need(v, "learning")
    language = learning.detect_language(body.question) if body.language == "auto" else body.language
    if body.age_band in learning.MINOR_BANDS:
        if v.consent_required:
            await _check_consent(request, v, body.age_band, engine, settings, language)
        elif not body.guardian_confirmed:
            raise HTTPException(403, learning.guardian_message(language))

    # Personal details are masked before anything else looks at the text.
    question = redact_personal(body.question, learning.MAX_QUESTION_CHARS)
    solved = learning.solve(question, band=body.age_band, language=language)
    if solved.kind != "not_math":
        return solved.as_dict()

    terms = content_terms(question)
    async with tenant_session(engine, org_id=v.org_id) as db:
        cands = await candidates(db, install_id=v.install_id, kind="learning", terms=terms)
    passages = rank(question, cands)
    note = learning.text_for(language, "note")
    if not passages:
        return {
            "kind": "not_found",
            "language": language,
            "message": learning.text_for(language, "not_found"),
            "passages": [],
            "notes": [note],
            "confidence": "n/a",
            "data_stored": False,
        }
    return {
        "kind": "content",
        "language": language,
        "message": passages[0].text,
        "passages": [
            {"text": p.text, "title": p.title, "source_url": p.url, "match": p.coverage}
            for p in passages
        ],
        "notes": [note],
        "confidence": "medium",
        "data_stored": False,
    }
