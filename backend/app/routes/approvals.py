import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.auth.deps import OrgAccess, allow, get_engine, get_rate_limiter, org_access
from app.config import Settings, get_settings
from app.db.audit import record_audit
from app.db.models import (
    Approval,
    CheckRun,
    Issue,
    RepairApplication,
    RepairPlan,
)
from app.db.tenant import tenant_session
from app.repair.apply import ApplyBlocked, approval_state, verify_for_apply
from app.repair.binding import ACTION_PUSH_BRANCH, binding_hash, binding_payload
from app.repair.context import export_tree
from app.repair.policy import sha256_text
from app.repair.repo import RepoError
from app.repair.sandbox import overall_status, run_checks
from app.routes.repairs import client_for, connection_for, repo_http_error, token_of
from app.schemas import (
    ApplyIn,
    ApplyOut,
    ApprovalIn,
    ApprovalOut,
    CheckResultOut,
    CheckRunOut,
    RevokeIn,
)
from app.security.rate_limit import RateLimiter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/organizations/{org_id}", tags=["approvals"])


def _check_out(run: CheckRun) -> CheckRunOut:
    return CheckRunOut(
        id=run.id,
        repair_id=run.repair_id,
        overall=run.overall,
        results=[CheckResultOut(**r) for r in run.results],
        diff_sha256=run.diff_sha256,
        base_sha=run.base_sha,
        created_at=run.created_at,
    )


def _approval_out(approval: Approval, plan: RepairPlan) -> ApprovalOut:
    return ApprovalOut(
        id=approval.id,
        repair_id=approval.repair_id,
        decision=approval.decision,
        state=approval_state(approval, plan, datetime.now(UTC)),
        action=approval.action,
        approver_id=approval.approver_id,
        issue_ids=list(approval.issue_ids),
        base_branch=approval.base_branch,
        branch_name=approval.branch_name,
        base_sha=approval.base_sha,
        diff_sha256=approval.diff_sha256,
        bound_hash=approval.bound_hash,
        acknowledged_incomplete_checks=approval.acknowledged_incomplete_checks,
        note=approval.note,
        expires_at=approval.expires_at,
        revoked_at=approval.revoked_at,
        revoked_reason=approval.revoked_reason,
        consumed_at=approval.consumed_at,
        created_at=approval.created_at,
    )


async def _plan(db: AsyncSession, org_id: uuid.UUID, repair_id: uuid.UUID) -> RepairPlan:
    plan = await db.scalar(
        select(RepairPlan).where(RepairPlan.id == repair_id, RepairPlan.org_id == org_id)
    )
    if plan is None:
        raise HTTPException(404, "Repair not found.")
    return plan


async def _latest_check(db: AsyncSession, repair_id: uuid.UUID) -> CheckRun | None:
    return await db.scalar(
        select(CheckRun)
        .where(CheckRun.repair_id == repair_id)
        .order_by(CheckRun.created_at.desc())
        .limit(1)
    )


async def _issue_ids(db: AsyncSession, plan: RepairPlan) -> list[Issue]:
    return list(
        await db.scalars(
            select(Issue).where(Issue.id == plan.issue_id, Issue.org_id == plan.org_id)
        )
    )


