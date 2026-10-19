import json
import uuid

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.config import get_settings
from app.diagnosis.anthropic_provider import AnthropicProvider
from app.diagnosis.schemas import DiagnosisOutput, Fact, IssueDraft, Usage
from tests.fakes import FakeProvider
from tests.helpers import project_base, registered, verified_project

HOST = "shop.example.com"


def _obs(conn, org: str, scan: str, kind: str, severity: str, page: str, data: dict) -> str:  # type: ignore[no-untyped-def,type-arg]
    oid = str(uuid.uuid4())
    conn.execute(
        text(
            "INSERT INTO observations (id, org_id, scan_id, kind, severity, page_url, data) "
            "VALUES (:i, :o, :s, :k, :v, :p, CAST(:d AS jsonb))"
        ),
        {"i": oid, "o": org, "s": scan, "k": kind, "v": severity, "p": page, "d": json.dumps(data)},
    )
    return oid


def _completed_scan(
    new_api, fake_verifier, fake_queue, owner_engine: Engine, failures: bool = True
):  # type: ignore[no-untyped-def]
    api = registered(new_api)
    org, project = verified_project(api, fake_verifier, host=HOST)
    scan = api.post(f"{project_base(org, project)}/scans").json()["id"]
    ids: dict[str, str] = {}
    with owner_engine.begin() as conn:
        conn.execute(
            text("UPDATE scans SET status = 'completed', finished_at = now() WHERE id = :s"),
            {"s": scan},
        )
        ids["page"] = _obs(
            conn,
            org,
            scan,
            "page",
            "info",
            f"https://{HOST}/",
            {"status": 200, "title": "Home", "load_ms": 120},
        )
        if failures:
            ids["error"] = _obs(
                conn,
                org,
                scan,
                "page_error",
                "error",
                f"https://{HOST}/about",
                {"message": "TypeError: Cannot read properties of undefined (reading 'pincode')"},
            )
            ids["http"] = _obs(
                conn,
                org,
                scan,
                "http_error",
                "error",
                f"https://{HOST}/",
                {"url": f"https://{HOST}/api/orders", "status": 500, "resource_type": "fetch"},
            )
    return api, org, scan, ids


def _diagnose(api, org, scan):  # type: ignore[no-untyped-def]
    return api.post(f"/v1/organizations/{org}/scans/{scan}/diagnose")


def test_the_builtin_rules_diagnose_a_scan_and_say_they_are_not_ai(
    new_api, fake_verifier, fake_queue, owner_engine
) -> None:  # type: ignore[no-untyped-def]
    api, org, scan, ids = _completed_scan(new_api, fake_verifier, fake_queue, owner_engine)
    response = _diagnose(api, org, scan)
    assert response.status_code == 200
    body = response.json()
    assert body["run"]["provider"] == "rules" and body["run"]["is_ai"] is False
    assert (
        body["run"]["status"] == "completed"
        and body["run"]["issues_count"] == len(body["issues"]) == 2
    )
    assert {i["category"] for i in body["issues"]} == {"frontend_error", "backend_api"}
    assert all(i["is_ai"] is False for i in body["issues"])
    status = api.get("/v1/ai/status").json()
    assert status == {
        "provider": "rules",
        "model": "built-in-rules",
        "is_ai": False,
        "label": "Built-in rules (not AI)",
    }


def test_issue_detail_separates_facts_hypotheses_and_unknowns_with_real_evidence(
    new_api, fake_verifier, fake_queue, owner_engine
) -> None:  # type: ignore[no-untyped-def]
    api, org, scan, ids = _completed_scan(new_api, fake_verifier, fake_queue, owner_engine)
    issues = _diagnose(api, org, scan).json()["issues"]
    js = next(i for i in issues if i["category"] == "frontend_error")
    detail = api.get(f"/v1/organizations/{org}/issues/{js['id']}").json()
    assert detail["facts"] and detail["hypotheses"] and detail["unverified"]
    assert detail["facts"][0]["evidence_ids"] == [ids["error"]]
    assert detail["proposed_fix"] and detail["verification_plan"] and detail["reproduction_steps"]
    # Every cited record exists in this organization and this scan.
    with owner_engine.connect() as conn:
        for ref in detail["evidence"]:
            row = conn.execute(
                text("SELECT org_id, scan_id FROM observations WHERE id = :i"), {"i": ref["id"]}
            ).one()
            assert (str(row.org_id), str(row.scan_id)) == (org, scan)
    backend = next(i for i in issues if i["category"] == "backend_api")
    assert backend["classification"] == "needs_review"  # no backend evidence: abstains
    assert api.get(f"/v1/organizations/{org}/issues/{backend['id']}").json()["hypotheses"] == []


