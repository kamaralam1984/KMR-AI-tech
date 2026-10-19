import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from app.auth.deps import (
    CurrentUser,
    OrgAccess,
    current_user,
    get_engine,
    org_access,
)
from app.db.audit import record_audit
from app.db.models import Membership, Organization
from app.db.tenant import tenant_session
from app.schemas import OrgCreateIn, OrgOut, OrgUpdateIn

router = APIRouter(prefix="/v1/organizations", tags=["organizations"])

MAX_OWNED_ORGS = 10


@router.post("", status_code=201, response_model=OrgOut)
async def create_organization(
    body: OrgCreateIn,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> OrgOut:
    async with tenant_session(engine, user_id=user.user_id) as db:
        owned = await db.scalar(
            select(func.count())
            .select_from(Membership)
            .where(Membership.user_id == user.user_id, Membership.role == "owner")
        )
    if (owned or 0) >= MAX_OWNED_ORGS:
        raise HTTPException(409, f"You can own at most {MAX_OWNED_ORGS} organizations.")

    # The new id is generated here and set as the active organization first: the RLS insert
    # policy only accepts a row whose id matches the transaction's current organization.
    org_id = uuid.uuid4()
    async with tenant_session(engine, user_id=user.user_id, org_id=org_id) as db:
        org = Organization(id=org_id, name=body.name, created_by=user.user_id)
        db.add(org)
        await db.flush()
        db.add(Membership(org_id=org_id, user_id=user.user_id, role="owner"))
        await record_audit(
            db,
            action="org.create",
            outcome="success",
            actor_user_id=user.user_id,
            org_id=org_id,
            target_type="organization",
            target_id=org_id,
        )
        await db.refresh(org)
    return OrgOut(id=org.id, name=org.name, role="owner", created_at=org.created_at)


@router.get("", response_model=list[OrgOut])
async def list_organizations(
    user: CurrentUser = Depends(current_user),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> list[OrgOut]:
    async with tenant_session(engine, user_id=user.user_id) as db:
        rows = (
            await db.execute(
                select(Organization, Membership.role)
                .join(Membership, Membership.org_id == Organization.id)
                .where(Membership.user_id == user.user_id)
                .order_by(Organization.name)
            )
        ).all()
    return [
        OrgOut(id=org.id, name=org.name, role=role, created_at=org.created_at) for org, role in rows
    ]


@router.get("/{org_id}", response_model=OrgOut)
async def get_organization(
    access: OrgAccess = Depends(org_access("viewer")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> OrgOut:
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        org = await db.get(Organization, access.org_id)
    if org is None:
        raise HTTPException(404, "Organization not found.")
    return OrgOut(id=org.id, name=org.name, role=access.role, created_at=org.created_at)


@router.patch("/{org_id}", response_model=OrgOut)
async def rename_organization(
    body: OrgUpdateIn,
    access: OrgAccess = Depends(org_access("admin")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> OrgOut:
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        await db.execute(
            update(Organization).where(Organization.id == access.org_id).values(name=body.name)
        )
        org = await db.get(Organization, access.org_id, populate_existing=True)
        if org is None:
            raise HTTPException(404, "Organization not found.")
        await record_audit(
            db,
            action="org.rename",
            outcome="success",
            actor_user_id=access.user.user_id,
            org_id=access.org_id,
            target_type="organization",
            target_id=access.org_id,
        )
    return OrgOut(id=org.id, name=org.name, role=access.role, created_at=org.created_at)
