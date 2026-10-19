import json
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.verification import AfterIssue, judge
from tests.helpers import project_base, registered
from tests.test_approvals_api import _apply, _approved, _branches  # noqa: F401
from tests.test_repairs_api import HOST, LANG_OBS, LANG_TITLE, lang_issue, roots  # noqa: F401

PAGE_OK = ("page", "info", {"status": 200, "title": "Home", "load_ms": 100})
JS_ERROR = ("page_error", "error", {"message": "TypeError: x is undefined"})


def _scan(api, org, project, owner_engine: Engine, observations, status="completed"):  # type: ignore[no-untyped-def]
    scan = api.post(f"{project_base(org, project)}/scans").json()["id"]
    with owner_engine.begin() as conn:
        conn.execute(
            text("UPDATE scans SET status=:st, finished_at=now() WHERE id=:s"),
            {"st": status, "s": scan},
        )
        for kind, severity, data in observations:
            conn.execute(
                text(
                    "INSERT INTO observations (id, org_id, scan_id, kind, severity, page_url, data) "
                    "VALUES (:i, :o, :s, :k, :v, :p, CAST(:d AS jsonb))"
                ),
                {"i": str(uuid.uuid4()), "o": org, "s": scan, "k": kind, "v": severity,
                 "p": f"https://{HOST}/", "d": json.dumps(data)},
            )  # fmt: skip
        # A scan that was running is closed so the next one can be started.
        conn.execute(
            text("UPDATE scans SET status='completed' WHERE id=:s AND status='queued'"), {"s": scan}
        )
        conn.execute(text("UPDATE scans SET status=:st WHERE id=:s"), {"st": status, "s": scan})
    return scan


def _verify(api, org, issue, scan):  # type: ignore[no-untyped-def]
    return api.post(f"/v1/organizations/{org}/issues/{issue}/verify", {"after_scan_id": scan})


@pytest.fixture
def seeded_issue(lang_issue, fake_queue, owner_engine):  # type: ignore[no-untyped-def]
    api, org, project, issue, _root = lang_issue
    # The scan that found the issue also visited the page (as every real scan does).
    with owner_engine.begin() as conn:
        scan = conn.execute(text("SELECT scan_id FROM issues WHERE id=:i"), {"i": issue}).scalar()
        conn.execute(
            text(
                "INSERT INTO observations (id, org_id, scan_id, kind, severity, page_url, data) "
                "VALUES (gen_random_uuid(), :o, :s, 'page', 'info', :p, '{\"status\": 200}'::jsonb)"
            ),
            {"o": org, "s": scan, "p": f"https://{HOST}/"},
        )
    return api, org, project, issue, owner_engine


# --- the decision itself ---------------------------------------------------------------------------


def _judge(**over):  # type: ignore[no-untyped-def]
    args = {
        "issue_fingerprint": "fp-1",
        "issue_pages": {f"https://{HOST}/"},
        "before_fingerprints": {"fp-1"},
        "after_status": "completed",
        "after_pages": {f"https://{HOST}/": 200},
        "after_issues": [],
        "after_diagnosed": True,
    }
    return judge(**{**args, **over})


def test_judge_only_says_fixed_when_every_criterion_holds() -> None:
    assert _judge().outcome == "fixed"
    assert _judge(after_issues=[AfterIssue("fp-1", "low", "same")]).outcome == "not_fixed"
    assert _judge(after_status="failed").outcome == "needs_review"
    assert _judge(after_diagnosed=False).outcome == "needs_review"
    assert _judge(after_pages={}).outcome == "needs_review"  # page never re-tested
    assert _judge(after_pages={f"https://{HOST}/": 404}).outcome == "needs_review"
    assert _judge(issue_pages=set()).outcome == "needs_review"
    new_high = AfterIssue("fp-2", "high", "new")
    assert _judge(after_issues=[new_high]).outcome == "needs_review"  # regression
    assert _judge(after_issues=[AfterIssue("fp-3", "info", "note")]).outcome == "fixed"
    # "Still present" wins over everything else, even a partial scan.
    assert (
        _judge(after_pages={}, after_issues=[AfterIssue("fp-1", "low", "x")]).outcome == "not_fixed"
    )


# --- through the API -------------------------------------------------------------------------------


def test_a_defect_that_is_gone_is_reported_fixed_and_stays_decided(seeded_issue) -> None:  # type: ignore[no-untyped-def]
    api, org, project, issue, engine = seeded_issue
    after = _scan(api, org, project, engine, [PAGE_OK])
    response = _verify(api, org, issue, after)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["outcome"] == "fixed" and body["via_kmr_ai"] is False
    assert body["details"]["pages_checked"] and body["details"]["still_present"] is False
    assert api.get(f"/v1/organizations/{org}/issues/{issue}").json()["state"] == "fixed"
    listed = api.get(f"/v1/organizations/{org}/issues/{issue}/verifications").json()
    assert [v["id"] for v in listed] == [body["id"]]
    # Diagnosing the original scan again must not reopen a verified decision.
    with engine.connect() as conn:
        scan = conn.execute(text("SELECT scan_id FROM issues WHERE id=:i"), {"i": issue}).scalar()
    api.post(f"/v1/organizations/{org}/scans/{scan}/diagnose")
    assert api.get(f"/v1/organizations/{org}/issues/{issue}").json()["state"] == "fixed"


