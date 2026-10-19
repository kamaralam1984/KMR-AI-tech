import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import case, func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from app.auth.deps import OrgAccess, allow, get_engine, get_rate_limiter, org_access
from app.config import Settings, get_settings
from app.db.audit import record_audit
from app.db.models import Project
from app.db.tenant import tenant_session
from app.routes.projects import _out
from app.scanning.ownership import (
    OwnershipVerifier,
    VerificationUnavailable,
    dns_record_name,
    file_url,
    proof_value,
)
from app.scanning.site import parse_site_url
from app.schemas import ProjectOut, SiteIn, VerifyIn, VerifyResultOut
from app.security.rate_limit import RateLimiter

router = APIRouter(prefix="/v1/organizations/{org_id}/projects/{project_id}", tags=["sites"])


def get_verifier(request: Request) -> OwnershipVerifier:
    verifier: OwnershipVerifier | None = getattr(request.app.state, "ownership_verifier", None)
    if verifier is None:
        raise HTTPException(503, "Ownership verification is not available.")
    return verifier


@router.put("/site", response_model=ProjectOut)
async def set_site(
    project_id: uuid.UUID,
    body: SiteIn,
    access: OrgAccess = Depends(org_access("member")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> ProjectOut:
    """Saves the site address. Changing the host drops verification: it must be proven again."""
    target = parse_site_url(body.site_url, settings)
    max_pages = min(body.max_pages, settings.scan_max_pages_cap)
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        project = await db.scalar(
            update(Project)
            .where(Project.id == project_id, Project.org_id == access.org_id)
            .values(
                site_url=target.url,
                site_host=target.host,
                scan_exclusions=body.exclusions,
                scan_max_pages=max_pages,
                inject_request_id=body.inject_request_id,
                updated_at=func.now(),
                # In an UPDATE the right-hand sides see the OLD row: if the host is changing,
                # the old proof is discarded, even if the user later switches back.
                verified_at=case((Project.site_host == target.host, Project.verified_at)),
                verified_method=case((Project.site_host == target.host, Project.verified_method)),
                verified_host=case((Project.site_host == target.host, Project.verified_host)),
            )
            .returning(Project)
            .execution_options(populate_existing=True)
        )
        if project is None:
            raise HTTPException(404, "Project not found.")
        await record_audit(
            db,
            action="site.set",
            outcome="success",
            actor_user_id=access.user.user_id,
            org_id=access.org_id,
            target_type="project",
            target_id=project_id,
            details={"host": target.host, "verified": project.is_verified},
        )
        return _out(project)


@router.post("/verify", response_model=VerifyResultOut)
async def verify_ownership(
    project_id: uuid.UUID,
    body: VerifyIn,
    access: OrgAccess = Depends(org_access("member")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
    limiter: RateLimiter = Depends(get_rate_limiter),  # noqa: B008
    verifier: OwnershipVerifier = Depends(get_verifier),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> VerifyResultOut:
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        project = await db.scalar(
            select(Project).where(Project.id == project_id, Project.org_id == access.org_id)
        )
        if project is None:
            raise HTTPException(404, "Project not found.")
        if not project.site_url or not project.site_host:
            raise HTTPException(409, "Set the website address first.")
        host, token, url = project.site_host, project.verification_token, project.site_url

    if not await allow(
        limiter, f"verify:{project_id}", settings.verification_checks_per_hour, 3600
    ):
        raise HTTPException(429, "Too many checks. Try again later.")

    expected = proof_value(token)
    found = False
    reason: str | None = None
    try:
        if body.method == "dns":
            records = await verifier.dns_txt(dns_record_name(host))
            found = any(record.strip() == expected for record in records)
            reason = None if found else "The DNS record was not found or does not match yet."
        else:
            scheme = "http" if url.startswith("http://") else "https"
            content = await verifier.fetch_file(file_url(scheme, host))
            found = content is not None and content.strip() == expected
            reason = None if found else "The file was not found or its content does not match."
    except VerificationUnavailable as exc:
        raise HTTPException(
            503, "Verification is temporarily unavailable. Try again later."
        ) from exc

    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        project = await db.scalar(
            select(Project).where(Project.id == project_id, Project.org_id == access.org_id)
        )
        if project is None:
            raise HTTPException(404, "Project not found.")
        if found and project.site_host == host:  # ignore a result for a host that has since changed
            await db.execute(
                update(Project)
                .where(Project.id == project_id)
                .values(verified_at=func.now(), verified_method=body.method, verified_host=host)
            )
            project = await db.scalar(
                select(Project)
                .where(Project.id == project_id)
                .execution_options(populate_existing=True)
            )
        await record_audit(
            db,
            action="domain.verify",
            outcome="success" if found else "failure",
            actor_user_id=access.user.user_id,
            org_id=access.org_id,
            target_type="project",
            target_id=project_id,
            details={"method": body.method, "host": host},
        )
        if project is None:
            raise HTTPException(404, "Project not found.")
        return VerifyResultOut(verified=found, reason=reason, project=_out(project))
