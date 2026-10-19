import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.auth.deps import OrgAccess, get_engine, org_access
from app.config import Settings, get_settings
from app.db.audit import record_audit
from app.db.models import Issue, Project, RepairPlan, RepositoryConnection
from app.db.tenant import tenant_session
from app.diagnosis.anthropic_provider import ProviderError
from app.repair.planner import PlanResult, build_plan
from app.repair.providers import RepairInput, RepairProvider
from app.repair.repo import RepoClient, RepoError, open_repo
from app.schemas import (
    RepairFileOut,
    RepairFindingOut,
    RepairIn,
    RepairOut,
    RepositoryIn,
    RepositoryOut,
)
from app.security.secrets_box import SecretsUnavailable, decrypt_secret, encrypt_secret

logger = logging.getLogger(__name__)
router = APIRouter(tags=["repairs"])

_REPO_STATUS = {
    "local_disabled": 503,
    "repo_unreachable": 502,
    "repo_error": 502,
    "git_timeout": 502,
    "git_failed": 502,
    "repo_not_found": 404,
    "issues_forbidden": 403,
}
_PROVIDER_ERRORS = {
    "no_api_key": (503, "The AI provider is not configured on this server."),
    "provider_unavailable": (502, "The AI service is temporarily unavailable. Try again later."),
    "provider_timeout": (502, "The AI service took too long to answer. Try again later."),
    "provider_rejected": (502, "The AI service rejected the request."),
    "invalid_output": (502, "The AI service returned an answer that could not be used."),
}


def repo_http_error(exc: RepoError) -> HTTPException:
    return HTTPException(_REPO_STATUS.get(exc.code, 400), exc.message)


def get_repair_provider(request: Request) -> RepairProvider:
    provider: RepairProvider | None = getattr(request.app.state, "repair_provider", None)
    if provider is None:
        raise HTTPException(503, "Repair planning is not available.")
    return provider


def client_for(
    request: Request, settings: Settings, conn: RepositoryConnection, token: str | None
) -> RepoClient:
    transport: httpx.AsyncBaseTransport | None = getattr(
        request.app.state, "github_transport", None
    )
    try:
        return open_repo(
            conn.provider,
            conn.location,
            token,
            local_roots=settings.repo_local_roots,
            github_api_url=settings.github_api_url,
            github_transport=transport,
        )
    except RepoError as exc:
        raise repo_http_error(exc) from exc


def _repo_out(conn: RepositoryConnection, head_sha: str | None = None) -> RepositoryOut:
    return RepositoryOut(
        id=conn.id,
        project_id=conn.project_id,
        provider=conn.provider,
        location=conn.location,
        default_branch=conn.default_branch,
        has_token=conn.token_ct is not None,
        check_command=conn.check_command,
        head_sha=head_sha,
        created_at=conn.created_at,
        updated_at=conn.updated_at,
    )


def repair_out(plan: RepairPlan) -> RepairOut:
    return RepairOut(
        id=plan.id,
        issue_id=plan.issue_id,
        project_id=plan.project_id,
        status=plan.status,
        source=plan.source,
        provider=plan.provider,
        model=plan.model,
        is_ai=plan.source == "ai",
        base_branch=plan.base_branch,
        base_sha=plan.base_sha,
        branch_name=plan.branch_name,
        explanation=plan.explanation,
        files=[
            RepairFileOut(
                path=c["path"],
                action=c["action"],
                additions=c["additions"],
                deletions=c["deletions"],
            )
            for c in plan.changes
        ],
        diff=plan.diff,
        diff_sha256=plan.diff_sha256,
        findings=[RepairFindingOut(**f) for f in plan.findings],
        warnings=list(plan.warnings),
        created_at=plan.created_at,
    )


async def _project(db: AsyncSession, org_id: uuid.UUID, project_id: uuid.UUID) -> Project:
    project = await db.scalar(
        select(Project).where(Project.id == project_id, Project.org_id == org_id)
    )
    if project is None:
        raise HTTPException(404, "Project not found.")
    return project


async def connection_for(
    db: AsyncSession, org_id: uuid.UUID, project_id: uuid.UUID
) -> RepositoryConnection | None:
    return await db.scalar(
        select(RepositoryConnection).where(
            RepositoryConnection.project_id == project_id, RepositoryConnection.org_id == org_id
        )
    )


def token_of(settings: Settings, conn: RepositoryConnection) -> str | None:
    if conn.token_ct is None:
        return None
    try:
        return decrypt_secret(settings, conn.token_ct)
    except SecretsUnavailable:
        return None


