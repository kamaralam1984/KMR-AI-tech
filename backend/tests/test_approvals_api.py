import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from app.config import get_settings
from app.db.audit_chain import verify_chain  # noqa: F401 - imported to prove the module loads
from tests.helpers import project_base, registered
from tests.test_repair_unit import _git, make_repo
from tests.test_repairs_api import (  # noqa: F401 - fixtures used by name
    PAGE,
    _connect,
    _plan,
    lang_issue,
    roots,
)


def base(org: str) -> str:
    return f"/v1/organizations/{org}"


def _ready(lang_issue, **extra):  # type: ignore[no-untyped-def]
    """A proposed, checked repair on a real git repository. Returns what tests need."""
    api, org, project, issue, root = lang_issue
    repo = make_repo(root, {"index.html": PAGE, "other.txt": "unrelated\n"})
    assert _connect(api, org, project, repo, **extra).status_code == 200
    plan = _plan(api, org, issue).json()
    assert plan["status"] == "proposed", plan
    return api, org, project, issue, repo, plan


def _check(api, org, plan):  # type: ignore[no-untyped-def]
    response = api.post(f"{base(org)}/repairs/{plan['id']}/checks")
    assert response.status_code == 200, response.text
    return response.json()


def _approve(api, org, plan, **body):  # type: ignore[no-untyped-def]
    return api.post(f"{base(org)}/repairs/{plan['id']}/approvals", {"decision": "approved", **body})


def _apply(api, org, plan, approval_id):  # type: ignore[no-untyped-def]
    return api.post(f"{base(org)}/repairs/{plan['id']}/apply", {"approval_id": approval_id})


def _branches(repo: Path) -> set[str]:
    return set(_git(repo, "branch", "--format=%(refname:short)").split())


def _approved(lang_issue):  # type: ignore[no-untyped-def]
    api, org, project, issue, repo, plan = _ready(lang_issue)
    _check(api, org, plan)
    approval = _approve(api, org, plan)
    assert approval.status_code == 200, approval.text
    return api, org, project, issue, repo, plan, approval.json()


def test_the_whole_flow_creates_only_the_approved_branch(lang_issue, owner_engine: Engine) -> None:  # type: ignore[no-untyped-def]
    api, org, _project, _issue, repo, plan = _ready(lang_issue)
    (repo / "index.html").write_text("uncommitted\n")  # must survive
    main, status = _git(repo, "rev-parse", "main"), _git(repo, "status", "--porcelain")
    run = _check(api, org, plan)
    assert run["overall"] == "passed" and run["diff_sha256"] == plan["diff_sha256"]
    assert {r["name"]: r["status"] for r in run["results"]} == {
        "syntax": "passed",
        "javascript_syntax": "skipped",
        "project_check": "skipped",
    }
    approval = _approve(api, org, plan, note="looks right").json()
    assert approval["state"] == "active" and approval["decision"] == "approved"
    assert approval["diff_sha256"] == plan["diff_sha256"] and len(approval["bound_hash"]) == 64
    assert _branches(repo) == {"main"}  # approving writes nothing
    applied = _apply(api, org, plan, approval["id"])
    assert applied.status_code == 200, applied.text
    out = applied.json()
    assert out["status"] == "applied" and out["branch_name"] == plan["branch_name"]
    assert "default branch was not changed" in out["message"].lower()
    # Exactly one new branch, on top of the approved base, holding exactly the approved content.
    assert _branches(repo) == {"main", plan["branch_name"]}
    assert _git(repo, "rev-parse", "main") == main == plan["base_sha"]
    assert _git(repo, "status", "--porcelain") == status
    assert (repo / "index.html").read_text() == "uncommitted\n"
    assert _git(repo, "rev-parse", f"{out['commit_sha']}^") == plan["base_sha"]
    committed = _git(repo, "show", f"{out['commit_sha']}:index.html")
    assert '<html lang="en">' in committed
    diff = _git(repo, "diff", plan["base_sha"], out["commit_sha"], "--stat")
    assert "index.html" in diff and "other.txt" not in diff
    # The approval is spent: the same approval cannot be used again.
    again = _apply(api, org, plan, approval["id"])
    assert again.status_code == 409 and "used" in again.text
    assert _branches(repo) == {"main", plan["branch_name"]}
    state = api.get(f"{base(org)}/repairs/{plan['id']}/approvals").json()[0]["state"]
    assert state == "used"
    with owner_engine.connect() as conn:
        rows = conn.execute(text("SELECT status FROM repair_applications")).all()
    assert [r.status for r in rows] == ["applied"]


