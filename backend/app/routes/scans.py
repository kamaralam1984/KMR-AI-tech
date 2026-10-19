import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.auth.deps import OrgAccess, get_engine, org_access
from app.config import Settings, get_settings
from app.db.audit import record_audit
from app.db.models import Evidence, Observation, Project, Scan
from app.db.tenant import tenant_session
from app.scanning.profiles import (
    DEFAULT_ENGINE,
    DEFAULT_PROFILE,
    ENGINES,
    PROFILES,
    describe,
    engine_supports,
)
from app.scanning.queue import QueueUnavailable, ScanQueue
from app.scanning.storage import EvidenceStore
from app.schemas import ObservationOut, ObservationPage, ScanDetailOut, ScanOut

router = APIRouter(prefix="/v1/organizations/{org_id}", tags=["scans"])

ACTIVE = ("queued", "running")


def _scan_out(scan: Scan, project_name: str | None = None) -> ScanOut:
    return ScanOut(
        id=scan.id,
        project_id=scan.project_id,
        project_name=project_name,
        status=scan.status,
        site_url=scan.site_url,
        created_at=scan.created_at,
        started_at=scan.started_at,
        finished_at=scan.finished_at,
        error_code=scan.error_code,
        stopped_reason=scan.stopped_reason,
        cancel_requested=scan.cancel_requested_at is not None,
        summary=scan.summary or {},
        limits=scan.limits or {},
    )


def get_queue(request: Request) -> ScanQueue | None:
    queue: ScanQueue | None = getattr(request.app.state, "scan_queue", None)
    return queue


def get_store(request: Request) -> EvidenceStore:
    store: EvidenceStore | None = getattr(request.app.state, "evidence_store", None)
    if store is None:
        raise HTTPException(503, "Evidence storage is not available.")
    return store


