"""Loads everything known about one scan, scoped to one organization, and correlates it."""

import uuid
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ApiCheck, ApiCheckRun, BackendLogEntry, Observation, Project, Scan
from app.scanning.backend_logs import MAX_ENTRIES_PER_SCAN
from app.scanning.correlate import Correlation, Log, Obs, Run, correlate

# Everything diagnosis needs. The correlation step itself only looks at failing requests, pages
# and API checks and ignores the rest.
LOADED_KINDS = (
    "http_error",
    "failed_request",
    "page",
    "api_check",
    "console_error",
    "page_error",
    "a11y_issue",
)


@dataclass
class ScanEvidence:
    scan: Scan
    project: Project | None
    observations: list[Observation]
    runs: list[tuple[ApiCheckRun, str, str]]  # (run, check name, check path)
    logs: list[BackendLogEntry]
    configured_checks: int
    correlations: list[Correlation]


async def load_scan(db: AsyncSession, scan_id: uuid.UUID, org_id: uuid.UUID) -> Scan:
    scan = await db.scalar(select(Scan).where(Scan.id == scan_id, Scan.org_id == org_id))
    if scan is None:
        raise HTTPException(404, "Scan not found.")
    return scan


async def load_scan_evidence(
    db: AsyncSession, scan_id: uuid.UUID, org_id: uuid.UUID
) -> ScanEvidence:
    scan = await load_scan(db, scan_id, org_id)
    project = await db.scalar(
        select(Project).where(Project.id == scan.project_id, Project.org_id == org_id)
    )
    observations = list(
        (
            await db.scalars(
                select(Observation)
                .where(
                    Observation.scan_id == scan_id,
                    Observation.org_id == org_id,
                    Observation.kind.in_(LOADED_KINDS),
                )
                .order_by(Observation.created_at, Observation.id)
                .limit(1000)
            )
        ).all()
    )
    runs = [
        (run, name, path)
        for run, name, path in (
            await db.execute(
                select(ApiCheckRun, ApiCheck.name, ApiCheck.path)
                .join(
                    ApiCheck,
                    (ApiCheck.id == ApiCheckRun.check_id) & (ApiCheck.org_id == ApiCheckRun.org_id),
                )
                .where(ApiCheckRun.scan_id == scan_id, ApiCheckRun.org_id == org_id)
            )
        ).all()
    ]
    logs = list(
        (
            await db.scalars(
                select(BackendLogEntry)
                .where(BackendLogEntry.scan_id == scan_id, BackendLogEntry.org_id == org_id)
                .limit(MAX_ENTRIES_PER_SCAN)
            )
        ).all()
    )
    configured = (
        await db.scalar(
            select(func.count())
            .select_from(ApiCheck)
            .where(ApiCheck.project_id == scan.project_id, ApiCheck.org_id == org_id)
        )
        or 0
    )
    site_host = scan.allowed_host or ((project.site_host if project else None) or "")
    correlations = correlate(
        [Obs(str(o.id), o.kind, o.severity, o.page_url, o.data or {}) for o in observations],
        [
            Run(str(r.id), name, path, r.status, r.http_status, r.reasons or [])
            for r, name, path in runs
        ],
        [Log(str(e.id), e.request_id, e.http_status, e.level, e.path, e.message) for e in logs],
        site_host=site_host,
    )
    return ScanEvidence(scan, project, observations, runs, logs, configured, correlations)