def test_nothing_can_be_approved_before_the_checks_pass(lang_issue) -> None:  # type: ignore[no-untyped-def]
    api, org, _p, _i, _repo, plan = _ready(lang_issue)
    early = _approve(api, org, plan)
    assert early.status_code == 409 and "checks" in early.text.lower()
    # A change that fails its checks can never be approved or applied.
    broken = _plan(
        api, org, plan["issue_id"], {"changes": {"index.html": PAGE + "</div>\n"}}
    ).json()
    run = _check(api, org, broken)
    assert run["overall"] == "failed"
    assert {r["name"]: r["status"] for r in run["results"]}["syntax"] == "failed"
    denied = _approve(api, org, broken, acknowledge_incomplete_checks=True)
    assert denied.status_code == 409 and "failed" in denied.text


def test_unavailable_checks_need_an_explicit_acknowledgement(lang_issue, api_app) -> None:  # type: ignore[no-untyped-def]
    api, org, _p, _i, repo, plan = _ready(lang_issue, check_command="true")
    broken_sandbox = get_settings().model_copy(
        update={"check_image": "kmr-image-that-is-missing:0"}
    )
    api_app.dependency_overrides[get_settings] = lambda: broken_sandbox
    try:
        run = _check(api, org, plan)
    finally:
        api_app.dependency_overrides.pop(get_settings, None)
    statuses = {r["name"]: r["status"] for r in run["results"]}
    assert statuses["project_check"] == "unavailable" and run["overall"] == "unavailable"
    refused = _approve(api, org, plan)
    assert refused.status_code == 409 and "accept" in refused.text
    accepted = _approve(api, org, plan, acknowledge_incomplete_checks=True).json()
    assert accepted["acknowledged_incomplete_checks"] is True
    assert _apply(api, org, plan, accepted["id"]).status_code == 200
    assert plan["branch_name"] in _branches(repo)


def test_a_real_project_check_runs_in_the_sandbox_and_can_block(lang_issue) -> None:  # type: ignore[no-untyped-def]
    api, org, _p, _i, _repo, plan = _ready(
        lang_issue, check_command="grep -q 'lang=\"en\"' index.html"
    )
    assert _check(api, org, plan)["overall"] == "passed"  # the check saw the PATCHED copy
    api.request("PUT", f"{project_base(org, plan['project_id'])}/repository", {
        "provider": "local_git", "location": _repo.as_posix(), "default_branch": "main",
        "check_command": "grep -q 'lang=\"zz\"' index.html"})  # fmt: skip
    failing = _check(api, org, plan)
    assert failing["overall"] == "failed"
    assert _approve(api, org, plan).status_code == 409


def test_only_admins_decide_and_apply(lang_issue, new_api) -> None:  # type: ignore[no-untyped-def]
    api, org, _p, _i, _repo, plan = _ready(lang_issue)
    _check(api, org, plan)
    member = registered(new_api)
    assert api.add_member(org, member.email, "member").status_code == 201
    assert _approve(member, org, plan).status_code == 403
    approval = _approve(api, org, plan).json()
    assert _apply(member, org, plan, approval["id"]).status_code == 403
    assert member.post(f"{base(org)}/approvals/{approval['id']}/revoke", {}).status_code == 403
    assert member.get(f"{base(org)}/repairs/{plan['id']}/approvals").status_code == 200
    assert member.get(f"{base(org)}/audit").status_code == 403
    assert member.get(f"{base(org)}/audit/verify").status_code == 403