@router.post("/repairs/{repair_id}/checks", response_model=CheckRunOut)
async def run_repair_checks(
    repair_id: uuid.UUID,
    request: Request,
    access: OrgAccess = Depends(org_access("member")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
    limiter: RateLimiter = Depends(get_rate_limiter),  # noqa: B008
) -> CheckRunOut:
    if not await allow(limiter, f"checks:{access.org_id}", settings.check_runs_per_hour, 3600):
        raise HTTPException(429, "Too many check runs. Try again later.")
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        plan = await _plan(db, access.org_id, repair_id)
        if plan.status != "proposed":
            raise HTTPException(409, "Checks can only run on a proposed repair.")
        conn = await connection_for(db, access.org_id, plan.project_id)
        if conn is None:
            raise HTTPException(409, "The project has no connected repository.")
        token = token_of(settings, conn)
        repo = client_for(request, settings, conn, token)
        command = conn.check_command
        changes = {c["path"]: c["new_content"] for c in plan.changes}
        base_sha, diff_sha = plan.base_sha, plan.diff_sha256
    try:
        base_files = await export_tree(repo, base_sha)
    except RepoError as exc:
        raise repo_http_error(exc) from exc
    finally:
        aclose: Any = getattr(repo, "aclose", None)
        if aclose is not None:
            await aclose()
    results = await run_checks(settings, base_files, changes, command)
    overall = overall_status(results)
    run = CheckRun(
        id=uuid.uuid4(),
        org_id=access.org_id,
        repair_id=repair_id,
        requested_by=access.user.user_id,
        diff_sha256=diff_sha,
        base_sha=base_sha,
        overall=overall,
        results=[r.as_dict() for r in results],
    )
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        db.add(run)
        await record_audit(
            db,
            action="repair.checks",
            outcome="success" if overall == "passed" else "failure",
            actor_user_id=access.user.user_id,
            org_id=access.org_id,
            target_type="repair_plan",
            target_id=repair_id,
            details={
                "overall": overall,
                "diff_sha256": diff_sha,
                "statuses": {r.name: r.status for r in results},
            },
        )
        await db.flush()
        return _check_out(run)


@router.get("/repairs/{repair_id}/checks", response_model=list[CheckRunOut])
async def list_repair_checks(
    repair_id: uuid.UUID,
    access: OrgAccess = Depends(org_access("viewer")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> list[CheckRunOut]:
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        await _plan(db, access.org_id, repair_id)
        runs = await db.scalars(
            select(CheckRun)
            .where(CheckRun.repair_id == repair_id)
            .order_by(CheckRun.created_at.desc())
            .limit(20)
        )
        return [_check_out(r) for r in runs]


@router.post("/repairs/{repair_id}/approvals", response_model=ApprovalOut)
async def decide(
    repair_id: uuid.UUID,
    body: ApprovalIn,
    access: OrgAccess = Depends(org_access("admin")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> ApprovalOut:
    blocked: tuple[str, str] | None = None
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        plan = await _plan(db, access.org_id, repair_id)
        conn = await connection_for(db, access.org_id, plan.project_id)
        issues = await _issue_ids(db, plan)
        latest = await _latest_check(db, repair_id)
        if plan.status != "proposed":
            blocked = ("plan_not_proposed", "Only a proposed repair can be decided.")
        elif conn is None:
            blocked = ("no_repository", "The project has no connected repository.")
        elif sha256_text(plan.diff) != plan.diff_sha256:
            blocked = ("diff_mismatch", "The stored change does not match its fingerprint.")
        elif body.decision == "approved":
            if not issues or any(i.state != "open" for i in issues):
                blocked = ("issues_not_open", "The issue is no longer open.")
            elif latest is None or latest.diff_sha256 != plan.diff_sha256:
                blocked = ("checks_missing", "Run the checks on this exact change first.")
            elif latest.overall == "failed":
                blocked = ("checks_failed", "The latest checks failed, so it cannot be approved.")
            elif latest.overall != "passed" and not body.acknowledge_incomplete_checks:
                blocked = (
                    "checks_incomplete",
                    "Not all checks ran. Confirm that you accept this to approve anyway.",
                )
        if blocked is not None:
            await record_audit(
                db,
                action="approval.decide",
                outcome="denied",
                actor_user_id=access.user.user_id,
                org_id=access.org_id,
                target_type="repair_plan",
                target_id=repair_id,
                details={"decision": body.decision, "code": blocked[0]},
            )
        else:
            assert conn is not None  # noqa: S101 - narrowed by the checks above
            issue_ids = [str(i.id) for i in issues]
            payload = binding_payload(
                org_id=access.org_id,
                project_id=plan.project_id,
                provider=conn.provider,
                location=conn.location,
                base_branch=plan.base_branch,
                branch_name=plan.branch_name,
                base_sha=plan.base_sha,
                issue_ids=issue_ids,
                diff_sha256=plan.diff_sha256,
                action=ACTION_PUSH_BRANCH,
                approver_id=access.user.user_id,
            )
            now = datetime.now(UTC)
            approval = Approval(
                id=uuid.uuid4(),
                org_id=access.org_id,
                repair_id=repair_id,
                project_id=plan.project_id,
                approver_id=access.user.user_id,
                decision=body.decision,
                action=ACTION_PUSH_BRANCH,
                issue_ids=issue_ids,
                repo_provider=conn.provider,
                repo_location=conn.location,
                base_branch=plan.base_branch,
                branch_name=plan.branch_name,
                base_sha=plan.base_sha,
                diff_sha256=plan.diff_sha256,
                bound_hash=binding_hash(payload),
                check_run_id=latest.id if latest else None,
                acknowledged_incomplete_checks=bool(
                    body.acknowledge_incomplete_checks and latest and latest.overall != "passed"
                ),
                note=body.note.strip(),
                expires_at=now + timedelta(hours=settings.approval_ttl_hours),
                created_at=now,
            )
            db.add(approval)
            await record_audit(
                db,
                action=f"approval.{body.decision}",
                outcome="success",
                actor_user_id=access.user.user_id,
                org_id=access.org_id,
                target_type="repair_plan",
                target_id=repair_id,
                details={
                    "approval_id": str(approval.id),
                    "diff_sha256": plan.diff_sha256,
                    "bound_hash": approval.bound_hash,
                    "checks": latest.overall if latest else None,
                    "acknowledged_incomplete_checks": approval.acknowledged_incomplete_checks,
                    "expires_at": approval.expires_at.isoformat(),
                },
            )
            await db.flush()
            return _approval_out(approval, plan)
    code, message = blocked
    raise HTTPException(409, message)


@router.get("/repairs/{repair_id}/approvals", response_model=list[ApprovalOut])
async def list_approvals(
    repair_id: uuid.UUID,
    access: OrgAccess = Depends(org_access("viewer")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> list[ApprovalOut]:
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        plan = await _plan(db, access.org_id, repair_id)
        rows = await db.scalars(
            select(Approval)
            .where(Approval.repair_id == repair_id)
            .order_by(Approval.created_at.desc())
            .limit(20)
        )
        return [_approval_out(a, plan) for a in rows]


@router.post("/approvals/{approval_id}/revoke", response_model=ApprovalOut)
async def revoke(
    approval_id: uuid.UUID,
    body: RevokeIn,
    access: OrgAccess = Depends(org_access("admin")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> ApprovalOut:
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        approval = await db.scalar(
            select(Approval).where(Approval.id == approval_id, Approval.org_id == access.org_id)
        )
        if approval is None:
            raise HTTPException(404, "Approval not found.")
        plan = await _plan(db, access.org_id, approval.repair_id)
        if approval.decision != "approved" or approval.consumed_at or approval.revoked_at:
            raise HTTPException(409, "This approval can no longer be revoked.")
        approval.revoked_at = datetime.now(UTC)
        approval.revoked_by = access.user.user_id
        approval.revoked_reason = body.reason.strip()[:500] or None
        await record_audit(
            db,
            action="approval.revoke",
            outcome="success",
            actor_user_id=access.user.user_id,
            org_id=access.org_id,
            target_type="approval",
            target_id=approval_id,
            details={"repair_id": str(approval.repair_id)},
        )
        await db.flush()
        return _approval_out(approval, plan)


@router.post("/repairs/{repair_id}/apply", response_model=ApplyOut)
async def apply_repair(
    repair_id: uuid.UUID,
    body: ApplyIn,
    request: Request,
    access: OrgAccess = Depends(org_access("admin")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> ApplyOut:
    """Creates the approved branch. This is the only thing that ever writes to a repository."""
    now = datetime.now(UTC)
    blocked: ApplyBlocked | None = None
    changes: dict[str, str] = {}
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        plan = await _plan(db, access.org_id, repair_id)
        approval = await db.scalar(
            select(Approval).where(
                Approval.id == body.approval_id, Approval.org_id == access.org_id
            )
        )
        if approval is None or approval.repair_id != repair_id:
            raise HTTPException(404, "Approval not found for this repair.")
        conn = await connection_for(db, access.org_id, plan.project_id)
        if conn is None:
            raise HTTPException(409, "The project has no connected repository.")
        issues = await _issue_ids(db, plan)
        latest = await _latest_check(db, repair_id)
        repo = client_for(request, settings, conn, token_of(settings, conn))
        try:
            changes = await verify_for_apply(
                repo=repo,
                plan=plan,
                conn=conn,
                approval=approval,
                issues=issues,
                latest_check=latest,
                now=now,
                settings=settings,
            )
        except ApplyBlocked as exc:
            blocked = exc
            await record_audit(
                db,
                action="repair.apply",
                outcome="denied",
                actor_user_id=access.user.user_id,
                org_id=access.org_id,
                target_type="repair_plan",
                target_id=repair_id,
                details={"approval_id": str(approval.id), "code": exc.code},
            )
        else:
            # Claim the approval before writing anything: a second request cannot reuse it.
            claimed = await db.execute(
                update(Approval)
                .where(
                    Approval.id == approval.id,
                    Approval.consumed_at.is_(None),
                    Approval.revoked_at.is_(None),
                )
                .values(consumed_at=now)
            )
            if claimed.rowcount != 1:  # type: ignore[attr-defined]
                blocked = ApplyBlocked(
                    "approval_used", "This approval was already used or revoked."
                )
        branch, base_sha, diff_sha = plan.branch_name, plan.base_sha, plan.diff_sha256
        issue_title = issues[0].title if issues else "issue"
    if blocked is not None:
        aclose: Any = getattr(repo, "aclose", None)
        if aclose is not None:
            await aclose()
        raise HTTPException(409, blocked.message)
    commit_sha: str | None = None
    error_code: str | None = None
    try:
        commit_sha = await repo.create_branch(
            branch=branch,
            base_sha=base_sha,
            changes=changes,
            message=f'KMR AI: fix "{issue_title[:100]}"\n\nApproved change {diff_sha[:12]}.',
        )
    except RepoError as exc:
        error_code = exc.code
    finally:
        close: Any = getattr(repo, "aclose", None)
        if close is not None:
            await close()
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        if error_code is not None and error_code != "branch_exists":
            # Nothing was written, so the approval is released and can be used again.
            await db.execute(
                update(Approval).where(Approval.id == approval.id).values(consumed_at=None)
            )
        record = RepairApplication(
            id=uuid.uuid4(),
            org_id=access.org_id,
            repair_id=repair_id,
            approval_id=approval.id,
            applied_by=access.user.user_id,
            status="applied" if commit_sha else "failed",
            branch_name=branch,
            commit_sha=commit_sha,
            base_sha=base_sha,
            diff_sha256=diff_sha,
            error_code=error_code,
        )
        db.add(record)
        await record_audit(
            db,
            action="repair.apply",
            outcome="success" if commit_sha else "failure",
            actor_user_id=access.user.user_id,
            org_id=access.org_id,
            target_type="repair_plan",
            target_id=repair_id,
            details={
                "approval_id": str(approval.id),
                "branch": branch,
                "commit": commit_sha,
                "diff_sha256": diff_sha,
                "error": error_code,
            },
        )
        await db.flush()
        message = (
            f"Branch {branch} was created from {base_sha[:8]}. Your default branch was not "
            "changed. Review and merge it yourself, and deploy it yourself."
            if commit_sha
            else (
                "A branch with that name already exists. Nothing was changed."
                if error_code == "branch_exists"
                else "The branch could not be created. Nothing was changed."
            )
        )
        out = ApplyOut(
            id=record.id,
            repair_id=repair_id,
            approval_id=approval.id,
            status=record.status,
            branch_name=branch,
            commit_sha=commit_sha,
            base_sha=base_sha,
            diff_sha256=diff_sha,
            error_code=error_code,
            message=message,
            created_at=record.created_at,
        )
    if not commit_sha:
        raise HTTPException(409 if error_code == "branch_exists" else 502, message)
    return out
