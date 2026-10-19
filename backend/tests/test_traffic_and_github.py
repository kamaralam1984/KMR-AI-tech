"""12A customer-supplied traffic and 12B sending a finding to GitHub as an issue."""

import hashlib
import json

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.config import get_settings
from app.diagnosis.impact import rank_issue
from app.diagnosis.traffic import TrafficFormatError, TrafficIndex, normalize_path, parse_csv
from tests.fakes import FakeGitHub
from tests.helpers import project_base, registered
from tests.test_repairs_api import PAGE, lang_issue, roots  # noqa: F401 - fixtures


def _week() -> dict[str, str]:
    return {"period_start": "2026-10-01", "period_end": "2026-10-07"}


def upload(api, org, project, csv_text: str, **over):  # type: ignore[no-untyped-def]
    body = {"source_label": "Analytics export", **_week(), "csv": csv_text, **over}
    return api.request("PUT", f"{project_base(org, project)}/traffic", body)


# ------------------------------------------------------------------------------- parsing


def test_paths_are_normalised_and_views_aggregated() -> None:
    parsed = parse_csv(
        "Page path,Views\n/Checkout/?utm=1,1 200\nhttps://x.com/checkout#top,300\n/about/,10\n"
    )
    assert parsed.rows == {"/Checkout": 1200, "/checkout": 300, "/about": 10}
    assert normalize_path("https://x.com") == "/"
    assert normalize_path("a//b/") == "/a/b"
    assert normalize_path("") is None


def test_unusable_rows_are_counted_not_guessed() -> None:
    parsed = parse_csv("path,views\n/ok,5\n/bad,many\n,3\n/neg,-4\n/ok2\n")
    assert parsed.rows == {"/ok": 5} and parsed.rejected == 4


@pytest.mark.parametrize(
    "csv",
    ["", "just,a,header", "a,b\n1,2", "path,views\n", "path,revenue\n/a,100"],
)
def test_files_without_a_path_and_views_column_are_refused(csv: str) -> None:
    with pytest.raises(TrafficFormatError):
        parse_csv(csv)


def test_revenue_and_other_columns_are_ignored() -> None:
    parsed = parse_csv("path,views,revenue,users\n/pay,100,99999,50\n")
    assert parsed.rows == {"/pay": 100}


def test_the_weight_is_capped_and_a_page_without_traffic_gets_none() -> None:
    index = TrafficIndex({"/": 80, "/a": 20}, 100, "2026-10-01", "2026-10-07", "Export")
    assert index.factor(0.0) == 1.0 and index.factor(0.8) == 3.0 and index.factor(0.05) == 1.5
    assert index.share("/none") == (0, 0.0)


def test_with_traffic_the_score_changes_and_says_whose_numbers_they_are() -> None:
    index = TrafficIndex({"/": 80, "/a": 20}, 100, "2026-10-01", "2026-10-07", "Export")
    plain = rank_issue("low", ["https://x.com/"])
    weighted = rank_issue("low", ["https://x.com/"], index)
    assert weighted.score == plain.score * 3 and weighted.basis == "heuristic+customer_traffic"
    assert weighted.traffic and weighted.traffic["source"] == "customer_supplied"
    reasons = " ".join(weighted.reasons)
    assert "did not measure or verify" in reasons and "No revenue" in reasons
    assert weighted.measured is False and plain.basis == "heuristic"


# --------------------------------------------------------------------------- the API


def test_upload_replace_read_and_delete(lang_issue) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    api, org, project, _issue, _root = lang_issue
    assert api.get(f"{project_base(org, project)}/traffic").json()["present"] is False
    r = upload(api, org, project, "path,views\n/,800\n/about,200\n/bad,x\n")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["present"] and body["total_views"] == 1000 and body["rows_rejected"] == 1
    assert body["top_pages"][0] == {"path": "/", "views": 800, "share_percent": 80.0}
    assert "did not measure or verify" in body["notice"]
    again = upload(api, org, project, "path,views\n/new,5\n").json()
    assert again["pages"] == 1 and again["total_views"] == 5  # replaced, not merged
    assert api.delete(f"{project_base(org, project)}/traffic").status_code == 204
    assert api.get(f"{project_base(org, project)}/traffic").json()["present"] is False


