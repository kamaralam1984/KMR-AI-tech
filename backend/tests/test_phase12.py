"""Phase 12: business-impact ranking, ticket export, scan profiles and profile comparison."""

import json
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.config import get_settings
from app.diagnosis.impact import classify_path, rank_issue
from app.scanning.profiles import PROFILES, apply_throttling, context_options, describe
from tests.helpers import project_base, registered, verified_project
from tests.test_diagnosis_api import HOST, _completed_scan, _diagnose, _obs

# ----------------------------------------------------------------------------- 12A impact


def test_journeys_are_guessed_from_the_page_address_only() -> None:
    assert classify_path("https://x.com/checkout/step-2").key == "checkout"
    assert classify_path("https://x.com/user/login").key == "login"
    assert classify_path("https://x.com/").key == "home"
    assert classify_path("https://x.com/blog/post-1").key == "other"
    assert classify_path(None).key == "other"


def test_a_checkout_problem_outranks_the_same_severity_elsewhere() -> None:
    checkout = rank_issue("high", ["https://x.com/checkout"])
    blog = rank_issue("high", ["https://x.com/blog"])
    assert checkout.score > blog.score
    assert rank_issue("critical", ["https://x.com/blog"]).score < checkout.score + 1


def test_ranking_is_labeled_a_heuristic_and_invents_no_numbers() -> None:
    impact = rank_issue("high", ["https://x.com/payment"])
    assert impact.basis == "heuristic" and impact.measured is False
    text_ = " ".join(impact.reasons).lower()
    assert "not a measurement" in text_ and "judged only from its address" in text_
    assert "revenue" in text_  # said only to state that none is shown
    assert not any(ch in text_ for ch in "$%")


def test_a_finding_without_a_page_says_so() -> None:
    impact = rank_issue("medium", [])
    assert impact.page is None and any("No page was recorded" in r for r in impact.reasons)


def test_ranked_endpoint_orders_by_journey_and_labels_the_basis(
    new_api, fake_verifier, fake_queue, owner_engine: Engine
) -> None:  # type: ignore[no-untyped-def]
    api, org, scan, _ids = _completed_scan(new_api, fake_verifier, fake_queue, owner_engine)
    with owner_engine.begin() as conn:
        _obs(
            conn,
            org,
            scan,
            "page_error",
            "error",
            f"https://{HOST}/checkout",
            {"message": "TypeError: Cannot read properties of undefined (reading 'total')"},
        )
    assert _diagnose(api, org, scan).status_code == 200
    ranked = api.get(f"/v1/organizations/{org}/issues/ranked").json()
    assert len(ranked) >= 2
    assert ranked[0]["impact"]["journey"] == "checkout"
    scores = [r["impact"]["score"] for r in ranked]
    assert scores == sorted(scores, reverse=True)
    assert all(
        r["impact"]["basis"] == "heuristic" and r["impact"]["measured"] is False for r in ranked
    )


def test_ranked_issues_do_not_cross_tenants(
    new_api, fake_verifier, fake_queue, owner_engine: Engine
) -> None:  # type: ignore[no-untyped-def]
    a, org_a, scan, _ = _completed_scan(new_api, fake_verifier, fake_queue, owner_engine)
    _diagnose(a, org_a, scan)
    b = registered(new_api)
    org_b = b.create_org()
    assert b.get(f"/v1/organizations/{org_b}/issues/ranked").json() == []
    assert b.get(f"/v1/organizations/{org_a}/issues/ranked").status_code in (403, 404)


# ------------------------------------------------------------------------ 12B ticket export


def _issue_with_secrets(new_api, fake_verifier, fake_queue, owner_engine):  # type: ignore[no-untyped-def]
    api = registered(new_api)
    org, project = verified_project(api, fake_verifier, host=HOST)
    scan = api.post(f"{project_base(org, project)}/scans").json()["id"]
    with owner_engine.begin() as conn:
        conn.execute(
            text("UPDATE scans SET status = 'completed', finished_at = now() WHERE id = :s"),
            {"s": scan},
        )
        _obs(
            conn,
            org,
            scan,
            "page_error",
            "error",
            f"https://{HOST}/account?token=abcDEF123456789012345678901234",
            {"message": "TypeError for jane.doe@example.com with api_key=sk_live_1234567890abcdef"},
        )
    issue = _diagnose(api, org, scan).json()["issues"][0]["id"]
    return api, org, issue


