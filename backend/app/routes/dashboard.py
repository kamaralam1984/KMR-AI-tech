import shutil
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.auth.deps import OrgAccess, get_engine, org_access
from app.config import Settings, get_settings
from app.db.models import (
    CheckRun,
    DiagnosisRun,
    Issue,
    Project,
    RepairApplication,
    RepairPlan,
    Scan,
    VerificationRun,
)
from app.db.tenant import tenant_session
from app.diagnosis.usage import month_runs, month_spend_micros
from app.schemas import DashboardOut, SystemOut, UsageOut

router = APIRouter(prefix="/v1/organizations/{org_id}", tags=["dashboard"])
STALL_SECONDS = 120


async def _grouped(db: AsyncSession, column: Any, org_id: uuid.UUID, *where: Any) -> dict[str, int]:
    rows = await db.execute(select(column, func.count()).where(*where).group_by(column))
    return {str(key): int(count) for key, count in rows.all()}


async def _count(db: AsyncSession, model: Any, *where: Any) -> int:
    return int(await db.scalar(select(func.count()).select_from(model).where(*where)) or 0)


@router.get("/dashboard", response_model=DashboardOut)
async def dashboard(
    access: OrgAccess = Depends(org_access("viewer")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> DashboardOut:
    org = access.org_id
    async with tenant_session(engine, user_id=access.user.user_id, org_id=org) as db:
        scan_states = await _grouped(db, Scan.status, org, Scan.org_id == org)
        last = await db.scalar(
            select(func.max(Scan.finished_at)).where(Scan.org_id == org, Scan.status == "completed")
        )
        issue_states = await _grouped(db, Issue.state, org, Issue.org_id == org)
        open_by_severity = await _grouped(
            db, Issue.severity, org, Issue.org_id == org, Issue.state.in_(("open", "needs_review"))
        )
        plans = await _grouped(db, RepairPlan.status, org, RepairPlan.org_id == org)
        applied = await _count(
            db,
            RepairApplication,
            RepairApplication.org_id == org,
            RepairApplication.status == "applied",
        )
        outcomes = await _grouped(db, VerificationRun.outcome, org, VerificationRun.org_id == org)
        projects = await _count(db, Project, Project.org_id == org)
    return DashboardOut(
        projects=projects,
        scans={
            "total": sum(scan_states.values()),
            "completed": scan_states.get("completed", 0),
            "failed": scan_states.get("failed", 0),
            "in_progress": scan_states.get("queued", 0) + scan_states.get("running", 0),
            "last_completed_at": last.isoformat() if last else None,
        },
        issues={
            "open": issue_states.get("open", 0),
            "needs_review": issue_states.get("needs_review", 0),
            "fixed": issue_states.get("fixed", 0),
            "not_fixed": issue_states.get("not_fixed", 0),
            "stale": issue_states.get("stale", 0),
            "open_by_severity": open_by_severity,
        },
        repairs={
            "awaiting_decision": plans.get("proposed", 0),
            "no_fix": plans.get("no_fix", 0),
            "rejected_by_rules": plans.get("rejected", 0),
            "branches_created": applied,
        },
        verifications={
            "fixed": outcomes.get("fixed", 0),
            "not_fixed": outcomes.get("not_fixed", 0),
            "needs_review": outcomes.get("needs_review", 0),
        },
    )


@router.get("/usage", response_model=UsageOut)
async def usage(
    access: OrgAccess = Depends(org_access("admin")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> UsageOut:
    org = access.org_id
    day = datetime.now(UTC) - timedelta(hours=24)
    hour = datetime.now(UTC) - timedelta(hours=1)
    async with tenant_session(engine, user_id=access.user.user_id, org_id=org) as db:
        scans = await _count(db, Scan, Scan.org_id == org, Scan.created_at >= day)
        diag_day = await _count(
            db, DiagnosisRun, DiagnosisRun.org_id == org, DiagnosisRun.created_at >= day
        )
        diag_month = await month_runs(db, org)
        spend = await month_spend_micros(db, org)
        plans = await _count(db, RepairPlan, RepairPlan.org_id == org, RepairPlan.created_at >= day)
        checks = await _count(db, CheckRun, CheckRun.org_id == org, CheckRun.created_at >= hour)
    priced = bool(settings.ai_input_price_per_mtok or settings.ai_output_price_per_mtok)
    ceiling = settings.ai_monthly_cost_ceiling_usd
    return UsageOut(
        scans_last_24h=scans,
        scans_daily_limit=settings.scan_daily_quota_per_org,
        diagnosis_runs_last_24h=diag_day,
        diagnosis_daily_limit=settings.ai_daily_runs_per_org,
        diagnosis_runs_this_month=diag_month,
        ai_cost_usd_this_month=round(spend / 1_000_000, 4) if priced else None,
        ai_monthly_cost_ceiling_usd=ceiling if ceiling > 0 else None,
        repair_plans_last_24h=plans,
        repair_plans_daily_limit=settings.repair_plans_per_day_per_org,
        check_runs_last_hour=checks,
        check_runs_hourly_limit=settings.check_runs_per_hour,
    )


@router.get("/system", response_model=SystemOut)
async def system(
    request: Request,
    access: OrgAccess = Depends(org_access("admin")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> SystemOut:
    """Worker and queue health for this organization's own scans. No infrastructure details."""
    org = access.org_id
    now = datetime.now(UTC)
    async with tenant_session(engine, user_id=access.user.user_id, org_id=org) as db:
        queued = await _count(db, Scan, Scan.org_id == org, Scan.status == "queued")
        oldest = await db.scalar(
            select(func.min(Scan.queued_at)).where(Scan.org_id == org, Scan.status == "queued")
        )
        running = await _count(db, Scan, Scan.org_id == org, Scan.status == "running")
        stalled = await _count(
            db,
            Scan,
            Scan.org_id == org,
            Scan.status == "running",
            func.coalesce(Scan.heartbeat_at, Scan.started_at)
            < now - timedelta(seconds=STALL_SECONDS),
        )
        last = await db.scalar(select(func.max(Scan.finished_at)).where(Scan.org_id == org))
    return SystemOut(
        database="ok",
        queue="connected" if getattr(request.app.state, "scan_queue", None) else "not_configured",
        scans_queued=queued,
        oldest_queued_seconds=int((now - oldest).total_seconds()) if oldest else None,
        scans_running=running,
        scans_stalled=stalled,
        last_scan_finished_at=last,
        sandbox_docker_cli=shutil.which("docker") is not None,
        scanner_mode=settings.scan_browser_mode,
        ai_provider=settings.ai_provider,
    )
