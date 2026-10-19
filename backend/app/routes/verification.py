import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.auth.deps import OrgAccess, get_engine, org_access
from app.config import Settings, get_settings
from app.db.audit import record_audit
from app.db.models import (
    DiagnosisRun,
    Issue,
    Observation,
    RepairApplication,
    RepairPlan,
    Scan,
    VerificationRun,
)
from app.db.tenant import tenant_session
from app.diagnosis.anthropic_provider import ProviderError
from app.diagnosis.pipeline import DiagnosisProvider, run_diagnosis
from app.diagnosis.usage import month_spend_micros, runs_last_day
from app.routes.issues import _PROVIDER_ERRORS, get_provider
from app.schemas import FixVerificationOut, FixVerifyIn
from app.verification import AfterIssue, judge, normalize_url

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/organizations/{org_id}", tags=["verification"])
_VISIT_KINDS = ("page", "api_check")


def _out(run: VerificationRun) -> FixVerificationOut:
    return FixVerificationOut(
        id=run.id,
        issue_id=run.issue_id,
        repair_id=run.repair_id,
        before_scan_id=run.before_scan_id,
        after_scan_id=run.after_scan_id,
        outcome=run.outcome,
        reasons=list(run.reasons),
        details=run.details,
        verified_diff_sha256=run.verified_diff_sha256,
        via_kmr_ai=run.application_id is not None,
        created_at=run.created_at,
    )


async def _diagnosed(db: AsyncSession, scan_id: uuid.UUID) -> bool:
    return bool(
        await db.scalar(
            select(func.count())
            .select_from(DiagnosisRun)
            .where(DiagnosisRun.scan_id == scan_id, DiagnosisRun.status == "completed")
        )
    )


