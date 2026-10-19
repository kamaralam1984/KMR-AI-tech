"""API used by the Chrome extension (`/ext/v1`). Authenticated by an extension token.

Rules every handler follows:
- A token acts as its user, and the user's current membership and role are checked on every call.
- Only verified projects are accepted, and only for pages on that project's own verified host.
- A payload that looks sensitive, or still carries what looks like personal data, is rejected whole
  and nothing is stored. Rejections report field paths and reasons, never values.
- Nothing here changes a site or a repository. Fixes go through the normal repair and approval
  flow in the web app; the extension only links to it.
"""

import base64
import binascii
import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from app.auth.deps import CurrentUser, OrgAccess, allow, get_engine, get_rate_limiter
from app.config import Settings, get_settings
from app.db.audit import record_audit
from app.db.models import (
    DiagnosisRun,
    Evidence,
    ExtensionToken,
    Issue,
    Observation,
    Project,
    Scan,
)
from app.db.tenant import get_role, has_role, tenant_session
from app.extension.policy import find_violations, sensitive_reason
from app.extension.schemas import (
    A11Y_CHECKS,
    SELECTOR,
    AnalyzeOut,
    EvidenceIn,
    EvidenceOut,
    ExtProjectOut,
    FindingOut,
    FixTarget,
)
from app.extension.service import ExtensionIdentity, resolve_token
from app.routes.issues import diagnose_scan, get_provider
from app.scanning.sanitize import sanitize_text, sanitize_url
from app.scanning.storage import EvidenceStore
from app.security.rate_limit import RateLimiter

router = APIRouter(prefix="/ext/v1", tags=["extension-public"])

MAX_BODY = 1_700_000
MAX_SCREENSHOT = 1_000_000
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
NOTICE = (
    "Page evidence was redacted in your browser and checked again here. Redaction is "
    "pattern-based and can miss things, such as names written in ordinary sentences."
)
# Which fields each kind may keep. Everything else the client sent is dropped.
FIELDS: dict[str, tuple[str, ...]] = {
    "page": ("title", "lang", "h1_count", "alerts", "headings", "load_ms", "viewport_width"),
    "console_error": ("message", "level"),
    "page_error": ("message",),
    "failed_request": ("url", "failure", "method"),
    "http_error": ("url", "status", "resource_type"),
    "a11y_issue": ("check", "count", "detail", "selectors"),
}
INFO_CHECKS = {"missing_viewport_meta", "heading_structure"}


async def authenticate(
    request: Request,
    engine: AsyncEngine,
    limiter: RateLimiter,
    kind: str,
    per_minute: int,
) -> ExtensionIdentity:
    """Bearer token to the user it acts as, for ONE kind of client. Re-checks membership."""
    header = request.headers.get("authorization", "")
    scheme, _, raw = header.partition(" ")
    if scheme.lower() != "bearer" or not raw or len(raw) > 200:
        raise HTTPException(401, "A token is required.")
    found = await resolve_token(engine, raw.strip())
    if found is None or found.kind != kind:
        raise HTTPException(
            401, "The token is not valid, has expired, was revoked or is for another app."
        )
    role = await get_role(engine, found.user_id, found.org_id)
    if role is None or not has_role(role, "member"):
        raise HTTPException(403, "This token's user can no longer work in this organization.")
    if not await allow(limiter, f"{kind}:{found.token_id}", per_minute, 60):
        raise HTTPException(429, "Too many requests. Please wait a minute.")
    async with tenant_session(engine, user_id=found.user_id, org_id=found.org_id) as db:
        await db.execute(
            update(ExtensionToken)
            .where(ExtensionToken.id == found.token_id)
            .values(last_used_at=datetime.now(UTC))
        )
    return found