@router.get("/scan-options")
async def scan_options(
    access: OrgAccess = Depends(org_access("viewer")),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> dict[str, object]:
    """What a scan can be asked to run as on this server: engines and profiles, honestly."""
    engines = ["chromium", *[e for e in settings.scan_engines_available if e != "chromium"]]
    return {
        "engines": engines,
        "profiles": [
            {
                "key": p.key,
                "label": p.label,
                "weight": p.weight,
                "throttled": p.throttled,
                "note": p.description,
            }
            for p in PROFILES.values()
        ],
    }


@router.post("/projects/{project_id}/scans", status_code=202, response_model=ScanOut)
async def create_scan(
    project_id: uuid.UUID,
    response: Response,
    idempotency_key: Annotated[str | None, Header(max_length=200)] = None,
    profile: Annotated[str, Query(max_length=30)] = DEFAULT_PROFILE,
    engine_name: Annotated[str, Query(alias="engine", max_length=20)] = DEFAULT_ENGINE,
    access: OrgAccess = Depends(org_access("member")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
    queue: ScanQueue | None = Depends(get_queue),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> ScanOut:
    if profile not in PROFILES:
        raise HTTPException(422, f"Unknown scan profile. Choose one of: {', '.join(PROFILES)}.")
    if engine_name not in ENGINES:
        raise HTTPException(422, f"Unknown browser engine. Choose one of: {', '.join(ENGINES)}.")
    if engine_name not in settings.scan_engines_available:
        raise HTTPException(
            409,
            f"The {engine_name} browser is not available on this server. "
            f"Available: {', '.join(settings.scan_engines_available)}.",
        )
    chosen = PROFILES[profile]
    unsupported = engine_supports(chosen, engine_name)
    if unsupported:
        raise HTTPException(422, unsupported)
    try:
        async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
            project = await db.scalar(
                select(Project).where(Project.id == project_id, Project.org_id == access.org_id)
            )
            if project is None:
                raise HTTPException(404, "Project not found.")

            if idempotency_key:
                existing = await db.scalar(
                    select(Scan).where(
                        Scan.project_id == project_id, Scan.idempotency_key == idempotency_key
                    )
                )
                if existing is not None:
                    response.status_code = 200  # same request again: return the first result
                    return _scan_out(existing, project.name)

            if not project.site_url or not project.site_host:
                raise HTTPException(409, "Set the website address before scanning.")
            if not project.is_verified:
                raise HTTPException(409, "Verify that you own this website before scanning.")

            since = datetime.now(UTC) - timedelta(hours=24)
            # Slower profiles keep the browser busy longer, so they use more of the daily quota.
            used = await db.scalars(
                select(Scan.limits).where(Scan.org_id == access.org_id, Scan.created_at >= since)
            )
            spent = sum(int((lim or {}).get("profile", {}).get("quota_weight", 1)) for lim in used)
            if spent + chosen.weight > settings.scan_daily_quota_per_org:
                raise HTTPException(
                    429,
                    "Your organization reached its daily scan limit. "
                    f"This profile counts as {chosen.weight} scan(s).",
                )

            scan = Scan(
                org_id=access.org_id,
                project_id=project_id,
                requested_by=access.user.user_id,
                site_url=project.site_url,
                allowed_host=project.site_host,
                idempotency_key=idempotency_key,
                limits={
                    "max_pages": min(project.scan_max_pages, settings.scan_max_pages_cap),
                    "max_requests": settings.scan_max_requests,
                    "max_bytes": settings.scan_max_bytes,
                    "timeout_seconds": settings.scan_timeout_seconds,
                    "page_timeout_seconds": settings.scan_page_timeout_seconds,
                    "exclusions": list(project.scan_exclusions or []),
                    "inject_request_id": project.inject_request_id,
                    "profile": describe(chosen, engine_name),
                },
            )
            db.add(scan)
            await db.flush()
            await db.refresh(scan)
            await record_audit(
                db,
                action="scan.create",
                outcome="success",
                actor_user_id=access.user.user_id,
                org_id=access.org_id,
                target_type="scan",
                target_id=scan.id,
                details={"host": project.site_host},
            )
            result = _scan_out(scan, project.name)
    except IntegrityError as exc:
        # The database allows one queued-or-running scan per project, even under a race.
        raise HTTPException(409, "A scan is already queued or running for this project.") from exc

    if queue is not None:
        try:
            await queue.enqueue(result.id)
        except QueueUnavailable:
            # The scan stays queued; the worker's recovery pass re-sends stale queued scans.
            pass
    return result


@router.get("/scans", response_model=list[ScanOut])
async def list_scans(
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
    access: OrgAccess = Depends(org_access("viewer")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> list[ScanOut]:
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        rows = (
            await db.execute(
                select(Scan, Project.name)
                .join(Project, (Project.id == Scan.project_id) & (Project.org_id == Scan.org_id))
                .where(Scan.org_id == access.org_id)
                .order_by(Scan.created_at.desc(), Scan.id)
                .limit(limit)
                .offset(offset)
            )
        ).all()
    return [_scan_out(scan, name) for scan, name in rows]


@router.get("/projects/{project_id}/scans", response_model=list[ScanOut])
async def list_project_scans(
    project_id: uuid.UUID,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    access: OrgAccess = Depends(org_access("viewer")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> list[ScanOut]:
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        project = await db.scalar(
            select(Project).where(Project.id == project_id, Project.org_id == access.org_id)
        )
        if project is None:
            raise HTTPException(404, "Project not found.")
        scans = await db.scalars(
            select(Scan)
            .where(Scan.project_id == project_id, Scan.org_id == access.org_id)
            .order_by(Scan.created_at.desc(), Scan.id)
            .limit(limit)
        )
        return [_scan_out(s, project.name) for s in scans]


async def _load_scan(db, scan_id: uuid.UUID, org_id: uuid.UUID) -> tuple[Scan, str]:  # type: ignore[no-untyped-def]
    row = (
        await db.execute(
            select(Scan, Project.name)
            .join(Project, (Project.id == Scan.project_id) & (Project.org_id == Scan.org_id))
            .where(Scan.id == scan_id, Scan.org_id == org_id)
        )
    ).first()
    if row is None:
        raise HTTPException(404, "Scan not found.")
    return row[0], row[1]


@router.get("/scans/{scan_id}", response_model=ScanDetailOut)
async def get_scan(
    scan_id: uuid.UUID,
    access: OrgAccess = Depends(org_access("viewer")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> ScanDetailOut:
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        scan, project_name = await _load_scan(db, scan_id, access.org_id)
        grouped = (
            await db.execute(
                select(Observation.kind, Observation.severity, func.count())
                .where(Observation.scan_id == scan_id, Observation.org_id == access.org_id)
                .group_by(Observation.kind, Observation.severity)
            )
        ).all()
    counts: dict[str, int] = {}
    severity_counts: dict[str, int] = {}
    for kind, severity, number in grouped:
        counts[kind] = counts.get(kind, 0) + number
        severity_counts[severity] = severity_counts.get(severity, 0) + number
    base = _scan_out(scan, project_name)
    return ScanDetailOut(**base.model_dump(), counts=counts, severity_counts=severity_counts)


@router.get("/scans/{scan_id}/observations", response_model=ObservationPage)
async def list_observations(
    scan_id: uuid.UUID,
    kind: Annotated[str | None, Query(max_length=40)] = None,
    severity: Annotated[str | None, Query(max_length=10)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
    access: OrgAccess = Depends(org_access("viewer")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> ObservationPage:
    filters = [Observation.scan_id == scan_id, Observation.org_id == access.org_id]
    if kind:
        filters.append(Observation.kind == kind)
    if severity:
        filters.append(Observation.severity == severity)
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        await _load_scan(db, scan_id, access.org_id)  # 404 if the scan is not in this org
        total = await db.scalar(select(func.count()).select_from(Observation).where(*filters))
        rows = await db.scalars(
            select(Observation)
            .where(*filters)
            .order_by(Observation.created_at, Observation.id)
            .limit(limit)
            .offset(offset)
        )
        items = [
            ObservationOut(
                id=o.id,
                kind=o.kind,
                severity=o.severity,
                page_url=o.page_url,
                data=o.data or {},
                evidence_id=o.evidence_id,
                created_at=o.created_at,
            )
            for o in rows
        ]
    return ObservationPage(items=items, total=total or 0)


@router.get("/scans/{scan_id}/evidence/{evidence_id}")
async def get_evidence(
    scan_id: uuid.UUID,
    evidence_id: uuid.UUID,
    access: OrgAccess = Depends(org_access("viewer")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
    store: EvidenceStore = Depends(get_store),  # noqa: B008
) -> Response:
    """Screenshots are served only through this tenant-checked route, never as static files."""
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        evidence = await db.scalar(
            select(Evidence).where(
                Evidence.id == evidence_id,
                Evidence.scan_id == scan_id,
                Evidence.org_id == access.org_id,
            )
        )
        if evidence is None:
            raise HTTPException(404, "Evidence not found.")
        key = evidence.storage_key
    try:
        data = store.read(key)
    except ValueError:
        data = None
    if data is None:
        raise HTTPException(404, "Evidence not found.")
    return Response(
        content=data,
        media_type="image/png",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "inline",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )


@router.post("/scans/{scan_id}/cancel", response_model=ScanOut)
async def cancel_scan(
    scan_id: uuid.UUID,
    access: OrgAccess = Depends(org_access("member")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> ScanOut:
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        scan, project_name = await _load_scan(db, scan_id, access.org_id)
        if scan.status not in ACTIVE:
            raise HTTPException(409, "Only a queued or running scan can be cancelled.")
        if scan.status == "queued":
            await db.execute(
                update(Scan)
                .where(Scan.id == scan_id, Scan.status == "queued")
                .values(
                    status="cancelled",
                    cancel_requested_at=func.now(),
                    finished_at=func.now(),
                    stopped_reason="cancelled",
                )
            )
        else:  # running: the worker notices this flag between pages and stops
            await db.execute(
                update(Scan).where(Scan.id == scan_id).values(cancel_requested_at=func.now())
            )
        await record_audit(
            db,
            action="scan.cancel",
            outcome="success",
            actor_user_id=access.user.user_id,
            org_id=access.org_id,
            target_type="scan",
            target_id=scan_id,
            details={"was": scan.status},
        )
        scan, project_name = await _load_scan(db, scan_id, access.org_id)
        await db.refresh(scan)
        return _scan_out(scan, project_name)


# ------------------------------------------------------------------- profile comparison


def _path(url: str | None) -> str:
    return (urlsplit(url).path or "/") if url else "(no page)"


def _signature(o: Observation) -> str:
    data = o.data or {}
    detail = data.get("check") or data.get("status") or ""
    text = str(data.get("message") or data.get("error") or "")[:60]
    return f"{o.kind}|{_path(o.page_url)}|{detail}|{text}"


async def _scan_view(db, org_id: uuid.UUID, scan_id: uuid.UUID):  # type: ignore[no-untyped-def]
    scan = await db.scalar(select(Scan).where(Scan.id == scan_id, Scan.org_id == org_id))
    if scan is None:
        raise HTTPException(404, "Scan not found.")
    if scan.status != "completed":
        raise HTTPException(409, "Only completed scans can be compared.")
    rows = (
        await db.scalars(
            select(Observation).where(Observation.scan_id == scan_id, Observation.org_id == org_id)
        )
    ).all()
    findings: dict[str, dict[str, str]] = {}
    load_ms: dict[str, int] = {}
    for o in rows:
        if o.kind == "page":
            value = (o.data or {}).get("load_ms")
            if isinstance(value, int | float):
                load_ms[_path(o.page_url)] = round(value)
        if o.severity in ("warning", "error", "critical"):
            findings.setdefault(
                _signature(o),
                {"kind": o.kind, "page": _path(o.page_url), "severity": o.severity},
            )
    profile = (scan.limits or {}).get("profile") or {"key": DEFAULT_PROFILE, "label": "Desktop"}
    return scan, profile, findings, load_ms


@router.get("/scans/{scan_id}/compare/{other_id}")
async def compare_scans(
    scan_id: uuid.UUID,
    other_id: uuid.UUID,
    access: OrgAccess = Depends(org_access("viewer")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> dict[str, object]:
    """Which findings appear under only one of two scans of the same project.

    Works on stored observations, so it needs no AI run. Load times are values measured by the
    scanner under each scan's own throttling; they are not real-visitor numbers.
    """
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        a, a_profile, a_findings, a_load = await _scan_view(db, access.org_id, scan_id)
        b, b_profile, b_findings, b_load = await _scan_view(db, access.org_id, other_id)
    if a.project_id != b.project_id:
        raise HTTPException(409, "Only scans of the same project can be compared.")

    def pick(source: dict[str, dict[str, str]], keys: set[str]) -> list[dict[str, str]]:
        return [source[k] for k in sorted(keys)]

    a_keys, b_keys = set(a_findings), set(b_findings)
    paths = sorted(set(a_load) | set(b_load))
    return {
        "basis": "Findings are matched on kind, page path and detail. Load times are measured by "
        "the scanner under each scan's own conditions, not by real visitors.",
        "first": {"scan_id": str(a.id), "profile": a_profile, "findings": len(a_findings)},
        "second": {"scan_id": str(b.id), "profile": b_profile, "findings": len(b_findings)},
        "only_in_first": pick(a_findings, a_keys - b_keys),
        "only_in_second": pick(b_findings, b_keys - a_keys),
        "in_both": len(a_keys & b_keys),
        "load_ms": [{"page": p, "first": a_load.get(p), "second": b_load.get(p)} for p in paths],
    }
