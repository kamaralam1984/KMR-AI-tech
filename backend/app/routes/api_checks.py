import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.auth.deps import OrgAccess, allow, get_engine, get_rate_limiter, org_access
from app.config import Settings, get_settings
from app.db.audit import record_audit
from app.db.models import ApiCheck, ApiCheckRun, Project
from app.db.tenant import tenant_session
from app.scanning.api_checks import (
    ApiTransport,
    CheckSpec,
    InvalidCheck,
    run_check,
    validate_header_name,
    validate_header_value,
    validate_path,
    validate_schema,
)
from app.scanning.net_policy import TargetRejected, validate_url
from app.scanning.site import net_policy
from app.schemas import ApiCheckIn, ApiCheckOut, ApiCheckPatch, ApiCheckRunOut
from app.security.rate_limit import RateLimiter
from app.security.secrets_box import SecretsUnavailable, decrypt_secret, encrypt_secret

router = APIRouter(
    prefix="/v1/organizations/{org_id}/projects/{project_id}/api-checks", tags=["api-checks"]
)

MAX_CHECKS_PER_PROJECT = 20


def get_transport(request: Request) -> ApiTransport:
    transport: ApiTransport | None = getattr(request.app.state, "api_transport", None)
    if transport is None:
        raise HTTPException(503, "API checks are not available.")
    return transport


def _run_out(run: ApiCheckRun) -> ApiCheckRunOut:
    return ApiCheckRunOut(
        id=run.id,
        check_id=run.check_id,
        scan_id=run.scan_id,
        triggered_by=run.triggered_by,
        status=run.status,
        http_status=run.http_status,
        latency_ms=run.latency_ms,
        response_bytes=run.response_bytes,
        reasons=run.reasons or [],
        created_at=run.created_at,
    )


def _out(check: ApiCheck, last: ApiCheckRun | None) -> ApiCheckOut:
    return ApiCheckOut(
        id=check.id,
        name=check.name,
        path=check.path,
        method=check.method,
        expected_status=check.expected_status,
        max_latency_ms=check.max_latency_ms,
        response_schema=check.response_schema,
        has_credentials=check.auth_secret_ct is not None,
        auth_header_name=check.auth_header_name,
        last_run=_run_out(last) if last else None,
        created_at=check.created_at,
    )


async def _project(db: AsyncSession, org_id: uuid.UUID, project_id: uuid.UUID) -> Project:
    project = await db.scalar(
        select(Project).where(Project.id == project_id, Project.org_id == org_id)
    )
    if project is None:
        raise HTTPException(404, "Project not found.")
    return project


async def _check(
    db: AsyncSession, org_id: uuid.UUID, project_id: uuid.UUID, check_id: uuid.UUID
) -> ApiCheck:
    check = await db.scalar(
        select(ApiCheck).where(
            ApiCheck.id == check_id, ApiCheck.project_id == project_id, ApiCheck.org_id == org_id
        )
    )
    if check is None:
        raise HTTPException(404, "API check not found.")
    return check


def _validated(path: str | None, schema: dict | None, credentials) -> None:  # type: ignore[no-untyped-def,type-arg]
    try:
        if path is not None:
            validate_path(path)
        if schema is not None:
            validate_schema(schema)
        if credentials is not None:
            validate_header_name(credentials.header_name)
            validate_header_value(credentials.value)
    except InvalidCheck as exc:
        raise HTTPException(422, str(exc)) from exc


def _encrypt(settings: Settings, value: str) -> bytes:
    try:
        return encrypt_secret(settings, value)
    except SecretsUnavailable as exc:
        raise HTTPException(503, "Credential storage is not configured on this server.") from exc