async def identity(
    request: Request,
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
    limiter: RateLimiter = Depends(get_rate_limiter),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> ExtensionIdentity:
    return await authenticate(
        request, engine, limiter, "extension", settings.ext_requests_per_minute
    )


def _clean_value(value: Any) -> Any:
    if isinstance(value, str):
        return sanitize_text(value, 300)
    if isinstance(value, bool | int | float) or value is None:
        return value
    if isinstance(value, list):
        return [_clean_value(v) for v in value[:12]]
    return None


def _clean_data(kind: str, data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in FIELDS[kind]:
        if key not in data:
            continue
        value = data[key]
        if key in ("url",) and isinstance(value, str):
            out[key] = sanitize_url(value)
        elif key == "selectors":
            out[key] = [s for s in (value or []) if isinstance(s, str) and SELECTOR.match(s)][:5]
        elif key == "check":
            if value in A11Y_CHECKS:
                out[key] = value
        else:
            out[key] = _clean_value(value)
    return out


def _severity(kind: str, data: dict[str, Any]) -> str:
    if kind in ("console_error", "page_error", "failed_request"):
        return "error"
    if kind == "http_error":
        return "error" if int(data.get("status") or 0) >= 500 else "warning"
    if kind == "a11y_issue":
        return "info" if data.get("check") in INFO_CHECKS else "warning"
    return "info"


@router.get("/projects", response_model=list[ExtProjectOut])
async def projects(
    who: ExtensionIdentity = Depends(identity),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> list[ExtProjectOut]:
    """Only verified projects: those are the only sites the extension may report on."""
    async with tenant_session(engine, user_id=who.user_id, org_id=who.org_id) as db:
        rows = (
            await db.scalars(
                select(Project)
                .where(Project.org_id == who.org_id)
                .order_by(Project.name)
                .limit(200)
            )
        ).all()
    return [
        ExtProjectOut(id=p.id, name=p.name, site_host=p.site_host or "", site_url=p.site_url or "")
        for p in rows
        if p.is_verified
    ]


async def _reject(
    engine: AsyncEngine, who: ExtensionIdentity, reason: str, details: dict[str, Any]
) -> None:
    async with tenant_session(engine, user_id=who.user_id, org_id=who.org_id) as db:
        await record_audit(
            db,
            action="extension.rejected",
            outcome="denied",
            actor_user_id=who.user_id,
            org_id=who.org_id,
            details={"reason": reason, **details},
        )


@router.post("/evidence", status_code=201, response_model=EvidenceOut)
async def ingest(
    request: Request,
    body: EvidenceIn,
    who: ExtensionIdentity = Depends(identity),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
    limiter: RateLimiter = Depends(get_rate_limiter),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> EvidenceOut:
    if int(request.headers.get("content-length") or 0) > MAX_BODY:
        raise HTTPException(413, "The page evidence is too large.")
    if not await allow(limiter, f"ext-ingest:{who.token_id}", settings.ext_ingests_per_hour, 3600):
        raise HTTPException(429, "Hourly limit for page uploads reached.")

    if body.client.sensitive_page:
        await _reject(engine, who, "client_reported_sensitive", {})
        raise HTTPException(422, "The extension reported a sensitive page. Nothing was stored.")
    why = sensitive_reason(body.page_url)
    if why:
        await _reject(engine, who, "sensitive_url", {"why": why})
        raise HTTPException(422, f"This page is not read: {why}. Nothing was stored.")

    to_scan = body.model_dump(exclude={"screenshot_png_b64"}, mode="json")
    violations = find_violations(
        {"observations": to_scan["observations"], "page_url": to_scan["page_url"]}
    )
    if violations:
        await _reject(
            engine, who, "redaction_failed", {"fields": [f"{p}: {r}" for p, r in violations[:10]]}
        )
        listed = "; ".join(f"{p} ({r})" for p, r in violations[:5])
        raise HTTPException(
            422,
            "Rejected: this looks like it still holds personal data or secrets in "
            f"{listed}. Nothing was stored.",
        )

    screenshot: bytes | None = None
    if body.screenshot_png_b64:
        try:
            screenshot = base64.b64decode(body.screenshot_png_b64, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(422, "The screenshot is not valid base64.") from None
        if not screenshot.startswith(PNG_MAGIC) or len(screenshot) > MAX_SCREENSHOT:
            raise HTTPException(422, "The screenshot must be a PNG of at most 1 MB.")
        store: EvidenceStore | None = getattr(request.app.state, "evidence_store", None)
        if store is None:
            screenshot = None  # nowhere to keep it: drop it rather than fail the whole upload

    page_url = sanitize_url(body.page_url)
    host = (urlsplit(page_url).hostname or "").lower()
    stored = 0
    async with tenant_session(engine, user_id=who.user_id, org_id=who.org_id) as db:
        project = await db.scalar(
            select(Project).where(Project.id == body.project_id, Project.org_id == who.org_id)
        )
        if project is None:
            raise HTTPException(404, "Project not found.")
        if not project.is_verified:
            raise HTTPException(409, "Verify that you own this website before using the agent.")
        if host != (project.site_host or "").lower():
            raise HTTPException(409, "This page is not on the verified website of that project.")

        since = datetime.now(UTC) - timedelta(hours=24)
        recent = await db.scalar(
            select(func.count())
            .select_from(Scan)
            .where(Scan.org_id == who.org_id, Scan.created_at >= since)
        )
        if (recent or 0) >= settings.scan_daily_quota_per_org:
            raise HTTPException(429, "Your organization reached its daily scan limit.")

        scan = Scan(
            org_id=who.org_id,
            project_id=project.id,
            requested_by=who.user_id,
            status="completed",
            site_url=page_url,
            allowed_host=project.site_host or host,
            limits={
                "source": "extension",
                "profile": {"key": "live_page", "label": "Live page (browser extension)"},
            },
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            summary={
                "source": "extension",
                "pages_scanned": 1,
                "client_version": body.client.version,
                "redaction_version": body.client.redaction_version,
                "redacted_counts": body.client.redacted_counts,
                "masked_regions": body.client.masked_regions,
            },
        )
        db.add(scan)
        await db.flush()

        evidence_id: uuid.UUID | None = None
        for item in body.observations:
            data = _clean_data(item.kind, item.data)
            if item.kind == "a11y_issue" and "check" not in data:
                continue
            obs = Observation(
                org_id=who.org_id,
                scan_id=scan.id,
                kind=item.kind,
                severity=_severity(item.kind, data),
                page_url=sanitize_url(item.page_url) if item.page_url else page_url,
                data={**data, "origin": "browser_extension"},
            )
            if item.kind == "page" and screenshot is not None and evidence_id is None:
                evidence_id = uuid.uuid4()
                key = EvidenceStore.key_for(who.org_id, scan.id, evidence_id)
                await anyio.to_thread.run_sync(
                    request.app.state.evidence_store.save, key, screenshot
                )
                db.add(
                    Evidence(
                        id=evidence_id,
                        org_id=who.org_id,
                        scan_id=scan.id,
                        kind="screenshot",
                        storage_key=key,
                        content_type="image/png",
                        size_bytes=len(screenshot),
                        sha256=hashlib.sha256(screenshot).hexdigest(),
                        page_url=page_url,
                    )
                )
                await db.flush()
                obs.evidence_id = evidence_id
            db.add(obs)
            stored += 1
        await record_audit(
            db,
            action="extension.ingest",
            outcome="success",
            actor_user_id=who.user_id,
            org_id=who.org_id,
            target_type="scan",
            target_id=scan.id,
            details={
                "observations": stored,
                "screenshot": evidence_id is not None,
                "masked_regions": body.client.masked_regions,
                "redacted_counts": body.client.redacted_counts,
            },
        )
    return EvidenceOut(
        scan_id=scan.id,
        observations_stored=stored,
        screenshot_stored=evidence_id is not None,
        notice=NOTICE,
    )


@router.post("/analyze/{scan_id}", response_model=AnalyzeOut)
async def analyze(
    scan_id: uuid.UUID,
    request: Request,
    who: ExtensionIdentity = Depends(identity),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> AnalyzeOut:
    """Diagnoses a scan this token's user uploaded, with the normal pipeline, quotas and labels."""
    async with tenant_session(engine, user_id=who.user_id, org_id=who.org_id) as db:
        scan = await db.scalar(select(Scan).where(Scan.id == scan_id, Scan.org_id == who.org_id))
        if (
            scan is None
            or (scan.limits or {}).get("source") != "extension"
            or scan.requested_by != who.user_id
        ):
            raise HTTPException(404, "Scan not found.")
    role = await get_role(engine, who.user_id, who.org_id)
    if role is None:
        raise HTTPException(403, "This token's user can no longer work in this organization.")
    access = OrgAccess(
        user=CurrentUser(who.user_id, "", "", uuid.UUID(int=0), b""), org_id=who.org_id, role=role
    )
    provider = get_provider(request)
    result = await diagnose_scan(
        scan_id, access=access, engine=engine, provider=provider, settings=settings
    )

    async with tenant_session(engine, user_id=who.user_id, org_id=who.org_id) as db:
        ids = [i.id for i in result.issues]
        issues = (
            (
                await db.scalars(select(Issue).where(Issue.id.in_(ids), Issue.org_id == who.org_id))
            ).all()
            if ids
            else []
        )
        obs_ids: set[uuid.UUID] = set()
        for issue in issues:
            for ev in issue.evidence:
                if ev.get("type") == "observation":
                    try:
                        obs_ids.add(uuid.UUID(str(ev.get("id"))))
                    except ValueError:
                        continue
        selectors: dict[str, list[str]] = {}
        checks: dict[str, str] = {}
        if obs_ids:
            rows = await db.execute(
                select(Observation.id, Observation.data).where(
                    Observation.id.in_(obs_ids), Observation.org_id == who.org_id
                )
            )
            for oid, data in rows:
                found = [s for s in (data or {}).get("selectors", []) if SELECTOR.match(str(s))]
                if found:
                    selectors[str(oid)] = found
                    if (data or {}).get("check") in A11Y_CHECKS:
                        checks[str(oid)] = str(data["check"])
        run = await db.scalar(select(DiagnosisRun).where(DiagnosisRun.id == result.run.id))
    findings = []
    for issue in issues:
        cited = [str(e.get("id")) for e in issue.evidence]
        picked: list[str] = []
        targets: list[FixTarget] = []
        for eid in cited:
            for sel in selectors.get(eid, []):
                if sel not in picked:
                    picked.append(sel)
                    if eid in checks:
                        targets.append(FixTarget(selector=sel, check=checks[eid]))
        findings.append(
            FindingOut(
                issue_id=issue.id,
                title=sanitize_text(issue.title, 200),
                severity=issue.severity,
                category=issue.category,
                confidence=issue.confidence,
                classification=issue.classification,
                summary=sanitize_text(issue.summary, 400),
                observed=[sanitize_text(f.get("text", ""), 300) for f in issue.facts][:6],
                inferred=[sanitize_text(h.get("text", ""), 300) for h in issue.hypotheses][:4],
                unverified=[sanitize_text(u, 300) for u in issue.unverified][:4],
                selectors=picked[:5],
                fix_targets=targets[:5],
                evidence_ids=cited[:10],
                proposed_fix=sanitize_text(issue.proposed_fix, 400),
                is_ai=bool(run and run.provider != "rules"),
                web_path=f"/issues/{issue.id}",
            )
        )
    provider_label = (
        "Built-in rules (not AI)" if provider.name == "rules" else f"AI model {provider.model}"
    )
    return AnalyzeOut(
        scan_id=scan_id,
        provider_label=provider_label,
        is_ai=provider.name != "rules",
        findings=findings,
        notice="These are proposals. Nothing on your site or in your code has been changed.",
    )