@pytest.mark.parametrize(
    "over",
    [
        {"period_end": "2026-09-01"},
        {"period_start": "2020-01-01"},
        {"period_end": "2999-01-01"},
        {"csv": "nothing useful"},
        {"extra_field": 1},
        {"source_label": ""},
    ],
)
def test_bad_uploads_are_refused(lang_issue, over) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    api, org, project, _i, _r = lang_issue
    assert upload(api, org, project, "path,views\n/,1\n", **over).status_code == 422


def test_ranking_uses_the_uploaded_traffic_and_labels_it(lang_issue) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    api, org, project, issue, _root = lang_issue
    before = api.get(f"/v1/organizations/{org}/issues/ranked").json()[0]
    assert before["impact"]["basis"] == "heuristic" and before["impact"]["traffic"] is None
    upload(api, org, project, "path,views\n/,800\n/about,200\n")
    after = api.get(f"/v1/organizations/{org}/issues/ranked").json()[0]
    assert after["impact"]["basis"] == "heuristic+customer_traffic"
    assert after["impact"]["score"] == before["impact"]["score"] * 3
    assert after["impact"]["traffic"]["share_percent"] == 80.0
    assert after["impact"]["measured"] is False
    ticket = api.get(f"/v1/organizations/{org}/issues/{issue}/ticket").json()["content"]
    assert "Customer-supplied traffic" in ticket and "did not measure or verify" in ticket
    assert "revenue" not in ticket.lower().replace("no revenue", "")


def test_traffic_is_tenant_scoped_and_needs_membership(lang_issue, new_api) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    api, org, project, _i, _r = lang_issue
    upload(api, org, project, "path,views\n/,10\n")
    other = registered(new_api)
    other_org = other.create_org("Other")
    assert other.get(f"/v1/organizations/{org}/projects/{project}/traffic").status_code in (
        403,
        404,
    )
    assert upload(other, org, project, "path,views\n/,1\n").status_code in (403, 404)
    assert upload(other, other_org, project, "path,views\n/,1\n").status_code == 404
    viewer = registered(new_api)
    api.add_member(org, viewer.email or "", "viewer")
    assert viewer.get(f"/v1/organizations/{org}/projects/{project}/traffic").status_code == 200
    assert upload(viewer, org, project, "path,views\n/,1\n").status_code == 403


# ---------------------------------------------------------------- 12B GitHub issue


@pytest.fixture
def github(lang_issue, api_app, encryption_key, monkeypatch):  # type: ignore[no-untyped-def]  # noqa: F811
    api, org, project, issue, _root = lang_issue
    fake = FakeGitHub({"index.html": PAGE})
    api_app.state.github_transport = fake.transport
    get_settings.cache_clear()
    body = {"provider": "github", "location": "acme/site", "default_branch": "main"}
    assert (
        api.request(
            "PUT", f"{project_base(org, project)}/repository", {**body, "token": "ghp_fake_token"}
        ).status_code
        == 200
    )
    return api, org, project, issue, fake, monkeypatch


def preview(api, org, issue) -> dict:  # type: ignore[type-arg,no-untyped-def]
    return api.get(f"/v1/organizations/{org}/issues/{issue}/ticket").json()  # type: ignore[no-any-return]


def send(api, org, issue, sha: str, **over):  # type: ignore[no-untyped-def]
    body = {"content_sha256": sha, "confirm": True, **over}
    return api.post(f"/v1/organizations/{org}/issues/{issue}/github-issue", body)