def test_a_rejection_is_recorded_and_can_never_be_applied(lang_issue) -> None:  # type: ignore[no-untyped-def]
    api, org, _p, _i, repo, plan = _ready(lang_issue)
    _check(api, org, plan)
    rejected = api.post(
        f"{base(org)}/repairs/{plan['id']}/approvals", {"decision": "rejected", "note": "no"}
    ).json()
    assert rejected["state"] == "rejected"
    response = _apply(api, org, plan, rejected["id"])
    assert response.status_code == 409 and _branches(repo) == {"main"}


def test_expired_revoked_and_superseded_approvals_are_refused(
    lang_issue, owner_engine: Engine
) -> None:  # type: ignore[no-untyped-def]
    api, org, _p, issue, repo, plan, approval = _approved(lang_issue)
    with owner_engine.begin() as conn:
        conn.execute(text("UPDATE approvals SET expires_at = :t WHERE id = :i"),
                     {"t": datetime.now(UTC) - timedelta(seconds=1), "i": approval["id"]})  # fmt: skip
    expired = _apply(api, org, plan, approval["id"])
    assert expired.status_code == 409 and "expired" in expired.text
    # Revoked.
    fresh = _approve(api, org, plan).json()
    revoked = api.post(
        f"{base(org)}/approvals/{fresh['id']}/revoke", {"reason": "changed my mind"}
    ).json()
    assert revoked["state"] == "revoked" and revoked["revoked_reason"] == "changed my mind"
    refused = _apply(api, org, plan, fresh["id"])
    assert refused.status_code == 409 and "revoked" in refused.text
    assert api.post(f"{base(org)}/approvals/{fresh['id']}/revoke", {}).status_code == 409
    # A new plan for the same issue invalidates approvals of the old one.
    third = _approve(api, org, plan).json()
    newer = _plan(api, org, issue).json()
    assert newer["id"] != plan["id"]
    states = {
        a["id"]: a["state"] for a in api.get(f"{base(org)}/repairs/{plan['id']}/approvals").json()
    }
    assert states[third["id"]] == "invalidated"
    gone = _apply(api, org, plan, third["id"])
    assert gone.status_code == 409 and "invalidated" in gone.text
    assert _branches(repo) == {"main"}


def test_an_approval_only_works_for_the_repair_it_was_made_for(lang_issue) -> None:  # type: ignore[no-untyped-def]
    api, org, _p, issue, repo, plan, approval = _approved(lang_issue)
    other = _plan(
        api, org, issue, {"changes": {"index.html": PAGE.replace("<html>", '<html lang="hi">')}}
    ).json()
    _check(api, org, other)
    assert _apply(api, org, other, approval["id"]).status_code == 404
    assert _branches(repo) == {"main"}


@pytest.mark.parametrize(
    ("column", "value", "needle"),
    [
        ("branch_name", "kmr-ai/other", "changed after approval"),
        ("base_sha", "0" * 40, "changed after approval"),
    ],
)
def test_tampering_with_the_plan_after_approval_blocks_the_apply(
    lang_issue, owner_engine: Engine, column: str, value: str, needle: str
) -> None:  # type: ignore[no-untyped-def]
    api, org, _p, _i, repo, plan, approval = _approved(lang_issue)
    with owner_engine.begin() as conn:
        conn.execute(text(f"UPDATE repair_plans SET {column} = :v WHERE id = :i"),  # noqa: S608
                     {"v": value, "i": plan["id"]})  # fmt: skip
    response = _apply(api, org, plan, approval["id"])
    assert response.status_code == 409 and needle in response.text
    assert _branches(repo) == {"main"}