def test_ticket_is_redacted_labeled_and_audited(
    new_api, fake_verifier, fake_queue, owner_engine: Engine
) -> None:  # type: ignore[no-untyped-def]
    api, org, issue = _issue_with_secrets(new_api, fake_verifier, fake_queue, owner_engine)
    response = api.get(f"/v1/organizations/{org}/issues/{issue}/ticket")
    assert response.status_code == 200
    body = response.json()
    content = body["content"]
    for leaked in (
        "jane.doe@example.com",
        "sk_live_1234567890abcdef",
        "abcDEF123456789012345678901234",
    ):
        assert leaked not in content
    assert "## Observed" in content and "estimate, not measured" in content
    assert "Steps to reproduce" in content
    assert body["filename"].endswith(".md") and "masking is pattern-based" in body["notice"]
    with owner_engine.begin() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM audit_events WHERE action = 'issue.ticket_preview'")
        ).scalar()
    assert count == 1


def test_ticket_for_another_tenants_issue_is_not_found(
    new_api, fake_verifier, fake_queue, owner_engine: Engine
) -> None:  # type: ignore[no-untyped-def]
    a, org_a, issue = _issue_with_secrets(new_api, fake_verifier, fake_queue, owner_engine)
    b = registered(new_api)
    org_b = b.create_org()
    assert b.get(f"/v1/organizations/{org_b}/issues/{issue}/ticket").status_code == 404
    assert b.get(f"/v1/organizations/{org_a}/issues/{issue}/ticket").status_code in (403, 404)
    assert b.get(f"/v1/organizations/{org_b}/issues/{uuid.uuid4()}/ticket").status_code == 404


# ------------------------------------------------------------------------- 12C scan profiles


def test_profile_definitions_are_consistent() -> None:
    assert set(PROFILES) == {"desktop", "phone", "slow_network", "low_end_phone"}
    assert PROFILES["desktop"].weight == 1 and not PROFILES["desktop"].throttled
    assert PROFILES["low_end_phone"].weight > PROFILES["slow_network"].weight > 1
    options = context_options(PROFILES["phone"])
    assert options["viewport"] == {"width": 360, "height": 640} and options["is_mobile"] is True
    assert "is_mobile" not in context_options(PROFILES["desktop"])
    stored = describe(PROFILES["slow_network"])
    assert stored["engine"] == "chromium" and stored["download_kbit_s"] == 1600
    assert stored["quota_weight"] == 2


def _scan_path(org: str, project: str, query: str = "") -> str:
    return f"{project_base(org, project)}/scans{query}"


def test_a_scan_stores_the_exact_profile_it_will_run_under(
    new_api, fake_verifier, fake_queue
) -> None:  # type: ignore[no-untyped-def]
    api = registered(new_api)
    org, project = verified_project(api, fake_verifier)
    created = api.post(_scan_path(org, project, "?profile=low_end_phone"))
    assert created.status_code == 202
    profile = created.json()["limits"]["profile"]
    assert profile["key"] == "low_end_phone" and profile["cpu_slowdown"] == 4.0
    assert profile["engine"] == "chromium" and profile["viewport"] == [360, 640]


def test_default_scan_is_the_desktop_profile(new_api, fake_verifier, fake_queue) -> None:  # type: ignore[no-untyped-def]
    api = registered(new_api)
    org, project = verified_project(api, fake_verifier)
    created = api.post(_scan_path(org, project))
    assert created.json()["limits"]["profile"]["key"] == "desktop"


def test_unknown_profiles_and_unavailable_engines_are_refused(
    new_api, fake_verifier, fake_queue
) -> None:  # type: ignore[no-untyped-def]
    api = registered(new_api)
    org, project = verified_project(api, fake_verifier)
    assert api.post(_scan_path(org, project, "?profile=hacker")).status_code == 422
    assert api.post(_scan_path(org, project, "?engine=netscape")).status_code == 422
    refused = api.post(_scan_path(org, project, "?engine=firefox"))
    assert refused.status_code == 409 and "not available" in refused.json()["error"]["message"]


