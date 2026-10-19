"""Database-level guarantees for scanning: isolation, integrity and the queue functions."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests.conftest import Seeded, TestDatabase
from tests.test_rls import as_tenant

SCAN_TABLES = ("scans", "observations", "evidence")


@pytest.mark.parametrize("table", SCAN_TABLES)
def test_rls_is_enabled_and_forced_on_scan_tables(app_sync_engine: Engine, table: str) -> None:
    with app_sync_engine.connect() as conn:
        row = conn.execute(
            text("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = :t"),
            {"t": table},
        ).one()
    assert row == (True, True)


def test_scans_are_invisible_without_context_and_filtered_with_it(
    app_sync_engine: Engine, seeded: Seeded, seeded_scans: dict[str, uuid.UUID]
) -> None:
    with app_sync_engine.begin() as conn:
        assert conn.execute(text("SELECT count(*) FROM scans")).scalar() == 0
        as_tenant(conn, seeded.user_a, seeded.org_a)
        assert conn.execute(text("SELECT id FROM scans")).scalars().all() == [
            seeded_scans["scan_a"]
        ]


def test_cannot_create_or_modify_scans_of_another_tenant(
    app_sync_engine: Engine, seeded: Seeded, seeded_scans: dict[str, uuid.UUID]
) -> None:
    with pytest.raises(DBAPIError, match="row-level security"), app_sync_engine.begin() as conn:
        as_tenant(conn, seeded.user_a, seeded.org_a)
        conn.execute(
            text(
                "INSERT INTO scans (org_id, project_id, requested_by, site_url, allowed_host, "
                "limits) VALUES (:o, :p, :u, 'https://x.example.com/', 'x.example.com', '{}')"
            ),
            {"o": seeded.org_b, "p": seeded.project_b, "u": seeded.user_a},
        )
    with app_sync_engine.begin() as conn:
        as_tenant(conn, seeded.user_a, seeded.org_a)
        result = conn.execute(
            text("UPDATE scans SET status = 'cancelled' WHERE id = :s"),
            {"s": seeded_scans["scan_b"]},
        )
        assert result.rowcount == 0


def test_a_scan_cannot_point_at_another_tenants_project(
    owner_engine: Engine, seeded: Seeded
) -> None:
    """Composite foreign key: (project_id, org_id) must match a real project of that org."""
    with pytest.raises(IntegrityError), owner_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO scans (org_id, project_id, requested_by, site_url, allowed_host, "
                "limits) VALUES (:o, :p, :u, 'https://x.example.com/', 'x.example.com', '{}')"
            ),
            {"o": seeded.org_a, "p": seeded.project_b, "u": seeded.user_a},
        )


def test_observations_cannot_be_attached_to_another_tenants_scan(
    owner_engine: Engine, seeded: Seeded, seeded_scans: dict[str, uuid.UUID]
) -> None:
    with pytest.raises(IntegrityError), owner_engine.begin() as conn:
        conn.execute(
            text("INSERT INTO observations (org_id, scan_id, kind) VALUES (:o, :s, 'note')"),
            {"o": seeded.org_a, "s": seeded_scans["scan_b"]},
        )


def test_only_one_active_scan_per_project(owner_engine: Engine, seeded: Seeded) -> None:
    insert = text(
        "INSERT INTO scans (org_id, project_id, requested_by, status, site_url, allowed_host, "
        "limits) VALUES (:o, :p, :u, :st, 'https://a.example.com/', 'a.example.com', '{}')"
    )
    args = {"o": seeded.org_a, "p": seeded.project_a, "u": seeded.user_a}
    with owner_engine.begin() as conn:
        conn.execute(insert, {**args, "st": "completed"})
        conn.execute(insert, {**args, "st": "completed"})  # history is unlimited
        conn.execute(insert, {**args, "st": "queued"})
    with pytest.raises(IntegrityError), owner_engine.begin() as conn:
        conn.execute(insert, {**args, "st": "running"})


def test_idempotency_key_is_unique_per_project(owner_engine: Engine, seeded: Seeded) -> None:
    insert = text(
        "INSERT INTO scans (org_id, project_id, requested_by, status, site_url, allowed_host, "
        "limits, idempotency_key) VALUES (:o, :p, :u, 'completed', 'https://a.example.com/', "
        "'a.example.com', '{}', :k)"
    )
    args = {"o": seeded.org_a, "p": seeded.project_a, "u": seeded.user_a, "k": "abc"}
    with owner_engine.begin() as conn:
        conn.execute(insert, args)
    with pytest.raises(IntegrityError), owner_engine.begin() as conn:
        conn.execute(insert, args)


def test_evidence_and_observations_are_append_only_for_the_app(
    app_sync_engine: Engine, seeded: Seeded, seeded_scans: dict[str, uuid.UUID]
) -> None:
    for statement in (
        "DELETE FROM observations",
        "UPDATE observations SET kind = 'note'",
        "DELETE FROM evidence",
        "UPDATE evidence SET kind = 'screenshot'",
    ):
        with pytest.raises(DBAPIError, match="permission denied"), app_sync_engine.begin() as conn:
            as_tenant(conn, seeded.user_a, seeded.org_a)
            conn.execute(text(statement))


# ------------------------------------------------------------------------ queue functions


def test_claim_moves_a_queued_scan_to_running_once(
    app_sync_engine: Engine, seeded: Seeded, seeded_scans: dict[str, uuid.UUID]
) -> None:
    claim = text("SELECT * FROM scan_claim(:s)")
    with app_sync_engine.begin() as conn:  # no tenant context at all
        first = conn.execute(claim, {"s": seeded_scans["scan_a"]}).all()
        second = conn.execute(claim, {"s": seeded_scans["scan_a"]}).all()
    assert first == [(seeded.org_a, seeded.project_a, seeded.user_a)]
    assert second == []  # a duplicate queue message cannot start it twice


def test_claim_refuses_a_scan_with_a_pending_cancel(
    app_sync_engine: Engine, owner_engine: Engine, seeded_scans: dict[str, uuid.UUID]
) -> None:
    with owner_engine.begin() as conn:
        conn.execute(
            text("UPDATE scans SET cancel_requested_at = now() WHERE id = :s"),
            {"s": seeded_scans["scan_a"]},
        )
    with app_sync_engine.begin() as conn:
        assert (
            conn.execute(text("SELECT * FROM scan_claim(:s)"), {"s": seeded_scans["scan_a"]}).all()
            == []
        )


def test_queue_functions_are_not_executable_by_public(
    owner_engine: Engine, pg: TestDatabase
) -> None:
    with owner_engine.connect() as conn:
        for signature in ("scan_claim(uuid)", "scan_recover(interval, interval, int)"):
            public = conn.execute(
                text("SELECT has_function_privilege('public', :f, 'EXECUTE')"), {"f": signature}
            ).scalar()
            app = conn.execute(
                text("SELECT has_function_privilege(:r, :f, 'EXECUTE')"),
                {"r": pg.app_role, "f": signature},
            ).scalar()
            owner = conn.execute(
                text("SELECT pg_get_userbyid(proowner) FROM pg_proc WHERE proname = :n"),
                {"n": signature.split("(")[0]},
            ).scalar()
            assert public is False and app is True
            assert owner == pg.queue_role


def test_the_app_role_still_cannot_read_scans_directly_without_context(
    app_sync_engine: Engine, seeded_scans: dict[str, uuid.UUID]
) -> None:
    """The functions open exactly two doors; the table itself stays closed."""
    with app_sync_engine.begin() as conn:
        assert conn.execute(text("SELECT count(*) FROM scans")).scalar() == 0


def _age(owner_engine: Engine, scan: uuid.UUID, **columns: str) -> None:
    sets = ", ".join(f"{name} = now() - interval '{age}'" for name, age in columns.items())
    with owner_engine.begin() as conn:
        conn.execute(text(f"UPDATE scans SET {sets} WHERE id = :s"), {"s": scan})  # noqa: S608


def test_recover_requeues_then_fails_scans_whose_worker_died(
    app_sync_engine: Engine, owner_engine: Engine, seeded_scans: dict[str, uuid.UUID]
) -> None:
    recover = text("SELECT * FROM scan_recover('5 minutes', '1 minute', 2)")
    a = seeded_scans["scan_a"]
    with owner_engine.begin() as conn:
        conn.execute(
            text("UPDATE scans SET status = 'running', attempts = 1 WHERE id = :s"), {"s": a}
        )
    _age(owner_engine, a, heartbeat_at="2 minutes")

    with app_sync_engine.begin() as conn:
        assert conn.execute(recover).all() == [(a, "requeued")]
    with owner_engine.connect() as conn:
        assert (
            conn.execute(text("SELECT status FROM scans WHERE id = :s"), {"s": a}).scalar()
            == "queued"
        )

    # Second death, attempts exhausted: give up instead of looping forever.
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE scans SET status = 'running', attempts = 2, "
                "heartbeat_at = now() - interval '2 minutes' WHERE id = :s"
            ),
            {"s": a},
        )
    with app_sync_engine.begin() as conn:
        assert conn.execute(recover).all() == [(a, "failed")]
    with owner_engine.connect() as conn:
        row = conn.execute(
            text("SELECT status, error_code, finished_at IS NOT NULL FROM scans WHERE id = :s"),
            {"s": a},
        ).one()
    assert row == ("failed", "worker_lost", True)


def test_recover_leaves_healthy_scans_alone_and_resends_lost_messages(
    app_sync_engine: Engine, owner_engine: Engine, seeded_scans: dict[str, uuid.UUID]
) -> None:
    recover = text("SELECT * FROM scan_recover('5 minutes', '1 minute', 2)")
    a, b = seeded_scans["scan_a"], seeded_scans["scan_b"]
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE scans SET status = 'running', attempts = 1, "
                "heartbeat_at = now() WHERE id = :s"
            ),
            {"s": a},
        )
    _age(owner_engine, b, queued_at="10 minutes")
    with app_sync_engine.begin() as conn:
        assert conn.execute(recover).all() == [(b, "requeue_message")]
    with owner_engine.connect() as conn:
        assert (
            conn.execute(text("SELECT status FROM scans WHERE id = :s"), {"s": a}).scalar()
            == "running"
        )