def test_tampering_with_the_stored_change_is_caught(lang_issue, owner_engine: Engine) -> None:  # type: ignore[no-untyped-def]
    api, org, _p, _i, repo, plan, approval = _approved(lang_issue)
    with owner_engine.begin() as conn:  # swap the diff text but keep its stored hash
        conn.execute(
            text("UPDATE repair_plans SET diff = diff || '+evil' WHERE id = :i"), {"i": plan["id"]}
        )
    r1 = _apply(api, org, plan, approval["id"])
    assert r1.status_code == 409 and "does not match" in r1.text
    with owner_engine.begin() as conn:  # restore the diff, then tamper with the files to commit
        conn.execute(
            text("UPDATE repair_plans SET diff = replace(diff, '+evil', '') WHERE id = :i"),
            {"i": plan["id"]},
        )
        conn.execute(text("UPDATE repair_plans SET changes = jsonb_set(changes, '{0,new_content}', "
                          "to_jsonb('<html lang=\"en\"><!-- AKIAIOSFODNN7EXAMPLE --></html>'::text)) "
                          "WHERE id = :i"), {"i": plan["id"]})  # fmt: skip
    r2 = _apply(api, org, plan, approval["id"])
    assert r2.status_code == 409
    assert _branches(repo) == {"main"}


def test_apply_notices_when_the_target_file_changed_since_planning(lang_issue) -> None:  # type: ignore[no-untyped-def]
    api, org, _p, _i, repo, plan, approval = _approved(lang_issue)
    (repo / "index.html").write_text(PAGE + "<!-- someone edited -->\n")
    _git(repo, "commit", "-qam", "edit the same file")
    response = _apply(api, org, plan, approval["id"])
    assert response.status_code == 409 and "changed on main" in response.text
    assert _branches(repo) == {"main"}


def test_apply_still_works_when_main_moved_but_the_target_is_untouched(lang_issue) -> None:  # type: ignore[no-untyped-def]
    api, org, _p, _i, repo, plan, approval = _approved(lang_issue)
    (repo / "other.txt").write_text("changed elsewhere\n")
    _git(repo, "commit", "-qam", "unrelated change")
    moved = _git(repo, "rev-parse", "main")
    response = _apply(api, org, plan, approval["id"])
    assert response.status_code == 200, response.text
    assert _git(repo, "rev-parse", "main") == moved  # main untouched
    assert _git(repo, "rev-parse", f"{plan['branch_name']}^") == plan["base_sha"]


def test_an_existing_branch_is_never_overwritten(lang_issue) -> None:  # type: ignore[no-untyped-def]
    api, org, _p, _i, repo, plan, approval = _approved(lang_issue)
    _git(repo, "branch", plan["branch_name"], "main")
    before = _git(repo, "rev-parse", plan["branch_name"])
    response = _apply(api, org, plan, approval["id"])
    assert response.status_code == 409 and "already exists" in response.text
    assert _git(repo, "rev-parse", plan["branch_name"]) == before


def test_other_organizations_cannot_touch_approvals(lang_issue, new_api) -> None:  # type: ignore[no-untyped-def]
    api, org, _p, _i, repo, plan, approval = _approved(lang_issue)
    other = registered(new_api)
    other_org = other.create_org("Other")
    for call in (
        lambda: _approve(other, other_org, plan),
        lambda: _apply(other, other_org, plan, approval["id"]),
        lambda: other.post(f"{base(other_org)}/approvals/{approval['id']}/revoke", {}),
        lambda: other.get(f"{base(other_org)}/repairs/{plan['id']}/approvals"),
        lambda: other.post(f"{base(other_org)}/repairs/{plan['id']}/checks"),
    ):
        assert call().status_code == 404
    assert _apply(other, org, plan, approval["id"]).status_code == 404  # not even a member there
    assert _branches(repo) == {"main"}


def test_the_app_role_cannot_rewrite_what_an_approval_is_bound_to(
    lang_issue, app_sync_engine: Engine
) -> None:  # type: ignore[no-untyped-def]
    api, org, _p, _i, _repo, plan, approval = _approved(lang_issue)
    for column, value in (
        ("diff_sha256", "a" * 64),
        ("bound_hash", "b" * 64),
        ("expires_at", "2099-01-01"),
    ):
        with pytest.raises(DBAPIError) as info, app_sync_engine.begin() as conn:
            conn.execute(text("SELECT set_config('app.current_org_id', :o, true)"), {"o": org})
            conn.execute(text(f"UPDATE approvals SET {column} = :v WHERE id = :i"),  # noqa: S608
                         {"v": value, "i": approval["id"]})  # fmt: skip
        assert "permission denied" in str(info.value)