def test_slower_profiles_use_more_of_the_daily_quota(
    new_api, fake_verifier, fake_queue, owner_engine: Engine, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    api = registered(new_api)
    org, project = verified_project(api, fake_verifier)
    monkeypatch.setenv("KMR_SCAN_DAILY_QUOTA_PER_ORG", "4")
    get_settings.cache_clear()
    path = _scan_path(org, project)
    assert api.post(path + "?profile=low_end_phone").status_code == 202  # uses 3 of 4
    with owner_engine.begin() as conn:
        conn.execute(text("UPDATE scans SET status = 'completed', finished_at = now()"))
    over = api.post(path + "?profile=slow_network")  # needs 2, only 1 left
    assert over.status_code == 429 and "counts as 2" in over.json()["error"]["message"]
    assert api.post(path).status_code == 202  # desktop needs 1


# ------------------------------------------------------------------------ profile comparison


def _finished_scan(api, org, project, owner_engine, scan_id=None):  # type: ignore[no-untyped-def]
    scan = scan_id or api.post(f"{project_base(org, project)}/scans").json()["id"]
    with owner_engine.begin() as conn:
        conn.execute(
            text("UPDATE scans SET status = 'completed', finished_at = now() WHERE id = :s"),
            {"s": scan},
        )
    return scan


def test_compare_shows_findings_that_appear_under_only_one_profile(
    new_api, fake_verifier, fake_queue, owner_engine: Engine
) -> None:  # type: ignore[no-untyped-def]
    api = registered(new_api)
    org, project = verified_project(api, fake_verifier, host=HOST)
    desktop = _finished_scan(api, org, project, owner_engine)
    phone = api.post(_scan_path(org, project, "?profile=phone")).json()["id"]
    _finished_scan(api, org, project, owner_engine, phone)
    with owner_engine.begin() as conn:
        for scan, load in ((desktop, 300), (phone, 1900)):
            _obs(
                conn,
                org,
                scan,
                "page",
                "info",
                f"https://{HOST}/",
                {"status": 200, "load_ms": load},
            )
        _obs(
            conn,
            org,
            phone,
            "a11y_issue",
            "warning",
            f"https://{HOST}/",
            {"check": "horizontal_overflow", "count": 1},
        )
        _obs(
            conn,
            org,
            desktop,
            "a11y_issue",
            "warning",
            f"https://{HOST}/",
            {"check": "missing_lang", "count": 1},
        )
        for scan in (desktop, phone):
            _obs(conn, org, scan, "page_error", "error", f"https://{HOST}/", {"message": "boom"})
    result = api.get(f"/v1/organizations/{org}/scans/{desktop}/compare/{phone}").json()
    assert [f["kind"] for f in result["only_in_second"]] == ["a11y_issue"]
    assert [f["kind"] for f in result["only_in_first"]] == ["a11y_issue"]
    assert result["in_both"] == 1
    assert result["second"]["profile"]["key"] == "phone"
    assert result["load_ms"] == [{"page": "/", "first": 300, "second": 1900}]
    assert "not by real visitors" in result["basis"]


def test_compare_is_tenant_scoped_and_needs_completed_scans_of_one_project(
    new_api, fake_verifier, fake_queue, owner_engine: Engine
) -> None:  # type: ignore[no-untyped-def]
    a = registered(new_api)
    org_a, proj_a = verified_project(a, fake_verifier, host=HOST)
    scan_a = _finished_scan(a, org_a, proj_a, owner_engine)
    pending = a.post(_scan_path(org_a, proj_a)).json()["id"]
    assert a.get(f"/v1/organizations/{org_a}/scans/{scan_a}/compare/{pending}").status_code == 409
    b = registered(new_api)
    org_b, _pb = verified_project(b, fake_verifier, host="other.example.com")
    assert b.get(f"/v1/organizations/{org_b}/scans/{scan_a}/compare/{scan_a}").status_code == 404


# ------------------------------------------------- 12C real browser (skips without Chrome)


def test_throttling_is_a_noop_for_unthrottled_profiles() -> None:
    import asyncio

    asyncio.run(apply_throttling(None, None, PROFILES["desktop"]))  # type: ignore[arg-type]


def test_json_round_trip_of_stored_profile() -> None:
    stored = json.dumps(describe(PROFILES["phone"]))
    assert json.loads(stored)["viewport"] == [360, 640]


@pytest.fixture(autouse=True)
def _reset_settings():  # type: ignore[no-untyped-def]
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ------------------------------------------------------------------- 12C other browser engines


def _enable_firefox(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("KMR_SCAN_ENGINES_AVAILABLE", '["chromium","firefox"]')
    monkeypatch.setenv("KMR_SCAN_BROWSER_WS_FIREFOX", "ws://172.31.250.10:3001/test-path")
    get_settings.cache_clear()


def test_a_listed_engine_can_be_chosen_and_is_stored_with_the_scan(
    new_api, fake_verifier, fake_queue, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    api = registered(new_api)
    org, project = verified_project(api, fake_verifier)
    _enable_firefox(monkeypatch)
    path = f"{project_base(org, project)}/scans"
    created = api.post(path + "?engine=firefox&profile=phone")
    assert created.status_code == 202, created.text
    profile = created.json()["limits"]["profile"]
    assert profile["engine"] == "firefox" and profile["key"] == "phone"


def test_throttled_profiles_are_refused_for_engines_that_cannot_throttle(
    new_api, fake_verifier, fake_queue, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    api = registered(new_api)
    org, project = verified_project(api, fake_verifier)
    _enable_firefox(monkeypatch)
    refused = api.post(f"{project_base(org, project)}/scans?engine=firefox&profile=slow_network")
    assert refused.status_code == 422 and "Chromium" in refused.json()["error"]["message"]
    assert api.post(f"{project_base(org, project)}/scans?profile=slow_network").status_code == 202


def test_an_engine_that_is_not_listed_is_refused_even_if_configured(
    new_api, fake_verifier, fake_queue, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    api = registered(new_api)
    org, project = verified_project(api, fake_verifier)
    monkeypatch.setenv("KMR_SCAN_BROWSER_WS_FIREFOX", "ws://172.31.250.10:3001/x")
    get_settings.cache_clear()
    assert api.post(f"{project_base(org, project)}/scans?engine=firefox").status_code == 409
    assert api.post(f"{project_base(org, project)}/scans?engine=webkit").status_code == 409


def test_startup_refuses_a_listed_engine_that_has_no_browser_configured() -> None:
    from app.config import Settings, validate_scan_settings

    def build(**over):  # type: ignore[no-untyped-def]
        return Settings(_env_file=None, app_env="test", **over)  # type: ignore[call-arg]

    validate_scan_settings(build())
    with pytest.raises(RuntimeError, match="not configured"):
        validate_scan_settings(build(scan_engines_available=["chromium", "firefox"]))
    validate_scan_settings(
        build(scan_engines_available=["chromium", "firefox"], scan_browser_ws_firefox="ws://x/y")
    )
    with pytest.raises(RuntimeError, match="unknown engine"):
        validate_scan_settings(build(scan_engines_available=["netscape"]))
    with pytest.raises(RuntimeError, match="not configured"):
        validate_scan_settings(
            build(
                scan_engines_available=["webkit"],
                scan_browser_mode="local",
                scan_local_chrome_path="/x",
            )
        )


def test_engine_rules_for_profiles() -> None:
    from app.scanning.profiles import engine_supports

    assert engine_supports(PROFILES["desktop"], "firefox") is None
    assert engine_supports(PROFILES["phone"], "firefox") is None
    assert engine_supports(PROFILES["low_end_phone"], "firefox") is not None
    assert engine_supports(PROFILES["low_end_phone"], "chromium") is None
    assert "is_mobile" not in context_options(PROFILES["phone"], "firefox")
    assert context_options(PROFILES["phone"], "firefox")["has_touch"] is True
    assert context_options(PROFILES["phone"], "chromium")["is_mobile"] is True


def test_scan_options_list_only_what_is_really_available(
    new_api, fake_verifier, fake_queue, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    api = registered(new_api)
    org, _project = verified_project(api, fake_verifier)
    options = api.get(f"/v1/organizations/{org}/scan-options").json()
    assert options["engines"] == ["chromium"]
    assert {p["key"] for p in options["profiles"]} == set(PROFILES)
    _enable_firefox(monkeypatch)
    assert api.get(f"/v1/organizations/{org}/scan-options").json()["engines"] == [
        "chromium",
        "firefox",
    ]
    outsider = registered(new_api)
    assert outsider.get(f"/v1/organizations/{org}/scan-options").status_code in (403, 404)