async def _last_runs(db: AsyncSession, ids: list[uuid.UUID]) -> dict[uuid.UUID, ApiCheckRun]:
    if not ids:
        return {}
    rows = await db.scalars(
        select(ApiCheckRun)
        .where(ApiCheckRun.check_id.in_(ids))
        .distinct(ApiCheckRun.check_id)
        .order_by(ApiCheckRun.check_id, ApiCheckRun.created_at.desc())
    )
    return {run.check_id: run for run in rows}


@router.get("", response_model=list[ApiCheckOut])
async def list_checks(
    project_id: uuid.UUID,
    access: OrgAccess = Depends(org_access("viewer")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> list[ApiCheckOut]:
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        await _project(db, access.org_id, project_id)
        checks = (
            await db.scalars(
                select(ApiCheck)
                .where(ApiCheck.project_id == project_id, ApiCheck.org_id == access.org_id)
                .order_by(ApiCheck.name)
            )
        ).all()
        last = await _last_runs(db, [c.id for c in checks])
        return [_out(c, last.get(c.id)) for c in checks]


@router.post("", status_code=201, response_model=ApiCheckOut)
async def create_check(
    project_id: uuid.UUID,
    body: ApiCheckIn,
    access: OrgAccess = Depends(org_access("member")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> ApiCheckOut:
    _validated(body.path, body.response_schema, body.credentials)
    ciphertext = _encrypt(settings, body.credentials.value) if body.credentials else None
    try:
        async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
            await _project(db, access.org_id, project_id)
            count = await db.scalar(
                select(func.count()).select_from(ApiCheck).where(ApiCheck.project_id == project_id)
            )
            if (count or 0) >= MAX_CHECKS_PER_PROJECT:
                raise HTTPException(
                    409, f"A project can have at most {MAX_CHECKS_PER_PROJECT} API checks."
                )
            check = ApiCheck(
                org_id=access.org_id,
                project_id=project_id,
                name=body.name,
                path=body.path,
                method=body.method,
                expected_status=body.expected_status,
                max_latency_ms=body.max_latency_ms,
                response_schema=body.response_schema,
                auth_header_name=body.credentials.header_name if body.credentials else None,
                auth_secret_ct=ciphertext,
                created_by=access.user.user_id,
            )
            db.add(check)
            await db.flush()
            await db.refresh(check)
            await record_audit(
                db,
                action="api_check.create",
                outcome="success",
                actor_user_id=access.user.user_id,
                org_id=access.org_id,
                target_type="api_check",
                target_id=check.id,
                details={"has_credentials": ciphertext is not None},
            )
            return _out(check, None)
    except IntegrityError as exc:
        raise HTTPException(409, "An API check with that name already exists.") from exc


@router.patch("/{check_id}", response_model=ApiCheckOut)
async def update_check(
    project_id: uuid.UUID,
    check_id: uuid.UUID,
    body: ApiCheckPatch,
    access: OrgAccess = Depends(org_access("member")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> ApiCheckOut:
    _validated(body.path, body.response_schema, body.credentials)
    try:
        async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
            check = await _check(db, access.org_id, project_id, check_id)
            for field in ("name", "path", "method", "expected_status", "max_latency_ms"):
                value = getattr(body, field)
                if value is not None:
                    setattr(check, field, value)
            if body.clear_schema:
                check.response_schema = None
            elif body.response_schema is not None:
                check.response_schema = body.response_schema
            if body.clear_credentials:
                check.auth_header_name, check.auth_secret_ct = None, None
            elif body.credentials is not None:
                check.auth_header_name = body.credentials.header_name
                check.auth_secret_ct = _encrypt(settings, body.credentials.value)
            check.updated_at = func.now()
            await db.flush()
            await db.refresh(check)
            await record_audit(
                db,
                action="api_check.update",
                outcome="success",
                actor_user_id=access.user.user_id,
                org_id=access.org_id,
                target_type="api_check",
                target_id=check.id,
            )
            last = await _last_runs(db, [check.id])
            return _out(check, last.get(check.id))
    except IntegrityError as exc:
        raise HTTPException(409, "An API check with that name already exists.") from exc


@router.delete("/{check_id}", status_code=204)
async def delete_check(
    project_id: uuid.UUID,
    check_id: uuid.UUID,
    access: OrgAccess = Depends(org_access("member")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> Response:
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        check = await _check(db, access.org_id, project_id, check_id)
        await db.delete(check)
        await record_audit(
            db,
            action="api_check.delete",
            outcome="success",
            actor_user_id=access.user.user_id,
            org_id=access.org_id,
            target_type="api_check",
            target_id=check_id,
        )
    return Response(status_code=204)


@router.post("/{check_id}/run", response_model=ApiCheckRunOut)
async def run_now(
    project_id: uuid.UUID,
    check_id: uuid.UUID,
    access: OrgAccess = Depends(org_access("member")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
    limiter: RateLimiter = Depends(get_rate_limiter),  # noqa: B008
    transport: ApiTransport = Depends(get_transport),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> ApiCheckRunOut:
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        project = await _project(db, access.org_id, project_id)
        check = await _check(db, access.org_id, project_id, check_id)
        if not project.site_url or not project.site_host:
            raise HTTPException(409, "Set the website address first.")
        if not project.is_verified:
            raise HTTPException(409, "Verify that you own the website before running checks.")
        spec = CheckSpec(
            check.path,
            check.method,
            check.expected_status,
            check.max_latency_ms,
            check.response_schema,
        )
        host, site_url = project.site_host, project.site_url
        header, ciphertext = check.auth_header_name, check.auth_secret_ct

    if not await allow(limiter, f"api-check:{project_id}", settings.api_check_runs_per_hour, 3600):
        raise HTTPException(429, "Too many check runs. Try again later.")

    policy = net_policy(settings)
    try:
        origin = validate_url(site_url, policy).origin
    except TargetRejected as exc:
        raise HTTPException(409, "The website address is no longer allowed to be checked.") from exc
    credentials: tuple[str, str] | None = None
    if header and ciphertext:
        secret = decrypt_secret(settings, ciphertext)
        credentials = (header, secret) if secret is not None else None
    request_id = f"kmr-chk-{uuid.uuid4().hex[:12]}"
    result = await run_check(
        spec,
        origin=origin,
        allowed_host=host,
        transport=transport,
        policy=policy,
        timeout=settings.api_check_timeout_seconds,
        request_id=request_id,
        credentials=credentials,
    )
    if header and ciphertext and credentials is None:
        result.reasons.append({"code": "credentials_unreadable"})  # key rotated or lost
        result.status = "error"

    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        run = ApiCheckRun(
            org_id=access.org_id,
            check_id=check_id,
            scan_id=None,
            triggered_by="manual",
            status=result.status,
            http_status=result.http_status,
            latency_ms=result.latency_ms,
            response_bytes=result.response_bytes,
            reasons=result.reasons,
            request_id=request_id,
        )
        db.add(run)
        await db.flush()
        await record_audit(
            db,
            action="api_check.run",
            outcome="success" if result.status == "passed" else "failure",
            actor_user_id=access.user.user_id,
            org_id=access.org_id,
            target_type="api_check",
            target_id=check_id,
            details={"status": result.status, "codes": [r["code"] for r in result.reasons]},
        )
        return _run_out(run)


@router.get("/{check_id}/runs", response_model=list[ApiCheckRunOut])
async def list_runs(
    project_id: uuid.UUID,
    check_id: uuid.UUID,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    access: OrgAccess = Depends(org_access("viewer")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> list[ApiCheckRunOut]:
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        await _check(db, access.org_id, project_id, check_id)
        runs = await db.scalars(
            select(ApiCheckRun)
            .where(ApiCheckRun.check_id == check_id, ApiCheckRun.org_id == access.org_id)
            .order_by(ApiCheckRun.created_at.desc())
            .limit(limit)
        )
        return [_run_out(r) for r in runs]
