import uuid

from sqlalchemy import text
from sqlalchemy.engine import Engine

from tests.helpers import project_base, registered
from tests.test_approvals_api import _apply, _approved
from tests.test_repairs_api import HOST, lang_issue, roots  # noqa: F401
from tests.test_verification import PAGE_OK, _scan, _verify


def test_a_new_organization_shows_zeros_not_invented_numbers(new_api) -> None:  # type: ignore[no-untyped-def]
    api = registered(new_api)
    org = api.create_org()
    body = api.get(f"/v1/organizations/{org}/dashboard").json()
    assert body["projects"] == 0
    assert body["scans"] == {
        "total": 0,
        "completed": 0,
        "failed": 0,
        "in_progress": 0,
        "last_completed_at": None,
    }
    assert body["issues"]["open"] == 0 and body["issues"]["open_by_severity"] == {}
    assert body["repairs"] == {
        "awaiting_decision": 0,
        "no_fix": 0,
        "rejected_by_rules": 0,
        "branches_created": 0,
    }
    assert body["verifications"] == {"fixed": 0, "not_fixed": 0, "needs_review": 0}
    usage = api.get(f"/v1/organizations/{org}/usage").json()
    assert usage["scans_last_24h"] == 0 and usage["ai_cost_usd_this_month"] is None
    system = api.get(f"/v1/organizations/{org}/system").json()
    assert system["scans_queued"] == 0 and system["oldest_queued_seconds"] is None


def test_the_dashboard_follows_the_real_records_through_the_whole_flow(
    lang_issue, fake_queue, owner_engine: Engine
) -> None:  # type: ignore[no-untyped-def]
    api, org, project, issue, repo, plan, approval = _approved(lang_issue)
    dash = f"/v1/organizations/{org}/dashboard"
    before = api.get(dash).json()
    assert before["scans"]["completed"] == 1 and before["projects"] == 1
    assert before["issues"]["open"] == 1 and before["issues"]["open_by_severity"] == {"low": 1}
    assert (
        before["repairs"]["awaiting_decision"] == 1 and before["repairs"]["branches_created"] == 0
    )
    assert _apply(api, org, plan, approval["id"]).status_code == 200
    assert api.get(dash).json()["repairs"]["branches_created"] == 1
    after = _scan(api, org, project, owner_engine, [PAGE_OK])
    assert _verify(api, org, issue, after).json()["outcome"] == "fixed"
    final = api.get(dash).json()
    assert final["issues"]["open"] == 0 and final["issues"]["fixed"] == 1
    assert final["verifications"] == {"fixed": 1, "not_fixed": 0, "needs_review": 0}
    assert final["scans"]["completed"] == 2 and final["scans"]["last_completed_at"]
    # Every number matches the table behind it.
    with owner_engine.connect() as conn:
        count = lambda sql: conn.execute(text(sql), {"o": org}).scalar()  # noqa: E731
        assert count("SELECT count(*) FROM issues WHERE org_id=:o AND state='fixed'") == 1
        assert count("SELECT count(*) FROM scans WHERE org_id=:o AND status='completed'") == 2
        assert count("SELECT count(*) FROM repair_applications WHERE org_id=:o") == 1


def test_usage_counts_match_what_actually_happened(lang_issue) -> None:  # type: ignore[no-untyped-def]
    api, org, *_ = _approved(lang_issue)
    usage = api.get(f"/v1/organizations/{org}/usage").json()
    assert usage["scans_last_24h"] == 1 and usage["diagnosis_runs_last_24h"] == 1
    assert usage["diagnosis_runs_this_month"] == 1 and usage["repair_plans_last_24h"] == 1
    assert usage["check_runs_last_hour"] == 1
    assert usage["scans_daily_limit"] >= 1 and usage["repair_plans_daily_limit"] >= 1


def test_system_status_reports_a_stalled_scan_without_infrastructure_details(
    lang_issue, fake_queue, owner_engine: Engine
) -> None:  # type: ignore[no-untyped-def]
    api, org, project, *_ = lang_issue
    queued = api.post(f"{project_base(org, project)}/scans").json()["id"]
    body = api.get(f"/v1/organizations/{org}/system").json()
    assert (
        body["scans_queued"] == 1
        and body["oldest_queued_seconds"] >= 0
        and body["scans_stalled"] == 0
    )
    with owner_engine.begin() as conn:
        conn.execute(
            text("UPDATE scans SET status='running', started_at=now()-interval '10 minutes', "
                 "heartbeat_at=now()-interval '9 minutes' WHERE id=:s"),
            {"s": queued},
        )  # fmt: skip
    stalled = api.get(f"/v1/organizations/{org}/system").json()
    assert stalled["scans_running"] == 1 and stalled["scans_stalled"] == 1
    flat = str(stalled).lower()
    for secret_word in ("password", "redis://", "postgres", "secret", "token"):
        assert secret_word not in flat


def test_roles_and_tenant_boundaries(lang_issue, new_api) -> None:  # type: ignore[no-untyped-def]
    api, org, *_ = lang_issue
    viewer = registered(new_api)
    api.add_member(org, viewer.email, "viewer")
    assert viewer.get(f"/v1/organizations/{org}/dashboard").status_code == 200
    assert viewer.get(f"/v1/organizations/{org}/usage").status_code == 403
    assert viewer.get(f"/v1/organizations/{org}/system").status_code == 403
    outsider = registered(new_api)
    other = outsider.create_org("Other")
    assert outsider.get(f"/v1/organizations/{org}/dashboard").status_code == 404
    mine = outsider.get(f"/v1/organizations/{other}/dashboard").json()
    assert mine["projects"] == 0 and mine["scans"]["total"] == 0 and mine["issues"]["open"] == 0
    assert uuid.UUID(org)
