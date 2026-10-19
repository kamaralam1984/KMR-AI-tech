import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.auth.deps import OrgAccess, get_engine, org_access
from app.db.audit import record_audit
from app.db.models import BackendLogEntry, Scan
from app.db.tenant import tenant_session
from app.scanning.backend_logs import MAX_ENTRIES_PER_SCAN, parse_logs
from app.scanning.evidence_loader import load_scan_evidence
from app.schemas import (
    BackendEvidenceOut,
    BackendLogsIn,
    BackendLogsResult,
    CorrelationOut,
    EvidenceRefOut,
    EvidenceSourceOut,
)

router = APIRouter(prefix="/v1/organizations/{org_id}/scans/{scan_id}", tags=["backend-evidence"])


async def _scan(db, scan_id: uuid.UUID, org_id: uuid.UUID) -> Scan:  # type: ignore[no-untyped-def]
    scan = await db.scalar(select(Scan).where(Scan.id == scan_id, Scan.org_id == org_id))
    if scan is None:
        raise HTTPException(404, "Scan not found.")
    return scan  # type: ignore[no-any-return]


@router.post("/backend-logs", response_model=BackendLogsResult)
async def upload_backend_logs(
    scan_id: uuid.UUID,
    body: BackendLogsIn,
    access: OrgAccess = Depends(org_access("member")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> BackendLogsResult:
    parsed = parse_logs(body.content, body.format)
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        await _scan(db, scan_id, access.org_id)
        existing = (
            await db.scalar(
                select(func.count())
                .select_from(BackendLogEntry)
                .where(BackendLogEntry.scan_id == scan_id)
            )
            or 0
        )
        room = max(0, MAX_ENTRIES_PER_SCAN - existing)
        accepted = parsed.entries[:room]
        rejected = parsed.rejected + (len(parsed.entries) - len(accepted))
        for entry in accepted:
            db.add(
                BackendLogEntry(
                    org_id=access.org_id,
                    scan_id=scan_id,
                    ts=entry.ts,
                    level=entry.level,
                    request_id=entry.request_id,
                    http_status=entry.http_status,
                    path=entry.path,
                    message=entry.message,
                )
            )
        await record_audit(
            db,
            action="backend_logs.upload",
            outcome="success",
            actor_user_id=access.user.user_id,
            org_id=access.org_id,
            target_type="scan",
            target_id=scan_id,
            details={"accepted": len(accepted), "rejected": rejected, "format": body.format},
        )
        return BackendLogsResult(
            accepted=len(accepted), rejected=rejected, total_for_scan=existing + len(accepted)
        )


@router.delete("/backend-logs", status_code=204)
async def delete_backend_logs(
    scan_id: uuid.UUID,
    access: OrgAccess = Depends(org_access("member")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> Response:
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        await _scan(db, scan_id, access.org_id)
        await db.execute(delete(BackendLogEntry).where(BackendLogEntry.scan_id == scan_id))
        await record_audit(
            db,
            action="backend_logs.delete",
            outcome="success",
            actor_user_id=access.user.user_id,
            org_id=access.org_id,
            target_type="scan",
            target_id=scan_id,
        )
    return Response(status_code=204)


@router.get("/backend-evidence", response_model=BackendEvidenceOut)
async def backend_evidence(
    scan_id: uuid.UUID,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    access: OrgAccess = Depends(org_access("viewer")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> BackendEvidenceOut:
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        loaded = await load_scan_evidence(db, scan_id, access.org_id)
    runs, logs, configured = loaded.runs, loaded.logs, loaded.configured_checks
    correlations = loaded.correlations

    sources = [
        EvidenceSourceOut(
            key="browser",
            label="Browser (observed)",
            state="available",
            detail="What the scanner's browser saw while loading your pages.",
        ),
        EvidenceSourceOut(
            key="api_checks",
            label="API checks (observed by KMR AI)",
            state="available" if runs else ("no_data" if configured else "not_connected"),
            detail=(
                f"{len(runs)} check run(s) in this scan."
                if runs
                else "Checks are set up but none ran in this scan."
                if configured
                else "No API checks are set up for this project."
            ),
        ),
        EvidenceSourceOut(
            key="customer_logs",
            label="Backend logs (supplied by you)",
            state="available" if logs else "not_connected",
            detail=(
                f"{len(logs)} log entries uploaded."
                if logs
                else "No backend logs were uploaded for this scan."
            ),
        ),
        EvidenceSourceOut(
            key="monitoring",
            label="Monitoring (Sentry, Datadog, OpenTelemetry)",
            state="not_connected",
            detail="Monitoring connectors are not available yet.",
        ),
        EvidenceSourceOut(
            key="repository",
            label="Source code repository",
            state="not_connected",
            detail="No repository is connected, so nothing is derived from your code.",
        ),
    ]
    available = bool(runs or logs)
    notice = (
        None
        if available
        else (
            "Backend telemetry is unavailable for this scan: no API checks ran and no backend logs "
            "were uploaded. Browser failures below cannot be tied to the backend."
        )
    )
    return BackendEvidenceOut(
        backend_telemetry_available=available,
        notice=notice,
        sources=sources,
        correlations=[
            CorrelationOut(
                observation_id=uuid.UUID(c.observation_id),
                kind=c.kind,
                page_url=c.page_url,
                request_url=c.request_url,
                http_status=c.http_status,
                request_id=c.request_id,
                classification=c.classification,
                needs_review=c.needs_review,
                statement=c.statement,
                sources=c.sources,
                evidence=[
                    EvidenceRefOut(
                        source=e.source,
                        ref_type=e.ref_type,
                        ref_id=uuid.UUID(e.ref_id),
                        summary=e.summary,
                    )
                    for e in c.evidence
                ],
            )
            for c in correlations[:limit]
        ],
    )
