"""Row-Level Security tested directly against Postgres, as the restricted app role.

These bypass the API on purpose: they prove the database itself refuses cross-tenant access,
even if application code were wrong.
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import DBAPIError

from tests.conftest import Seeded

TENANT_TABLES = ("organizations", "memberships", "projects", "audit_events")


def as_tenant(conn: Connection, user: uuid.UUID | None, org: uuid.UUID | None) -> None:
    conn.execute(
        text(
            "SELECT set_config('app.current_user_id', :u, true), "
            "set_config('app.current_org_id', :o, true)"
        ),
        {"u": str(user) if user else "", "o": str(org) if org else ""},
    )


def test_app_role_cannot_bypass_rls(app_sync_engine: Engine) -> None:
    with app_sync_engine.connect() as conn:
        row = conn.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ).one()
    assert row == (False, False)


@pytest.mark.parametrize("table", TENANT_TABLES)
def test_rls_is_enabled_and_forced_on_tenant_tables(app_sync_engine: Engine, table: str) -> None:
    with app_sync_engine.connect() as conn:
        row = conn.execute(
            text("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = :t"),
            {"t": table},
        ).one()
    assert row == (True, True)


@pytest.mark.parametrize("table", TENANT_TABLES)
def test_no_context_sees_nothing(app_sync_engine: Engine, seeded: Seeded, table: str) -> None:
    with app_sync_engine.begin() as conn:
        assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0  # noqa: S608 - fixed list


def test_org_context_filters_unfiltered_queries(app_sync_engine: Engine, seeded: Seeded) -> None:
    with app_sync_engine.begin() as conn:
        as_tenant(conn, seeded.user_a, seeded.org_a)
        projects = conn.execute(text("SELECT id FROM projects")).scalars().all()  # no WHERE
    assert projects == [seeded.project_a]


def test_cannot_insert_into_another_tenant(app_sync_engine: Engine, seeded: Seeded) -> None:
    with pytest.raises(DBAPIError, match="row-level security"), app_sync_engine.begin() as conn:
        as_tenant(conn, seeded.user_a, seeded.org_a)
        conn.execute(
            text("INSERT INTO projects (org_id, name, created_by) VALUES (:o, 'evil', :u)"),
            {"o": seeded.org_b, "u": seeded.user_a},
        )


def test_cannot_update_or_hide_other_tenant_rows(
    app_sync_engine: Engine, seeded: Seeded, owner_engine: Engine
) -> None:
    with app_sync_engine.begin() as conn:
        as_tenant(conn, seeded.user_a, seeded.org_a)
        result = conn.execute(
            text("UPDATE projects SET name = 'pwned' WHERE id = :p"), {"p": seeded.project_b}
        )
        assert result.rowcount == 0
    with owner_engine.connect() as conn:
        name = conn.execute(
            text("SELECT name FROM projects WHERE id = :p"), {"p": seeded.project_b}
        ).scalar()
    assert name == "Project of Org B"


def test_cannot_move_a_row_to_another_tenant(app_sync_engine: Engine, seeded: Seeded) -> None:
    with pytest.raises(DBAPIError, match="row-level security"), app_sync_engine.begin() as conn:
        as_tenant(conn, seeded.user_a, seeded.org_a)
        conn.execute(
            text("UPDATE projects SET org_id = :b WHERE id = :p"),
            {"b": seeded.org_b, "p": seeded.project_a},
        )


def test_cannot_delete_projects_or_users(app_sync_engine: Engine, seeded: Seeded) -> None:
    for statement in ("DELETE FROM projects", "DELETE FROM users", "DELETE FROM organizations"):
        with pytest.raises(DBAPIError, match="permission denied"), app_sync_engine.begin() as conn:
            as_tenant(conn, seeded.user_a, seeded.org_a)
            conn.execute(text(statement))


def test_user_context_sees_only_own_memberships_and_orgs(
    app_sync_engine: Engine, seeded: Seeded
) -> None:
    with app_sync_engine.begin() as conn:
        as_tenant(conn, seeded.user_a, None)
        orgs = conn.execute(text("SELECT id FROM organizations")).scalars().all()
        members = conn.execute(text("SELECT org_id FROM memberships")).scalars().all()
        projects = conn.execute(text("SELECT id FROM projects")).scalars().all()
    assert orgs == [seeded.org_a]
    assert members == [seeded.org_a]
    assert projects == []  # no active organization, so no tenant data


def test_organization_insert_requires_matching_context(
    app_sync_engine: Engine, seeded: Seeded
) -> None:
    new_org = uuid.uuid4()
    with pytest.raises(DBAPIError, match="row-level security"), app_sync_engine.begin() as conn:
        as_tenant(conn, seeded.user_a, seeded.org_a)  # context is org A, row is a different id
        conn.execute(
            text("INSERT INTO organizations (id, name, created_by) VALUES (:i, 'x', :u)"),
            {"i": new_org, "u": seeded.user_a},
        )
    with app_sync_engine.begin() as conn:
        as_tenant(conn, seeded.user_a, new_org)
        conn.execute(
            text("INSERT INTO organizations (id, name, created_by) VALUES (:i, 'ok', :u)"),
            {"i": new_org, "u": seeded.user_a},
        )


def test_audit_log_is_append_only_and_tenant_scoped(
    app_sync_engine: Engine, seeded: Seeded
) -> None:
    insert = text(
        "INSERT INTO audit_events (org_id, actor_user_id, action, outcome) "
        "VALUES (:o, :u, 'test', 'success')"
    )
    with app_sync_engine.begin() as conn:
        as_tenant(conn, seeded.user_a, seeded.org_a)
        conn.execute(insert, {"o": seeded.org_a, "u": seeded.user_a})
        conn.execute(insert, {"o": None, "u": seeded.user_a})  # org-less event is allowed
        # ...but is not readable by the app, and only this org's rows are visible.
        assert conn.execute(text("SELECT count(*) FROM audit_events")).scalar() == 1
    with pytest.raises(DBAPIError, match="row-level security"), app_sync_engine.begin() as conn:
        as_tenant(conn, seeded.user_a, seeded.org_a)
        conn.execute(insert, {"o": seeded.org_b, "u": seeded.user_a})
    for statement in ("UPDATE audit_events SET action = 'x'", "DELETE FROM audit_events"):
        with pytest.raises(DBAPIError, match="permission denied"), app_sync_engine.begin() as conn:
            as_tenant(conn, seeded.user_a, seeded.org_a)
            conn.execute(text(statement))


def test_context_does_not_leak_across_pooled_connections(
    app_sync_engine: Engine, seeded: Seeded
) -> None:
    # The fixture's pool holds exactly one connection, so both transactions reuse it.
    with app_sync_engine.begin() as conn:
        as_tenant(conn, seeded.user_a, seeded.org_a)
        assert conn.execute(text("SELECT count(*) FROM projects")).scalar() == 1
    with app_sync_engine.begin() as conn:
        assert conn.execute(
            text("SELECT current_setting('app.current_org_id', true)")
        ).scalar() in ("", None)
        assert conn.execute(text("SELECT count(*) FROM projects")).scalar() == 0