def test_a_clean_scan_produces_no_issues(new_api, fake_verifier, fake_queue, owner_engine) -> None:  # type: ignore[no-untyped-def]
    api, org, scan, _ = _completed_scan(
        new_api, fake_verifier, fake_queue, owner_engine, failures=False
    )
    body = _diagnose(api, org, scan).json()
    assert body["issues"] == [] and body["run"]["issues_count"] == 0


def test_only_a_completed_scan_can_be_diagnosed(new_api, fake_verifier, fake_queue) -> None:  # type: ignore[no-untyped-def]
    api = registered(new_api)
    org, project = verified_project(api, fake_verifier, host=HOST)
    scan = api.post(f"{project_base(org, project)}/scans").json()["id"]  # still queued
    assert _diagnose(api, org, scan).status_code == 409


def test_diagnosing_again_updates_the_same_issues_and_marks_vanished_ones_stale(
    new_api, fake_verifier, fake_queue, owner_engine
) -> None:  # type: ignore[no-untyped-def]
    api, org, scan, ids = _completed_scan(new_api, fake_verifier, fake_queue, owner_engine)
    first = {i["category"]: i["id"] for i in _diagnose(api, org, scan).json()["issues"]}
    second = {i["category"]: i["id"] for i in _diagnose(api, org, scan).json()["issues"]}
    assert first == second  # same rows, so anything linked to them survives
    with owner_engine.begin() as conn:
        conn.execute(text("DELETE FROM observations WHERE id = :i"), {"i": ids["error"]})
    states = {i["category"]: i["state"] for i in _diagnose(api, org, scan).json()["issues"]}
    assert states["frontend_error"] == "stale" and states["backend_api"] == "needs_review"


def test_listing_filters_and_scan_issues(new_api, fake_verifier, fake_queue, owner_engine) -> None:  # type: ignore[no-untyped-def]
    api, org, scan, _ = _completed_scan(new_api, fake_verifier, fake_queue, owner_engine)
    _diagnose(api, org, scan)
    base = f"/v1/organizations/{org}"
    assert len(api.get(f"{base}/issues").json()) == 2
    assert [i["severity"] for i in api.get(f"{base}/issues?severity=critical").json()] == []
    assert len(api.get(f"{base}/issues?state=needs_review").json()) == 1
    assert len(api.get(f"{base}/scans/{scan}/issues").json()) == 2
    assert api.get(f"{base}/issues?limit=0").status_code == 422


def test_viewers_read_but_cannot_diagnose_and_outsiders_see_nothing(
    new_api, fake_verifier, fake_queue, owner_engine
) -> None:  # type: ignore[no-untyped-def]
    api, org, scan, _ = _completed_scan(new_api, fake_verifier, fake_queue, owner_engine)
    issue = _diagnose(api, org, scan).json()["issues"][0]["id"]
    viewer, outsider = registered(new_api), registered(new_api)
    api.add_member(org, viewer.email, "viewer")  # type: ignore[arg-type]
    base = f"/v1/organizations/{org}"
    assert viewer.get(f"{base}/issues/{issue}").status_code == 200
    assert _diagnose(viewer, org, scan).status_code == 403
    for response in (
        outsider.get(f"{base}/issues"),
        outsider.get(f"{base}/issues/{issue}"),
        outsider.get(f"{base}/scans/{scan}/issues"),
        _diagnose(outsider, org, scan),
    ):
        assert response.status_code == 404
    other_org = outsider.create_org()
    assert outsider.get(f"/v1/organizations/{other_org}/issues/{issue}").status_code == 404


# ----------------------------------------------------------------------------- providers, usage, quotas


def _install(api_app, provider) -> None:  # type: ignore[no-untyped-def]
    api_app.state.diagnosis_provider = provider