def test_a_defect_that_is_still_there_is_not_fixed(seeded_issue) -> None:  # type: ignore[no-untyped-def]
    api, org, project, issue, engine = seeded_issue
    after = _scan(api, org, project, engine, [PAGE_OK, *LANG_OBS])
    body = _verify(api, org, issue, after).json()
    assert body["outcome"] == "not_fixed" and body["details"]["still_present"] is True
    assert api.get(f"/v1/organizations/{org}/issues/{issue}").json()["state"] == "not_fixed"


def test_an_unvisited_page_or_failed_scan_needs_review_not_fixed(seeded_issue) -> None:  # type: ignore[no-untyped-def]
    api, org, project, issue, engine = seeded_issue
    never_visited = _scan(api, org, project, engine, [])  # completed, but saw nothing
    body = _verify(api, org, issue, never_visited).json()
    assert body["outcome"] == "needs_review" and body["details"]["pages_not_retested"]
    broken = _scan(api, org, project, engine, [PAGE_OK], status="failed")
    assert _verify(api, org, issue, broken).json()["outcome"] == "needs_review"
    assert api.get(f"/v1/organizations/{org}/issues/{issue}").json()["state"] == "needs_review"


def test_a_regression_introduced_by_the_fix_is_caught(seeded_issue) -> None:  # type: ignore[no-untyped-def]
    api, org, project, issue, engine = seeded_issue
    after = _scan(api, org, project, engine, [PAGE_OK, JS_ERROR])
    body = _verify(api, org, issue, after).json()
    assert body["outcome"] == "needs_review"
    assert body["details"]["new_issues"] and "new problem" in body["reasons"][0]


def test_a_fix_made_through_kmr_ai_records_the_verified_diff_hash(
    lang_issue, fake_queue, owner_engine
) -> None:  # type: ignore[no-untyped-def]
    api, org, project, issue, repo, plan, approval = _approved(lang_issue)
    with owner_engine.begin() as conn:
        scan = conn.execute(text("SELECT scan_id FROM issues WHERE id=:i"), {"i": issue}).scalar()
        conn.execute(
            text("INSERT INTO observations (id, org_id, scan_id, kind, severity, page_url, data) "
                 "VALUES (gen_random_uuid(), :o, :s, 'page', 'info', :p, '{\"status\": 200}'::jsonb)"),
            {"o": org, "s": scan, "p": f"https://{HOST}/"},
        )  # fmt: skip
    early = _scan(api, org, project, owner_engine, [PAGE_OK])
    assert _apply(api, org, plan, approval["id"]).status_code == 200
    # A scan that started before the branch existed cannot verify it.
    refused = _verify(api, org, issue, early)
    assert refused.status_code == 409 and "before the fix branch" in refused.text
    after = _scan(api, org, project, owner_engine, [PAGE_OK])
    body = _verify(api, org, issue, after).json()
    assert body["outcome"] == "fixed" and body["via_kmr_ai"] is True
    assert body["verified_diff_sha256"] == plan["diff_sha256"]
    assert body["repair_id"] == plan["id"]


def test_verification_preconditions_and_isolation(seeded_issue, new_api, fake_verifier) -> None:  # type: ignore[no-untyped-def]
    api, org, project, issue, engine = seeded_issue
    with engine.connect() as conn:
        original = conn.execute(
            text("SELECT scan_id FROM issues WHERE id=:i"), {"i": issue}
        ).scalar()
    same = _verify(api, org, issue, str(original))
    assert same.status_code == 409  # the original scan cannot verify itself
    assert _verify(api, org, issue, str(uuid.uuid4())).status_code == 404
    after = _scan(api, org, project, engine, [PAGE_OK])
    viewer = registered(new_api)
    api.add_member(org, viewer.email, "viewer")
    assert _verify(viewer, org, issue, after).status_code == 403
    outsider = registered(new_api)
    other_org = outsider.create_org("Other")
    assert _verify(outsider, org, issue, after).status_code == 404
    assert _verify(outsider, other_org, issue, after).status_code == 404
    assert (
        outsider.get(f"/v1/organizations/{other_org}/issues/{issue}/verifications").status_code
        == 404
    )
    # Another project's scan is refused.
    from tests.helpers import verified_project

    org2, project2 = verified_project(api, fake_verifier, host="other.example.com")
    foreign = _scan(api, org2, project2, engine, [PAGE_OK])
    assert _verify(api, org, issue, foreign).status_code in (404, 409)


def test_verification_is_audited_and_the_chain_still_verifies(seeded_issue) -> None:  # type: ignore[no-untyped-def]
    api, org, project, issue, engine = seeded_issue
    _verify(api, org, issue, _scan(api, org, project, engine, [PAGE_OK]))
    events = api.get(f"/v1/organizations/{org}/audit?action=verification.run").json()
    assert events and events[0]["details"]["result"] == "fixed"
    assert api.get(f"/v1/organizations/{org}/audit/verify").json()["ok"] is True