def test_the_app_role_can_only_change_a_plans_status(lang_issue, app_sync_engine: Engine) -> None:  # type: ignore[no-untyped-def]
    api, org, _p, _i, _repo, plan = _ready(lang_issue)
    for column, value in (
        ("diff", "x"),
        ("diff_sha256", "a" * 64),
        ("base_sha", "b" * 40),
        ("changes", "[]"),
    ):
        with pytest.raises(DBAPIError) as info, app_sync_engine.begin() as conn:
            conn.execute(text("SELECT set_config('app.current_org_id', :o, true)"), {"o": org})
            conn.execute(text(f"UPDATE repair_plans SET {column} = {'CAST(:v AS jsonb)' if column == 'changes' else ':v'} WHERE id = :i"),
                         {"v": value, "i": plan["id"]})  # fmt: skip
        assert "permission denied" in str(info.value), column
    with app_sync_engine.begin() as conn:  # status itself may change (superseding an old plan)
        conn.execute(text("SELECT set_config('app.current_org_id', :o, true)"), {"o": org})
        done = conn.execute(
            text("UPDATE repair_plans SET status = 'superseded' WHERE id = :i"), {"i": plan["id"]}
        )
        assert done.rowcount == 1


def test_there_is_no_route_that_merges_deploys_migrates_or_deletes(api_app) -> None:  # type: ignore[no-untyped-def]
    forbidden = ("merge", "deploy", "migrat", "force", "delete-branch", "push", "release")
    paths = [getattr(r, "path", "") for r in api_app.routes]
    assert [p for p in paths if any(word in p.lower() for word in forbidden)] == []
    from app.repair.github import GitHubRepo
    from app.repair.repo import LocalGitRepo

    for cls in (LocalGitRepo, GitHubRepo):
        names = {n for n in dir(cls) if not n.startswith("_")}
        assert not {n for n in names if any(w in n for w in ("merge", "delete", "force", "reset"))}


# --- audit chain ---------------------------------------------------------------------------------


def test_the_audit_chain_verifies_and_is_scoped_per_organization(lang_issue, new_api) -> None:  # type: ignore[no-untyped-def]
    api, org, _p, _i, _repo, plan, approval = _approved(lang_issue)
    _apply(api, org, plan, approval["id"])
    report = api.get(f"{base(org)}/audit/verify").json()
    assert report["ok"] is True and report["checked"] >= 8 and report["unchained"] == 0
    assert len(report["tip"]) == 64
    events = api.get(f"{base(org)}/audit?limit=200").json()
    seqs = sorted(e["chain_seq"] for e in events if e["chain_seq"])
    assert seqs == list(range(1, len(seqs) + 1))  # no gaps
    actions = {e["action"] for e in events}
    assert {
        "repository.connect",
        "repair.plan",
        "repair.checks",
        "approval.approved",
        "repair.apply",
    } <= actions
    assert api.get(f"{base(org)}/audit?action=repair.apply").json()[0]["outcome"] == "success"
    other = registered(new_api)
    other_org = other.create_org("Other")
    assert other.get(f"{base(other_org)}/audit/verify").json()["ok"] is True
    assert other.get(f"{base(org)}/audit").status_code == 404