def test_usage_and_cost_are_recorded_and_the_model_is_named(
    api_app, new_api, fake_verifier, fake_queue, owner_engine, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("KMR_AI_INPUT_PRICE_PER_MTOK", "3")
    monkeypatch.setenv("KMR_AI_OUTPUT_PRICE_PER_MTOK", "15")
    get_settings.cache_clear()
    api, org, scan, ids = _completed_scan(new_api, fake_verifier, fake_queue, owner_engine)
    _install(api_app, FakeProvider(usage=Usage(input_tokens=2000, output_tokens=500)))
    run = _diagnose(api, org, scan).json()["run"]
    assert run["provider"] == "fake-ai" and run["model"] == "fake-model-1" and run["is_ai"] is True
    assert (run["input_tokens"], run["output_tokens"]) == (2000, 500)
    assert run["cost_micros"] == 2000 * 3 + 500 * 15  # micro-dollars: price per million tokens
    assert run["latency_ms"] is not None
    usage = api.get(f"/v1/organizations/{org}/ai-usage").json()
    assert usage["month_runs"] == 1 and usage["month_cost_usd"] == pytest.approx(0.0135)


def test_prices_unknown_means_no_invented_cost(
    api_app, new_api, fake_verifier, fake_queue, owner_engine
) -> None:  # type: ignore[no-untyped-def]
    api, org, scan, _ = _completed_scan(new_api, fake_verifier, fake_queue, owner_engine)
    _install(api_app, FakeProvider())
    assert _diagnose(api, org, scan).json()["run"]["cost_micros"] is None
    assert api.get(f"/v1/organizations/{org}/ai-usage").json()["month_cost_usd"] is None


def test_usage_is_visible_to_admins_only(new_api, fake_verifier, fake_queue, owner_engine) -> None:  # type: ignore[no-untyped-def]
    api, org, scan, _ = _completed_scan(new_api, fake_verifier, fake_queue, owner_engine)
    member = registered(new_api)
    api.add_member(org, member.email, "member")  # type: ignore[arg-type]
    assert member.get(f"/v1/organizations/{org}/ai-usage").status_code == 403
    assert api.get(f"/v1/organizations/{org}/ai-usage").status_code == 200


def test_the_daily_run_limit_is_enforced(
    new_api, fake_verifier, fake_queue, owner_engine, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("KMR_AI_DAILY_RUNS_PER_ORG", "2")
    get_settings.cache_clear()
    api, org, scan, _ = _completed_scan(new_api, fake_verifier, fake_queue, owner_engine)
    assert [_diagnose(api, org, scan).status_code for _ in range(3)] == [200, 200, 429]


def test_the_monthly_cost_ceiling_blocks_further_runs_and_is_audited(
    api_app, new_api, fake_verifier, fake_queue, owner_engine, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv(
        "KMR_AI_INPUT_PRICE_PER_MTOK", "1000000"
    )  # $1 per token, to hit the ceiling fast
    monkeypatch.setenv("KMR_AI_OUTPUT_PRICE_PER_MTOK", "0")
    monkeypatch.setenv("KMR_AI_MONTHLY_COST_CEILING_USD", "5")
    get_settings.cache_clear()
    api, org, scan, _ = _completed_scan(new_api, fake_verifier, fake_queue, owner_engine)
    _install(api_app, FakeProvider(usage=Usage(input_tokens=10, output_tokens=1)))
    assert _diagnose(api, org, scan).status_code == 200  # $10 spent, over the $5 ceiling
    blocked = _diagnose(api, org, scan)
    assert (
        blocked.status_code == 429
        and "monthly AI spending limit" in blocked.json()["error"]["message"]
    )
    with owner_engine.connect() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM audit_events WHERE action = 'ai.cost_ceiling_reached'")
        ).scalar()
    assert count == 1


def test_a_failing_provider_is_a_clean_error_and_changes_nothing(
    api_app, new_api, fake_verifier, fake_queue, owner_engine
) -> None:  # type: ignore[no-untyped-def]
    api, org, scan, _ = _completed_scan(new_api, fake_verifier, fake_queue, owner_engine)
    assert _diagnose(api, org, scan).status_code == 200  # rules create two issues first
    _install(api_app, FakeProvider(error="provider_unavailable"))
    failed = _diagnose(api, org, scan)
    assert (
        failed.status_code == 502 and "temporarily unavailable" in failed.json()["error"]["message"]
    )
    assert len(api.get(f"/v1/organizations/{org}/scans/{scan}/issues").json()) == 2
    with owner_engine.connect() as conn:
        row = conn.execute(
            text("SELECT status, error_code FROM diagnosis_runs ORDER BY created_at DESC LIMIT 1")
        ).one()
    assert (row.status, row.error_code) == ("failed", "provider_unavailable")


def test_invented_citations_are_dropped_and_flagged_not_stored(
    api_app, new_api, fake_verifier, fake_queue, owner_engine
) -> None:  # type: ignore[no-untyped-def]
    api, org, scan, ids = _completed_scan(new_api, fake_verifier, fake_queue, owner_engine)
    ghost = str(uuid.uuid4())
    output = DiagnosisOutput(
        issues=[
            IssueDraft(
                title="Real",
                category="frontend_error",
                severity="medium",
                confidence="medium",
                classification="diagnosed",
                evidence_ids=[ids["error"]],
                facts=[Fact(text="seen", evidence_ids=[ids["error"]])],
            ),
            IssueDraft(
                title="Pure invention",
                category="backend_api",
                severity="critical",
                confidence="high",
                classification="diagnosed",
                evidence_ids=[ghost],
                facts=[Fact(text="made up", evidence_ids=[ghost])],
            ),
        ]
    )
    _install(api_app, FakeProvider(output=output))
    body = _diagnose(api, org, scan).json()
    assert [i["title"] for i in body["issues"]] == ["Real"]
    assert body["run"]["dropped_issues"] == 1
    assert {"invalid_citations", "issues_without_evidence_dropped"} <= set(body["run"]["flags"])


def test_unusable_ai_output_saves_nothing(
    api_app, new_api, fake_verifier, fake_queue, owner_engine
) -> None:  # type: ignore[no-untyped-def]
    api, org, scan, _ = _completed_scan(new_api, fake_verifier, fake_queue, owner_engine)
    bad = httpx.MockTransport(
        lambda r: httpx.Response(
            200,
            json={
                "content": [
                    {
                        "type": "tool_use",
                        "name": "report_issues",
                        "input": {"issues": [{"title": "x"}]},
                    }
                ]
            },
        )
    )
    settings = get_settings()
    settings = settings.model_copy(
        update={"ai_provider": "anthropic", "ai_api_key": SecretStr("k" * 20)}
    )
    _install(api_app, AnthropicProvider(settings, transport=bad, backoff_seconds=0))
    response = _diagnose(api, org, scan)
    assert (
        response.status_code == 502 and "could not be used" in response.json()["error"]["message"]
    )
    assert api.get(f"/v1/organizations/{org}/scans/{scan}/issues").json() == []


def test_an_unconfigured_ai_provider_says_so(
    api_app, new_api, fake_verifier, fake_queue, owner_engine
) -> None:  # type: ignore[no-untyped-def]
    api, org, scan, _ = _completed_scan(new_api, fake_verifier, fake_queue, owner_engine)
    unconfigured = get_settings().model_copy(
        update={"ai_provider": "anthropic", "ai_api_key": None}
    )
    _install(api_app, AnthropicProvider(unconfigured))
    response = _diagnose(api, org, scan)
    assert response.status_code == 503 and "not configured" in response.json()["error"]["message"]


def test_the_ai_is_given_only_sanitized_evidence_and_never_secrets(
    api_app, new_api, fake_verifier, fake_queue, owner_engine
) -> None:  # type: ignore[no-untyped-def]
    api, org, scan, _ = _completed_scan(new_api, fake_verifier, fake_queue, owner_engine)
    with owner_engine.begin() as conn:
        _obs(
            conn,
            org,
            scan,
            "console_error",
            "error",
            f"https://{HOST}/x",
            {
                "message": "failed with Authorization: Bearer abcdef1234567890abcdef for jane@example.com"
            },
        )
    provider = FakeProvider()
    _install(api_app, provider)
    _diagnose(api, org, scan)
    sent = json.dumps([i.model_dump() for i in provider.inputs[0].evidence])
    assert "abcdef1234567890abcdef" not in sent and "jane@example.com" not in sent
