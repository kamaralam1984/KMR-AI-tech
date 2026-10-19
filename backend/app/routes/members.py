import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.auth.deps import OrgAccess, allow, get_engine, get_rate_limiter, org_access
from app.db.audit import record_audit
from app.db.models import Membership, User
from app.db.tenant import ROLE_RANK, Role, tenant_session
from app.schemas import MemberAddIn, MemberOut, MemberRoleIn
from app.security.rate_limit import RateLimiter

router = APIRouter(prefix="/v1/organizations/{org_id}/members", tags=["members"])

# Adding a member by email reveals whether an account exists, so it is rate limited.
ADD_MEMBER_PER_HOUR = 30


def _may_assign(actor: Role, target_current: str | None, new_role: str | None) -> bool:
    """Owners manage everyone. Admins manage only members and viewers, and cannot promote."""
    if actor == "owner":
        return True
    if actor != "admin":
        return False
    admin_rank = ROLE_RANK["admin"]
    if target_current is not None and ROLE_RANK[target_current] >= admin_rank:
        return False
    return not (new_role is not None and ROLE_RANK[new_role] >= admin_rank)


async def _owner_count_locked(db: AsyncSession, org_id: uuid.UUID) -> int:
    # FOR UPDATE serializes concurrent changes, so two owners cannot demote each other at once.
    rows = await db.scalars(
        select(Membership.id)
        .where(Membership.org_id == org_id, Membership.role == "owner")
        .with_for_update()
    )
    return len(rows.all())


@router.get("", response_model=list[MemberOut])
async def list_members(
    access: OrgAccess = Depends(org_access("viewer")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> list[MemberOut]:
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        rows = (
            await db.execute(
                select(User.id, User.email, User.display_name, Membership.role)
                .join(Membership, Membership.user_id == User.id)
                .where(Membership.org_id == access.org_id)
                .order_by(User.email)
            )
        ).all()
    return [
        MemberOut(user_id=r.id, email=r.email, display_name=r.display_name, role=r.role)
        for r in rows
    ]


@router.post("", status_code=201, response_model=MemberOut)
async def add_member(
    body: MemberAddIn,
    access: OrgAccess = Depends(org_access("admin")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
    limiter: RateLimiter = Depends(get_rate_limiter),  # noqa: B008
) -> MemberOut:
    if not _may_assign(access.role, None, body.role):
        raise HTTPException(403, "You cannot assign that role.")
    if not await allow(limiter, f"member-add:{access.user.user_id}", ADD_MEMBER_PER_HOUR, 3600):
        raise HTTPException(429, "Too many requests. Try again later.")
    try:
        async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
            target = await db.scalar(select(User).where(User.email == body.email))
            if target is None or not target.is_active:
                raise HTTPException(404, "No account with that email address.")
            db.add(Membership(org_id=access.org_id, user_id=target.id, role=body.role))
            await db.flush()
            await record_audit(
                db,
                action="member.add",
                outcome="success",
                actor_user_id=access.user.user_id,
                org_id=access.org_id,
                target_type="user",
                target_id=target.id,
                details={"role": body.role},
            )
    except IntegrityError as exc:
        raise HTTPException(409, "That user is already a member.") from exc
    return MemberOut(
        user_id=target.id, email=target.email, display_name=target.display_name, role=body.role
    )


@router.patch("/{user_id}", response_model=MemberOut)
async def change_role(
    user_id: uuid.UUID,
    body: MemberRoleIn,
    access: OrgAccess = Depends(org_access("admin")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> MemberOut:
    if user_id == access.user.user_id:
        raise HTTPException(403, "You cannot change your own role.")
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        membership = await db.scalar(
            select(Membership).where(
                Membership.org_id == access.org_id, Membership.user_id == user_id
            )
        )
        if membership is None:
            raise HTTPException(404, "Member not found.")
        if not _may_assign(access.role, membership.role, body.role):
            raise HTTPException(403, "You cannot change this member's role.")
        if membership.role == "owner" and body.role != "owner":
            if await _owner_count_locked(db, access.org_id) <= 1:
                raise HTTPException(409, "An organization must keep at least one owner.")
        previous = membership.role
        await db.execute(
            update(Membership)
            .where(Membership.org_id == access.org_id, Membership.user_id == user_id)
            .values(role=body.role)
        )
        target = await db.get(User, user_id)
        await record_audit(
            db,
            action="member.role_change",
            outcome="success",
            actor_user_id=access.user.user_id,
            org_id=access.org_id,
            target_type="user",
            target_id=user_id,
            details={"from": previous, "to": body.role},
        )
    if target is None:
        raise HTTPException(404, "Member not found.")
    return MemberOut(
        user_id=target.id, email=target.email, display_name=target.display_name, role=body.role
    )


@router.delete("/{user_id}", status_code=204)
async def remove_member(
    user_id: uuid.UUID,
    access: OrgAccess = Depends(org_access("viewer")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> Response:
    leaving = user_id == access.user.user_id
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        membership = await db.scalar(
            select(Membership).where(
                Membership.org_id == access.org_id, Membership.user_id == user_id
            )
        )
        if membership is None:
            raise HTTPException(404, "Member not found.")
        if not leaving and not _may_assign(access.role, membership.role, None):
            raise HTTPException(403, "You cannot remove this member.")
        if membership.role == "owner" and await _owner_count_locked(db, access.org_id) <= 1:
            raise HTTPException(409, "An organization must keep at least one owner.")
        await db.delete(membership)
        await record_audit(
            db,
            action="member.leave" if leaving else "member.remove",
            outcome="success",
            actor_user_id=access.user.user_id,
            org_id=access.org_id,
            target_type="user",
            target_id=user_id,
            details={"role": membership.role},
        )
    return Response(status_code=204)
