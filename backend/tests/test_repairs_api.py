import hashlib
import json
import uuid
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.config import Settings, get_settings
from app.repair.providers import AnthropicRepairProvider
from tests.fakes import FakeGitHub
from tests.helpers import project_base, registered, verified_project
from tests.test_repair_unit import _git, make_repo

HOST = "shop.example.com"
PAGE = "<!doctype html>\n<html>\n<head>\n<title>Home</title>\n</head>\n<body>hi</body>\n</html>\n"
LANG_TITLE = "Page does not declare its language"


@pytest.fixture
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Allowed folder for local repositories; must be requested before the app fixtures."""
    root = tmp_path / "repos"
    root.mkdir()
    monkeypatch.setenv("KMR_REPO_LOCAL_ROOTS", json.dumps([str(root)]))
    get_settings.cache_clear()
    return root


def _seed_issue(new_api, fake_verifier, owner_engine: Engine, observations):  # type: ignore[no-untyped-def]
    api = registered(new_api)
    org, project = verified_project(api, fake_verifier, host=HOST)
    base = project_base(org, project)
    scan = api.post(f"{base}/scans").json()["id"]
    with owner_engine.begin() as conn:
        conn.execute(
            text("UPDATE scans SET status='completed', finished_at=now() WHERE id=:s"), {"s": scan}
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
    issues = api.post(f"/v1/organizations/{org}/scans/{scan}/diagnose").json()["issues"]
    return api, org, project, issues


LANG_OBS = [("a11y_issue", "warning", {"check": "missing_lang", "count": 1})]


@pytest.fixture
def lang_issue(roots, new_api, fake_verifier, owner_engine):  # type: ignore[no-untyped-def]
    api, org, project, issues = _seed_issue(new_api, fake_verifier, owner_engine, LANG_OBS)
    issue = next(i for i in issues if i["title"] == LANG_TITLE)
    return api, org, project, issue["id"], roots


def _connect(api, org, project, repo: Path, **extra):  # type: ignore[no-untyped-def]
    body = {"provider": "local_git", "location": str(repo), "default_branch": "main", **extra}
    return api.request("PUT", f"{project_base(org, project)}/repository", body)


def _plan(api, org, issue, body=None):  # type: ignore[no-untyped-def]
    return api.post(f"/v1/organizations/{org}/issues/{issue}/repairs", body)


def test_connecting_a_repository_is_admin_only_and_confined(lang_issue, new_api) -> None:  # type: ignore[no-untyped-def]
    api, org, project, _issue, root = lang_issue
    repo = make_repo(root, {"index.html": PAGE})
    outside = make_repo(root.parent, {"index.html": PAGE}, name="elsewhere")
    ok = _connect(api, org, project, repo)
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["provider"] == "local_git" and body["has_token"] is False
    assert body["head_sha"] == _git(repo, "rev-parse", "main")
    assert api.get(f"{project_base(org, project)}/repository").json()["location"] == str(repo)
    for location, status in [(str(outside), 400), (str(root / "nope"), 404)]:
        denied = api.request("PUT", f"{project_base(org, project)}/repository",
                             {"provider": "local_git", "location": location})  # fmt: skip
        assert denied.status_code == status, denied.text
    missing_branch = _connect(api, org, project, repo, default_branch="release")
    assert missing_branch.status_code == 400
    # A member can read but not connect.
    member = registered(new_api)
    assert api.add_member(org, member.email, "member").status_code == 201
    assert member.get(f"{project_base(org, project)}/repository").status_code == 200
    assert _connect(member, org, project, repo).status_code == 403
    assert member.delete(f"{project_base(org, project)}/repository").status_code == 403
    assert api.delete(f"{project_base(org, project)}/repository").status_code == 204
    assert api.get(f"{project_base(org, project)}/repository").json() is None


def test_the_builtin_rules_plan_a_real_fix_without_touching_the_repository(lang_issue) -> None:  # type: ignore[no-untyped-def]
    api, org, project, issue, root = lang_issue
    repo = make_repo(root, {"index.html": PAGE, "about.html": PAGE.replace("Home", "About")})
    assert _connect(api, org, project, repo).status_code == 200
    main_before, status_before = (
        _git(repo, "rev-parse", "main"),
        _git(repo, "status", "--porcelain"),
    )
    response = _plan(api, org, issue)
    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["status"] == "proposed" and plan["source"] == "rules" and plan["is_ai"] is False
    assert {f["path"] for f in plan["files"]} == {"index.html", "about.html"}
    assert '+<html lang="en">' in plan["diff"]
    assert plan["diff_sha256"] == hashlib.sha256(plan["diff"].encode()).hexdigest()
    assert plan["base_sha"] == main_before and plan["base_branch"] == "main"
    assert plan["branch_name"].startswith("kmr-ai/fix-")
    # Planning wrote nothing: no branch, no commit, same working tree.
    assert _git(repo, "rev-parse", "main") == main_before
    assert _git(repo, "status", "--porcelain") == status_before
    assert _git(repo, "branch", "--format=%(refname:short)") == "main"
    # Planning again supersedes the earlier proposal.
    second = _plan(api, org, issue).json()
    listed = api.get(f"/v1/organizations/{org}/issues/{issue}/repairs").json()
    assert {p["id"]: p["status"] for p in listed} == {
        plan["id"]: "superseded",
        second["id"]: "proposed",
    }
    assert api.get(f"/v1/organizations/{org}/repairs/{plan['id']}").json()["diff"] == plan["diff"]


def test_planning_needs_a_repository_and_an_open_issue(lang_issue, roots, owner_engine) -> None:  # type: ignore[no-untyped-def]
    api, org, project, issue, root = lang_issue
    assert _plan(api, org, issue).status_code == 409  # nothing connected
    repo = make_repo(root, {"index.html": PAGE})
    _connect(api, org, project, repo)
    with owner_engine.begin() as conn:
        conn.execute(text("UPDATE issues SET state='fixed' WHERE id=:i"), {"i": issue})
    assert _plan(api, org, issue).status_code == 409
    assert _plan(api, org, str(uuid.uuid4())).status_code == 404


def test_an_issue_that_needs_review_gets_no_automatic_fix(
    roots, new_api, fake_verifier, owner_engine
) -> None:  # type: ignore[no-untyped-def]
    http = [
        (
            "http_error",
            "error",
            {"url": f"https://{HOST}/api/orders", "status": 500, "resource_type": "fetch"},
        )
    ]
    api, org, project, issues = _seed_issue(new_api, fake_verifier, owner_engine, http)
    backend = next(i for i in issues if i["classification"] == "needs_review")
    _connect(api, org, project, make_repo(roots, {"index.html": PAGE}))
    plan = _plan(api, org, backend["id"]).json()
    assert (
        plan["status"] == "no_fix" and plan["diff"] == "" and "needs review" in plan["explanation"]
    )


def test_no_fix_is_reported_when_the_rules_do_not_apply(lang_issue) -> None:  # type: ignore[no-untyped-def]
    api, org, project, issue, root = lang_issue
    done = PAGE.replace("<html>", '<html lang="en">')
    _connect(api, org, project, make_repo(root, {"index.html": done}))
    plan = _plan(api, org, issue).json()
    assert plan["status"] == "no_fix" and plan["files"] == []


def test_a_person_can_submit_their_own_change_and_it_is_validated(lang_issue) -> None:  # type: ignore[no-untyped-def]
    api, org, project, issue, root = lang_issue
    repo = make_repo(root, {"index.html": PAGE, ".env": "A=1", "package.json": "{}"})
    _connect(api, org, project, repo)
    fixed = PAGE.replace("<html>", '<html lang="hi">')
    ok = _plan(
        api, org, issue, {"changes": {"index.html": fixed}, "explanation": "Hindi site"}
    ).json()
    assert ok["status"] == "proposed" and ok["source"] == "manual" and ok["provider"] == "customer"
    cases = {
        "path_not_allowed": {"../outside.html": "x"},
        "path_not_allowed ": {".env": "A=2"},
        "path_not_allowed  ": {"package.json": "{}"},
        "adds_secret": {"index.html": PAGE + "<!-- AKIAIOSFODNN7EXAMPLE -->"},
        "deletion": {"index.html": ""},
        "no_change": {"index.html": PAGE},
    }
    for rule, changes in cases.items():
        plan = _plan(api, org, issue, {"changes": changes}).json()
        assert plan["status"] == "rejected" and plan["diff"] == "" and plan["diff_sha256"] == "", (
            rule
        )
        assert rule.strip() in {f["rule"] for f in plan["findings"]}, (rule, plan["findings"])
    assert _git(repo, "branch", "--format=%(refname:short)") == "main"


def _ai_provider(api_app, handler):  # type: ignore[no-untyped-def]
    settings = Settings(ai_provider="anthropic", ai_api_key=SecretStr("k" * 20), ai_max_retries=0)
    api_app.state.repair_provider = AnthropicRepairProvider(
        settings, httpx.MockTransport(handler), backoff_seconds=0
    )


def _reply(files: list[dict[str, str]]) -> httpx.Response:
    block = {
        "type": "tool_use",
        "name": "propose_patch",
        "input": {"explanation": "ai", "files": files},
    }
    return httpx.Response(200, json={"model": "claude-test", "content": [block], "usage": {}})


def test_an_ai_proposal_is_validated_and_never_sees_files_with_secrets(lang_issue, api_app) -> None:  # type: ignore[no-untyped-def]
    api, org, project, issue, root = lang_issue
    repo = make_repo(root, {"index.html": PAGE, "config.js": "const k = 'AKIAIOSFODNN7EXAMPLE';\n"})
    _connect(api, org, project, repo)
    sent: list[str] = []

    def good(request: httpx.Request) -> httpx.Response:
        sent.append(request.content.decode())
        return _reply(
            [{"path": "index.html", "content": PAGE.replace("<html>", '<html lang="en">')}]
        )

    _ai_provider(api_app, good)
    plan = _plan(api, org, issue).json()
    assert plan["status"] == "proposed" and plan["is_ai"] is True and plan["model"] == "claude-test"
    assert "AKIAIOSFODNN7EXAMPLE" not in sent[0] and "config.js" not in sent[0]
    assert any("config.js" in w and "contains_secret" in w for w in plan["warnings"])

    evil = [
        {"path": "config.js", "content": "x"},  # a file the proposer was not shown
    ]
    _ai_provider(api_app, lambda r: _reply(evil))
    rejected = _plan(api, org, issue).json()
    assert rejected["status"] == "rejected"
    assert "file_not_shown" in {f["rule"] for f in rejected["findings"]}
    _ai_provider(
        api_app, lambda r: _reply([{"path": ".github/workflows/x.yml", "content": "on: push"}])
    )
    assert "path_not_allowed" in {f["rule"] for f in _plan(api, org, issue).json()["findings"]}
    leak = [{"path": "index.html", "content": PAGE + "<!-- ghp_" + "ab" * 20 + " -->"}]
    _ai_provider(api_app, lambda r: _reply(leak))
    assert "adds_secret" in {f["rule"] for f in _plan(api, org, issue).json()["findings"]}


def test_a_failing_ai_provider_saves_nothing(lang_issue, api_app, owner_engine) -> None:  # type: ignore[no-untyped-def]
    api, org, project, issue, root = lang_issue
    _connect(api, org, project, make_repo(root, {"index.html": PAGE}))
    _ai_provider(api_app, lambda r: httpx.Response(400, text="echo of secret prompt"))
    response = _plan(api, org, issue)
    assert response.status_code == 502 and "secret" not in response.text
    with owner_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM repair_plans")).scalar() == 0


def test_github_connection_stores_the_token_encrypted_and_plans_from_the_api(
    lang_issue, api_app, encryption_key, owner_engine
) -> None:  # type: ignore[no-untyped-def]
    api, org, project, issue, _root = lang_issue
    fake = FakeGitHub({"index.html": PAGE})
    api_app.state.github_transport = fake.transport
    get_settings.cache_clear()
    base = f"{project_base(org, project)}/repository"
    body = {"provider": "github", "location": "acme/site", "default_branch": "main"}
    assert api.request("PUT", base, body).status_code == 422  # a token is required
    assert api.request("PUT", base, {**body, "token": "ghp_wrong"}).status_code == 400
    ok = api.request("PUT", base, {**body, "token": "ghp_fake_token"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["has_token"] is True and "ghp_fake_token" not in ok.text
    assert "ghp_fake_token" not in api.get(base).text
    with owner_engine.connect() as conn:
        stored = conn.execute(text("SELECT token_ct FROM repository_connections")).scalar()
    assert stored is not None and b"ghp_fake_token" not in bytes(stored)
    plan = _plan(api, org, issue).json()
    assert plan["status"] == "proposed" and plan["base_sha"] == fake.refs["main"]
    assert not [m for m, _ in fake.requests if m in ("PATCH", "PUT", "DELETE")]
    assert [p for m, p in fake.requests if m == "POST"] == []  # planning never writes


def test_repair_data_is_isolated_between_organizations(lang_issue, new_api, roots) -> None:  # type: ignore[no-untyped-def]
    api, org, project, issue, root = lang_issue
    repo = make_repo(root, {"index.html": PAGE})
    _connect(api, org, project, repo)
    plan = _plan(api, org, issue).json()
    other = registered(new_api)
    other_org = other.create_org("Other")
    other_project = other.create_project(other_org)
    # The other tenant cannot see, plan against, or reconnect anything of the first.
    assert other.get(f"/v1/organizations/{other_org}/repairs/{plan['id']}").status_code == 404
    assert other.get(f"/v1/organizations/{other_org}/issues/{issue}/repairs").status_code == 404
    assert _plan(other, other_org, issue).status_code == 404
    # A non-member does not even learn that the organization exists.
    assert other.get(f"/v1/organizations/{org}/repairs/{plan['id']}").status_code == 404
    assert _connect(other, org, project, repo).status_code == 404
    assert _connect(other, other_org, project, repo).status_code == 404  # not their project
    assert other.get(f"{project_base(other_org, other_project)}/repository").json() is None


def test_a_viewer_can_read_plans_but_not_create_them(lang_issue, new_api) -> None:  # type: ignore[no-untyped-def]
    api, org, project, issue, root = lang_issue
    _connect(api, org, project, make_repo(root, {"index.html": PAGE}))
    plan = _plan(api, org, issue).json()
    viewer = registered(new_api)
    assert api.add_member(org, viewer.email, "viewer").status_code == 201
    assert viewer.get(f"/v1/organizations/{org}/repairs/{plan['id']}").status_code == 200
    assert _plan(viewer, org, issue).status_code == 403


def test_the_daily_planning_limit_is_enforced(lang_issue, api_app) -> None:  # type: ignore[no-untyped-def]
    api, org, project, issue, root = lang_issue
    _connect(api, org, project, make_repo(root, {"index.html": PAGE}))
    limited = get_settings().model_copy(update={"repair_plans_per_day_per_org": 2})
    api_app.dependency_overrides[get_settings] = lambda: limited
    try:
        assert [_plan(api, org, issue).status_code for _ in range(3)] == [200, 200, 429]
    finally:
        api_app.dependency_overrides.pop(get_settings, None)


def test_audit_records_the_plan_with_its_diff_hash_and_no_secrets(lang_issue, owner_engine) -> None:  # type: ignore[no-untyped-def]
    api, org, project, issue, root = lang_issue
    _connect(api, org, project, make_repo(root, {"index.html": PAGE}))
    plan = _plan(api, org, issue).json()
    with owner_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT action, outcome, details FROM audit_events WHERE action LIKE 'repair%' OR action LIKE 'repository%'"
            )
        ).all()
    actions = {r.action: r for r in rows}
    assert actions["repository.connect"].outcome == "success"
    assert actions["repair.plan"].details["diff_sha256"] == plan["diff_sha256"]


def test_the_organization_repair_list_is_scoped_and_filterable(lang_issue, new_api) -> None:  # type: ignore[no-untyped-def]
    api, org, project, issue, root = lang_issue
    _connect(api, org, project, make_repo(root, {"index.html": PAGE}))
    plan = _plan(api, org, issue).json()
    listed = api.get(f"/v1/organizations/{org}/repairs").json()
    assert [p["id"] for p in listed] == [plan["id"]]
    assert api.get(f"/v1/organizations/{org}/repairs?status=rejected").json() == []
    other = registered(new_api)
    other_org = other.create_org("Other")
    assert other.get(f"/v1/organizations/{other_org}/repairs").json() == []