def test_editing_deleting_or_reordering_audit_events_is_detected(
    lang_issue, owner_engine: Engine
) -> None:  # type: ignore[no-untyped-def]
    api, org, _p, _i, _repo, plan, approval = _approved(lang_issue)
    assert api.get(f"{base(org)}/audit/verify").json()["ok"] is True
    with owner_engine.begin() as conn:
        target = conn.execute(
            text(
                "SELECT id, chain_seq FROM audit_events WHERE org_id = :o AND action = 'repair.checks'"
            ),
            {"o": org},
        ).one()
        conn.execute(text("UPDATE audit_events SET details = jsonb_set(details, '{overall}', '\"passed-ish\"') "
                          "WHERE id = :i"), {"i": target.id})  # fmt: skip
    edited = api.get(f"{base(org)}/audit/verify").json()
    assert edited["ok"] is False and edited["reason"] == "event_modified"
    assert edited["broken_at"] == target.chain_seq
    with owner_engine.begin() as conn:  # repair the edit by deleting a different event instead
        conn.execute(text("UPDATE audit_events SET details = jsonb_set(details, '{overall}', '\"passed\"') WHERE id = :i"),
                     {"i": target.id})  # fmt: skip
        assert conn.execute(
            text("SELECT count(*) FROM audit_events WHERE org_id = :o"), {"o": org}
        ).scalar()
    assert api.get(f"{base(org)}/audit/verify").json()["ok"] is True
    with owner_engine.begin() as conn:
        conn.execute(
            text("DELETE FROM audit_events WHERE org_id = :o AND chain_seq = 3"), {"o": org}
        )
    deleted = api.get(f"{base(org)}/audit/verify").json()
    assert (
        deleted["ok"] is False
        and deleted["reason"] == "missing_event"
        and deleted["broken_at"] == 3
    )


def test_the_app_role_cannot_edit_or_delete_audit_events(
    lang_issue, app_sync_engine: Engine
) -> None:  # type: ignore[no-untyped-def]
    api, org, *_ = _ready(lang_issue)
    for statement in ("UPDATE audit_events SET action = 'x'", "DELETE FROM audit_events"):
        with pytest.raises(DBAPIError) as info, app_sync_engine.begin() as conn:
            conn.execute(text("SELECT set_config('app.current_org_id', :o, true)"), {"o": org})
            conn.execute(text(statement))
        assert "permission denied" in str(info.value)


def test_audit_details_never_hold_file_contents_or_tokens(lang_issue) -> None:  # type: ignore[no-untyped-def]
    api, org, _p, _i, _repo, plan, approval = _approved(lang_issue)
    _apply(api, org, plan, approval["id"])
    blob = json.dumps(api.get(f"{base(org)}/audit?limit=200").json())
    assert "<html" not in blob and "ghp_" not in blob and "new_content" not in blob
    assert uuid.UUID(plan["id"]).hex  # ids are fine to log


def test_checks_that_fail_after_approval_still_block_the_apply(
    lang_issue, owner_engine: Engine
) -> None:  # type: ignore[no-untyped-def]
    api, org, _p, _i, repo, plan, approval = _approved(lang_issue)
    with owner_engine.begin() as conn:  # a later check run on the same change comes back failed
        conn.execute(
            text(
                "INSERT INTO check_runs (org_id, repair_id, requested_by, diff_sha256, base_sha, overall, results, created_at) "
                "SELECT org_id, repair_id, requested_by, diff_sha256, base_sha, 'failed', '[]'::jsonb, now() + interval '1 second' "
                "FROM check_runs WHERE repair_id = :r LIMIT 1"
            ),
            {"r": plan["id"]},
        )
    response = _apply(api, org, plan, approval["id"])
    assert response.status_code == 409 and "failed" in response.text
    assert _branches(repo) == {"main"}


def test_a_concurrent_second_apply_cannot_reuse_the_approval(
    lang_issue, owner_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    """Another request wins the race between 'verified' and 'claimed': this one must lose."""
    import app.routes.approvals as routes

    api, org, _p, _i, repo, plan, approval = _approved(lang_issue)
    real = routes.verify_for_apply  # type: ignore[attr-defined]

    async def verify_then_lose_the_race(**kwargs):  # type: ignore[no-untyped-def]
        result = await real(**kwargs)
        with owner_engine.begin() as conn:
            conn.execute(
                text("UPDATE approvals SET consumed_at = now() WHERE id = :i"),
                {"i": approval["id"]},
            )
        return result

    monkeypatch.setattr(routes, "verify_for_apply", verify_then_lose_the_race, raising=True)
    response = _apply(api, org, plan, approval["id"])
    assert response.status_code == 409 and "already used" in response.text
    assert _branches(repo) == {"main"}