def enable(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("KMR_GITHUB_ISSUES_ENABLED", "true")
    get_settings.cache_clear()


def test_it_is_off_by_default_and_the_preview_says_why(github) -> None:  # type: ignore[no-untyped-def]
    api, org, _p, issue, fake, _mp = github
    shown = preview(api, org, issue)
    assert shown["github"]["available"] is False and "turned off" in shown["github"]["reason"]
    assert send(api, org, issue, shown["content_sha256"]).status_code == 403
    assert fake.issues == []


def test_a_confirmed_send_creates_exactly_the_previewed_text_once(
    github, owner_engine: Engine
) -> None:  # type: ignore[no-untyped-def]
    api, org, _p, issue, fake, mp = github
    enable(mp)
    shown = preview(api, org, issue)
    assert shown["github"] == {
        "available": True, "reason": None, "repository": "acme/site", "existing_url": None
    }  # fmt: skip
    assert shown["content_sha256"] == hashlib.sha256(shown["content"].encode()).hexdigest()
    r = send(api, org, issue, shown["content_sha256"])
    assert r.status_code == 201, r.text
    assert r.json() == {
        "url": "https://github.com/acme/site/issues/1",
        "number": 1,
        "repository": "acme/site",
    }
    sent = fake.issues[0]
    assert sent["body"] == shown["content"] and sent["title"] == shown["title"]
    assert sent["keys"] == ["body", "title"]  # no labels, assignees or milestone
    # Exactly once: a second attempt is refused and nothing more reaches GitHub.
    again = send(api, org, issue, shown["content_sha256"])
    assert again.status_code == 409 and "already sent" in again.json()["error"]["message"]
    assert len(fake.issues) == 1
    assert preview(api, org, issue)["github"]["existing_url"] == r.json()["url"]
    with owner_engine.begin() as conn:
        details = conn.execute(
            text("SELECT details::text FROM audit_events WHERE action = 'issue.github_created'")
        ).scalar_one()
    assert json.loads(details)["content_sha256"] == shown["content_sha256"]
    assert "ghp_fake_token" not in details and shown["content"] not in details
    # No write other than the one issue; the repository itself was untouched.
    assert [p for m, p in fake.requests if m == "POST"] == ["/repos/acme/site/issues"]


@pytest.mark.parametrize("sha", ["0" * 64, "a" * 64])
def test_text_that_differs_from_the_preview_is_refused(github, sha: str) -> None:  # type: ignore[no-untyped-def]
    api, org, _p, issue, fake, mp = github
    enable(mp)
    r = send(api, org, issue, sha)
    assert r.status_code == 409 and "changed since you previewed" in r.json()["error"]["message"]
    assert fake.issues == []


def test_it_needs_confirmation_the_admin_role_and_a_valid_hash(github, new_api) -> None:  # type: ignore[no-untyped-def]
    api, org, _p, issue, fake, mp = github
    enable(mp)
    sha = preview(api, org, issue)["content_sha256"]
    assert send(api, org, issue, sha, confirm=False).status_code == 422
    assert send(api, org, issue, "not-a-hash").status_code == 422
    assert (
        api.post(
            f"/v1/organizations/{org}/issues/{issue}/github-issue", {"content_sha256": sha}
        ).status_code
        == 422
    )
    member = registered(new_api)
    api.add_member(org, member.email or "", "member")
    assert send(member, org, issue, sha).status_code == 403
    outsider = registered(new_api)
    assert send(outsider, org, issue, sha).status_code in (403, 404)
    assert fake.issues == []


def test_a_token_without_the_issues_permission_gives_a_clear_message(
    github, owner_engine: Engine
) -> None:  # type: ignore[no-untyped-def]
    api, org, _p, issue, fake, mp = github
    enable(mp)
    fake.issues_allowed = False
    sha = preview(api, org, issue)["content_sha256"]
    r = send(api, org, issue, sha)
    assert r.status_code == 403 and "Issues: Read and write" in r.json()["error"]["message"]
    with owner_engine.begin() as conn:
        failed = conn.execute(
            text("SELECT count(*) FROM audit_events WHERE action = 'issue.github_failed'")
        ).scalar()
        exported = conn.execute(text("SELECT count(*) FROM issue_exports")).scalar()
    assert failed == 1 and exported == 0
    fake.issues_allowed = True  # fixed on GitHub's side: a retry now works, once
    assert send(api, org, issue, sha).status_code == 201


def test_a_project_without_github_cannot_send(lang_issue, monkeypatch) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    api, org, _project, issue, _root = lang_issue
    enable(monkeypatch)
    shown = preview(api, org, issue)
    assert shown["github"]["available"] is False and "Connect a GitHub" in shown["github"]["reason"]
    assert send(api, org, issue, shown["content_sha256"]).status_code == 409


def test_another_tenant_cannot_send_or_see_a_finding(github, new_api) -> None:  # type: ignore[no-untyped-def]
    api, org, _p, issue, fake, mp = github
    enable(mp)
    sha = preview(api, org, issue)["content_sha256"]
    other = registered(new_api)
    other_org = other.create_org("Other")
    assert send(other, other_org, issue, sha).status_code == 404
    assert fake.issues == []