@router.put(
    "/v1/organizations/{org_id}/projects/{project_id}/repository", response_model=RepositoryOut
)
async def connect_repository(
    project_id: uuid.UUID,
    body: RepositoryIn,
    request: Request,
    access: OrgAccess = Depends(org_access("admin")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> RepositoryOut:
    location = body.location.strip()
    token = body.token.get_secret_value() if body.token else None
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        await _project(db, access.org_id, project_id)
        existing = await connection_for(db, access.org_id, project_id)
        if body.provider == "github" and token is None:
            if existing is None or existing.provider != "github":
                raise HTTPException(422, "A GitHub access token is required.")
            token = token_of(settings, existing)
    # The repository is contacted outside any database transaction. The probe is a transient
    # object: nothing is saved unless the branch resolves.
    probe = RepositoryConnection(provider=body.provider, location=location)
    repo = client_for(request, settings, probe, token)
    failure: RepoError | None = None
    head: str | None = None
    try:
        head = await repo.resolve_branch(body.default_branch)
    except RepoError as exc:
        failure = exc
    finally:
        aclose: Any = getattr(repo, "aclose", None)
        if aclose is not None:
            await aclose()
    token_ct: bytes | None = None
    if failure is None and body.provider == "github":
        try:
            token_ct = encrypt_secret(settings, token or "")
        except SecretsUnavailable as exc:
            raise HTTPException(503, "Secret storage is not configured on this server.") from exc
    command = (
        None
        if body.clear_check_command
        else (body.check_command.strip() if body.check_command else None)
    )
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        audit = {
            "action": "repository.connect",
            "actor_user_id": access.user.user_id,
            "org_id": access.org_id,
            "target_type": "project",
            "target_id": project_id,
        }
        if failure is not None:
            await record_audit(
                db,
                outcome="failure",
                details={"provider": body.provider, "code": failure.code},
                **audit,  # type: ignore[arg-type]
            )
            conn = None
        else:
            conn = await connection_for(db, access.org_id, project_id)
            if conn is None:
                conn = RepositoryConnection(
                    org_id=access.org_id,
                    project_id=project_id,
                    created_by=access.user.user_id,
                    check_command=command or None,
                )
                db.add(conn)
            elif body.clear_check_command or body.check_command is not None:
                conn.check_command = command or None
            conn.provider, conn.location = body.provider, location
            conn.default_branch = body.default_branch
            conn.token_ct = token_ct
            conn.updated_at = datetime.now(UTC)
            await record_audit(
                db,
                outcome="success",
                details={"provider": body.provider, "location": location[:200]},
                **audit,  # type: ignore[arg-type]
            )
            await db.flush()
        out = _repo_out(conn, head) if conn is not None else None
    if failure is not None or out is None:
        raise repo_http_error(failure or RepoError("repo_error"))
    return out


@router.get(
    "/v1/organizations/{org_id}/projects/{project_id}/repository",
    response_model=RepositoryOut | None,
)
async def get_repository(
    project_id: uuid.UUID,
    access: OrgAccess = Depends(org_access("viewer")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> RepositoryOut | None:
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        await _project(db, access.org_id, project_id)
        conn = await connection_for(db, access.org_id, project_id)
        return _repo_out(conn) if conn else None


@router.delete("/v1/organizations/{org_id}/projects/{project_id}/repository", status_code=204)
async def disconnect_repository(
    project_id: uuid.UUID,
    access: OrgAccess = Depends(org_access("admin")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> Response:
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        await _project(db, access.org_id, project_id)
        conn = await connection_for(db, access.org_id, project_id)
        if conn is None:
            raise HTTPException(404, "No repository is connected.")
        await db.delete(conn)
        await record_audit(
            db,
            action="repository.disconnect",
            outcome="success",
            actor_user_id=access.user.user_id,
            org_id=access.org_id,
            target_type="project",
            target_id=project_id,
            details={"provider": conn.provider},
        )
    return Response(status_code=204)


async def _load_issue(db: AsyncSession, org_id: uuid.UUID, issue_id: uuid.UUID) -> Issue:
    issue = await db.scalar(select(Issue).where(Issue.id == issue_id, Issue.org_id == org_id))
    if issue is None:
        raise HTTPException(404, "Issue not found.")
    return issue


@router.post("/v1/organizations/{org_id}/issues/{issue_id}/repairs", response_model=RepairOut)
async def plan_repair(
    issue_id: uuid.UUID,
    request: Request,
    body: RepairIn | None = None,
    access: OrgAccess = Depends(org_access("member")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
    provider: RepairProvider = Depends(get_repair_provider),  # noqa: B008
) -> RepairOut:
    body = body or RepairIn()
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        issue = await _load_issue(db, access.org_id, issue_id)
        if issue.state not in ("open", "needs_review"):
            raise HTTPException(409, "Only an open issue can be planned for repair.")
        conn = await connection_for(db, access.org_id, issue.project_id)
        if conn is None:
            raise HTTPException(409, "Connect a repository to this project first.")
        today = await db.scalar(
            select(func.count())
            .select_from(RepairPlan)
            .where(
                RepairPlan.org_id == access.org_id,
                RepairPlan.created_at >= func.now() - func.make_interval(0, 0, 0, 1),
            )
        )
        if (today or 0) >= settings.repair_plans_per_day_per_org:
            raise HTTPException(429, "Your organization reached its daily repair planning limit.")
        facts = [str(f.get("text", ""))[:300] for f in issue.facts][:12]
        inp = RepairInput(
            title=issue.title,
            category=issue.category,
            severity=issue.severity,
            component=issue.component,
            summary=issue.summary,
            facts=facts,
            suggestion=issue.proposed_fix,
            files={},
        )
        token = token_of(settings, conn)
        repo = client_for(request, settings, conn, token)
        default_branch = conn.default_branch
        needs_review = issue.classification == "needs_review" or issue.state == "needs_review"
    try:
        result: PlanResult = await build_plan(
            repo=repo,
            settings=settings,
            default_branch=default_branch,
            issue=inp,
            needs_review=needs_review,
            provider=provider,
            manual=body.changes,
            manual_explanation=body.explanation,
        )
    except RepoError as exc:
        raise repo_http_error(exc) from exc
    except ProviderError as exc:
        status, message = _PROVIDER_ERRORS.get(exc.code, (502, "Repair planning failed."))
        raise HTTPException(status, message) from exc
    finally:
        aclose: Any = getattr(repo, "aclose", None)
        if aclose is not None:
            await aclose()

    plan_id = uuid.uuid4()
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        if result.status == "proposed":
            await db.execute(
                update(RepairPlan)
                .where(RepairPlan.issue_id == issue_id, RepairPlan.status == "proposed")
                .values(status="superseded")
            )
        plan = RepairPlan(
            id=plan_id,
            org_id=access.org_id,
            project_id=issue.project_id,
            issue_id=issue_id,
            connection_id=conn.id,
            requested_by=access.user.user_id,
            status=result.status,
            source=result.source,
            provider=result.provider,
            model=result.model,
            base_branch=default_branch,
            base_sha=result.base_sha,
            branch_name=f"kmr-ai/fix-{str(issue_id)[:8]}-{str(plan_id)[:8]}",
            explanation=result.explanation[:1500],
            changes=result.changes,
            diff=result.diff,
            diff_sha256=result.diff_sha256,
            findings=[f.as_dict() for f in result.findings],
            warnings=result.warnings[:30],
        )
        db.add(plan)
        await record_audit(
            db,
            action="repair.plan",
            outcome="success" if result.status in ("proposed", "no_fix") else "denied",
            actor_user_id=access.user.user_id,
            org_id=access.org_id,
            target_type="repair_plan",
            target_id=plan_id,
            details={
                "status": result.status,
                "source": result.source,
                "diff_sha256": result.diff_sha256,
                "rules": [f.rule for f in result.findings],
            },
        )
        await db.flush()
        await db.refresh(plan)
        return repair_out(plan)


@router.get("/v1/organizations/{org_id}/issues/{issue_id}/repairs", response_model=list[RepairOut])
async def list_repairs(
    issue_id: uuid.UUID,
    access: OrgAccess = Depends(org_access("viewer")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> list[RepairOut]:
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        await _load_issue(db, access.org_id, issue_id)
        plans = await db.scalars(
            select(RepairPlan)
            .where(RepairPlan.issue_id == issue_id, RepairPlan.org_id == access.org_id)
            .order_by(RepairPlan.created_at.desc())
            .limit(50)
        )
        return [repair_out(p) for p in plans]


@router.get("/v1/organizations/{org_id}/repairs", response_model=list[RepairOut])
async def list_org_repairs(
    status: Annotated[str | None, Query(max_length=20)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    access: OrgAccess = Depends(org_access("viewer")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> list[RepairOut]:
    filters = [RepairPlan.org_id == access.org_id]
    if status:
        filters.append(RepairPlan.status == status)
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        plans = await db.scalars(
            select(RepairPlan).where(*filters).order_by(RepairPlan.created_at.desc()).limit(limit)
        )
        return [repair_out(p) for p in plans]


@router.get("/v1/organizations/{org_id}/repairs/{repair_id}", response_model=RepairOut)
async def get_repair(
    repair_id: uuid.UUID,
    access: OrgAccess = Depends(org_access("viewer")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> RepairOut:
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        plan = await db.scalar(
            select(RepairPlan).where(RepairPlan.id == repair_id, RepairPlan.org_id == access.org_id)
        )
        if plan is None:
            raise HTTPException(404, "Repair not found.")
        return repair_out(plan)
