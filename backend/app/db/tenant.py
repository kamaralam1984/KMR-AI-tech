"""The only way application code reaches tenant data.

Every function here opens one transaction and sets two transaction-local settings,
`app.current_user_id` and `app.current_org_id`, which the Row-Level Security policies read.
Because they are set with `set_config(..., true)` they vanish when the transaction ends, so a
pooled connection can never carry one tenant's context into another request.

Background jobs must use `run_as_tenant`, which verifies membership and role first.
"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.db.models import Membership

Role = Literal["owner", "admin", "member", "viewer"]
ROLE_RANK: dict[str, int] = {"viewer": 0, "member": 1, "admin": 2, "owner": 3}


class TenantAccessDenied(Exception):  # noqa: N818 - reads better as a domain term
    """The user is not a member of the organization, or lacks the required role."""


def has_role(actual: str | None, minimum: Role) -> bool:
    return actual is not None and ROLE_RANK[actual] >= ROLE_RANK[minimum]


@asynccontextmanager
async def tenant_session(
    engine: AsyncEngine,
    *,
    user_id: uuid.UUID | None = None,
    org_id: uuid.UUID | None = None,
) -> AsyncIterator[AsyncSession]:
    """One transaction with the tenant context set. Commits on success, rolls back on error."""
    async with AsyncSession(engine, expire_on_commit=False) as session, session.begin():
        await session.execute(
            text(
                "SELECT set_config('app.current_user_id', :user_id, true), "
                "set_config('app.current_org_id', :org_id, true)"
            ),
            {
                "user_id": str(user_id) if user_id else "",
                "org_id": str(org_id) if org_id else "",
            },
        )
        yield session


async def get_role(engine: AsyncEngine, user_id: uuid.UUID, org_id: uuid.UUID) -> Role | None:
    async with tenant_session(engine, user_id=user_id, org_id=org_id) as session:
        role = await session.scalar(
            select(Membership.role).where(
                Membership.org_id == org_id, Membership.user_id == user_id
            )
        )
    return role  # type: ignore[return-value]


@dataclass(frozen=True)
class TenantScope:
    session: AsyncSession
    user_id: uuid.UUID
    org_id: uuid.UUID
    role: Role


@asynccontextmanager
async def run_as_tenant(
    engine: AsyncEngine,
    *,
    user_id: uuid.UUID,
    org_id: uuid.UUID,
    min_role: Role = "viewer",
) -> AsyncIterator[TenantScope]:
    """Authorize, then run inside the tenant context. Use this for background jobs."""
    role = await get_role(engine, user_id, org_id)
    if role is None or not has_role(role, min_role):
        raise TenantAccessDenied
    async with tenant_session(engine, user_id=user_id, org_id=org_id) as session:
        yield TenantScope(session=session, user_id=user_id, org_id=org_id, role=role)
