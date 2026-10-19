"""Background jobs get no special access: they must enter a tenant through `run_as_tenant`."""

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.engine import create_engine
from app.db.models import Project
from app.db.tenant import TenantAccessDenied, run_as_tenant, tenant_session
from tests.conftest import Seeded, TestDatabase

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def engine(pg: TestDatabase):  # type: ignore[no-untyped-def]
    async_engine: AsyncEngine = create_engine(pg.app_url)
    yield async_engine
    await async_engine.dispose()


async def test_member_job_sees_only_its_own_tenant(engine: AsyncEngine, seeded: Seeded) -> None:
    async with run_as_tenant(engine, user_id=seeded.user_a, org_id=seeded.org_a) as scope:
        projects = (await scope.session.scalars(select(Project.id))).all()  # no WHERE clause
    assert projects == [seeded.project_a]


async def test_job_for_a_non_member_is_refused(engine: AsyncEngine, seeded: Seeded) -> None:
    with pytest.raises(TenantAccessDenied):
        async with run_as_tenant(engine, user_id=seeded.user_a, org_id=seeded.org_b):
            pytest.fail("job body must not run")


async def test_job_for_an_unknown_organization_is_refused(
    engine: AsyncEngine, seeded: Seeded
) -> None:
    with pytest.raises(TenantAccessDenied):
        async with run_as_tenant(engine, user_id=seeded.user_a, org_id=uuid.uuid4()):
            pytest.fail("job body must not run")


async def test_job_needing_a_higher_role_is_refused(
    engine: AsyncEngine, seeded: Seeded, owner_engine: Engine
) -> None:
    with owner_engine.begin() as conn:
        conn.execute(
            text("UPDATE memberships SET role = 'viewer' WHERE user_id = :u"),
            {"u": seeded.user_a},
        )
    async with run_as_tenant(engine, user_id=seeded.user_a, org_id=seeded.org_a, min_role="viewer"):
        pass
    with pytest.raises(TenantAccessDenied):
        async with run_as_tenant(
            engine, user_id=seeded.user_a, org_id=seeded.org_a, min_role="member"
        ):
            pytest.fail("job body must not run")


async def test_revoked_membership_stops_jobs(
    engine: AsyncEngine, seeded: Seeded, owner_engine: Engine
) -> None:
    with owner_engine.begin() as conn:
        conn.execute(text("DELETE FROM memberships WHERE user_id = :u"), {"u": seeded.user_a})
    with pytest.raises(TenantAccessDenied):
        async with run_as_tenant(engine, user_id=seeded.user_a, org_id=seeded.org_a):
            pytest.fail("job body must not run")


async def test_a_session_without_tenant_context_sees_no_tenant_data(
    engine: AsyncEngine, seeded: Seeded
) -> None:
    async with tenant_session(engine) as session:
        assert (await session.scalars(select(Project.id))).all() == []