@router.post("/issues/{issue_id}/verify", response_model=FixVerificationOut)
async def verify_fix(
    issue_id: uuid.UUID,
    body: FixVerifyIn,
    access: OrgAccess = Depends(org_access("member")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
    provider: DiagnosisProvider = Depends(get_provider),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> FixVerificationOut:
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        issue = await db.scalar(
            select(Issue).where(Issue.id == issue_id, Issue.org_id == access.org_id)
        )
        if issue is None:
            raise HTTPException(404, "Issue not found.")
        before = await db.scalar(select(Scan).where(Scan.id == issue.scan_id))
        after = await db.scalar(
            select(Scan).where(Scan.id == body.after_scan_id, Scan.org_id == access.org_id)
        )
        if before is None or after is None:
            raise HTTPException(404, "Scan not found.")
        if after.project_id != issue.project_id:
            raise HTTPException(409, "That scan belongs to a different project.")
        if after.id == before.id or after.created_at <= before.created_at:
            raise HTTPException(409, "Choose a scan that was started after the original one.")
        application = await db.scalar(
            select(RepairApplication)
            .join(RepairPlan, RepairPlan.id == RepairApplication.repair_id)
            .where(
                RepairPlan.issue_id == issue_id,
                RepairApplication.status == "applied",
                RepairApplication.org_id == access.org_id,
            )
            .order_by(RepairApplication.created_at.desc())
            .limit(1)
        )
        if application is not None and after.created_at <= application.created_at:
            raise HTTPException(
                409,
                "That scan started before the fix branch was created. Scan again after it ships.",
            )
        needs_diagnosis = after.status == "completed" and not await _diagnosed(db, after.id)
        if needs_diagnosis:
            if await runs_last_day(db, access.org_id) >= settings.ai_daily_runs_per_org:
                raise HTTPException(429, "Your organization reached its daily diagnosis limit.")
            ceiling = settings.ai_monthly_cost_ceiling_usd
            if ceiling > 0 and await month_spend_micros(db, access.org_id) >= ceiling * 1_000_000:
                raise HTTPException(429, "Your organization reached its monthly AI spending limit.")
    if needs_diagnosis:
        try:
            await run_diagnosis(
                engine,
                settings,
                provider,
                org_id=access.org_id,
                user_id=access.user.user_id,
                scan_id=after.id,
            )
        except ProviderError as exc:
            status, message = _PROVIDER_ERRORS.get(exc.code, (502, "Diagnosis failed."))
            raise HTTPException(status, message) from exc

    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        issue = await db.scalar(select(Issue).where(Issue.id == issue_id))
        if issue is None:
            raise HTTPException(404, "Issue not found.")
        before_fps = set(
            await db.scalars(select(Issue.fingerprint).where(Issue.scan_id == issue.scan_id))
        )
        after_rows = (
            await db.scalars(
                select(Issue).where(
                    Issue.scan_id == after.id, Issue.state.in_(("open", "needs_review"))
                )
            )
        ).all()
        evidence_ids = [uuid.UUID(str(e["id"])) for e in issue.evidence if e.get("id")]
        pages: set[str] = set()
        if evidence_ids:
            urls = await db.scalars(
                select(Observation.page_url).where(
                    Observation.id.in_(evidence_ids), Observation.page_url.is_not(None)
                )
            )
            pages = {u for u in urls if u}
        visits: dict[str, int | None] = {}
        for obs in await db.scalars(
            select(Observation).where(
                Observation.scan_id == after.id, Observation.kind.in_(_VISIT_KINDS)
            )
        ):
            if obs.page_url:
                code = obs.data.get("status", obs.data.get("http_status"))
                visits[normalize_url(obs.page_url)] = code if isinstance(code, int) else None
        verdict = judge(
            issue_fingerprint=issue.fingerprint,
            issue_pages=pages,
            before_fingerprints=before_fps,
            after_status=after.status,
            after_pages=visits,
            after_issues=[AfterIssue(i.fingerprint, i.severity, i.title) for i in after_rows],
            after_diagnosed=after.status != "completed" or await _diagnosed(db, after.id),
        )
        details: dict[str, Any] = {
            **verdict.details,
            "before_issue_count": len(before_fps),
            "after_issue_count": len(after_rows),
            "after_scan_pages": after.summary.get("pages_scanned") if after.summary else None,
        }
        run = VerificationRun(
            id=uuid.uuid4(),
            org_id=access.org_id,
            project_id=issue.project_id,
            issue_id=issue_id,
            repair_id=application.repair_id if application else None,
            application_id=application.id if application else None,
            before_scan_id=issue.scan_id,
            after_scan_id=after.id,
            requested_by=access.user.user_id,
            outcome=verdict.outcome,
            reasons=verdict.reasons,
            details=details,
            verified_diff_sha256=application.diff_sha256 if application else None,
        )
        db.add(run)
        await db.execute(update(Issue).where(Issue.id == issue_id).values(state=verdict.outcome))
        await record_audit(
            db,
            action="verification.run",
            outcome="success",
            actor_user_id=access.user.user_id,
            org_id=access.org_id,
            target_type="issue",
            target_id=issue_id,
            details={
                "result": verdict.outcome,
                "before_scan_id": str(issue.scan_id),
                "after_scan_id": str(after.id),
                "diff_sha256": run.verified_diff_sha256,
            },
        )
        await db.flush()
        return _out(run)


@router.get("/issues/{issue_id}/verifications", response_model=list[FixVerificationOut])
async def list_verifications(
    issue_id: uuid.UUID,
    access: OrgAccess = Depends(org_access("viewer")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> list[FixVerificationOut]:
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        if await db.scalar(select(Issue.id).where(Issue.id == issue_id)) is None:
            raise HTTPException(404, "Issue not found.")
        runs = await db.scalars(
            select(VerificationRun)
            .where(VerificationRun.issue_id == issue_id, VerificationRun.org_id == access.org_id)
            .order_by(VerificationRun.created_at.desc())
            .limit(20)
        )
        return [_out(r) for r in runs]
