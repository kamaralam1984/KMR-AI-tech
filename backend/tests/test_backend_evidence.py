import json
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.scanning.backend_logs import MAX_ENTRIES_PER_SCAN, parse_logs
from app.scanning.correlate import Log, Obs, Run, correlate
from tests.helpers import project_base, registered, verified_project

HOST = "shop.example.com"


# ----------------------------------------------------------------------------- parsing


def test_jsonl_keys_are_understood_under_common_names() -> None:
    lines = "\n".join(
        [
            json.dumps(
                {
                    "requestId": "kmr-abc-1",
                    "statusCode": 500,
                    "path": "/api/p?x=1",
                    "level": "ERROR",
                    "msg": "db timeout",
                    "timestamp": "2026-01-01T10:00:00Z",
                }
            ),
            json.dumps(
                {
                    "x_request_id": "kmr-abc-2",
                    "status": "200",
                    "url": "https://shop.example.com/a",
                    "severity": "warning",
                    "message": "slow",
                    "ts": 1767261600,
                }
            ),
            json.dumps({"correlation_id": "kmr-abc-3", "http_status": 404}),
        ]
    )
    result = parse_logs(lines, "jsonl")
    assert result.rejected == 0 and len(result.entries) == 3
    first, second, third = result.entries
    assert (first.request_id, first.http_status, first.path, first.level) == (
        "kmr-abc-1",
        500,
        "/api/p",
        "error",
    )
    assert first.ts is not None and first.ts.year == 2026
    assert (second.http_status, second.path, second.level) == (200, "/a", "warn")
    assert third.request_id == "kmr-abc-3" and third.path is None


def test_lines_that_say_nothing_about_a_request_are_rejected() -> None:
    content = "\n".join(
        [
            "not json",
            json.dumps({"hello": "world"}),
            json.dumps([1, 2]),
            "",
            json.dumps({"path": "/ok"}),
        ]
    )
    result = parse_logs(content, "jsonl")
    assert len(result.entries) == 1 and result.rejected == 3


def test_invalid_ids_and_statuses_are_dropped_not_stored() -> None:
    line = json.dumps({"request_id": "bad id with spaces!", "path": "/x", "status": 99999})
    entry = parse_logs(line, "jsonl").entries[0]
    assert entry.request_id is None and entry.http_status is None and entry.path == "/x"


def test_plain_text_logs_are_understood() -> None:
    text_logs = "\n".join(
        [
            "2026-01-01T10:00:00Z ERROR request_id=kmr-abc-9 GET /api/products status=500 boom",
            '10.0.0.1 - - [01/Jan/2026] "GET /api/orders HTTP/1.1" 502 123 x-request-id: kmr-abc-10',
            "just some noise",
        ]
    )
    result = parse_logs(text_logs, "text")
    assert result.rejected == 1
    a, b = result.entries
    assert (a.request_id, a.http_status, a.path, a.level) == (
        "kmr-abc-9",
        500,
        "/api/products",
        "error",
    )
    assert (b.request_id, b.http_status, b.path) == ("kmr-abc-10", 502, "/api/orders")


def test_secrets_and_personal_data_are_masked_in_stored_messages() -> None:
    line = json.dumps(
        {
            "request_id": "kmr-abc-1",
            "msg": "login failed for jane@example.com Authorization: Bearer abcdef1234567890xyz token=abc123def456",
        }
    )
    message = parse_logs(line, "jsonl").entries[0].message or ""
    for leaked in ("jane@example.com", "abcdef1234567890", "abc123def456"):
        assert leaked not in message


def test_only_a_bounded_number_of_lines_is_processed() -> None:
    content = "\n".join(json.dumps({"path": f"/p{i}"}) for i in range(25_000))
    result = parse_logs(content, "jsonl")
    assert len(result.entries) + result.rejected == 25_000 and len(result.entries) == 20_000


# ----------------------------------------------------------------------------- correlation


def obs(
    kind: str = "http_error",
    status: int | None = 500,
    url: str = f"https://{HOST}/api/products",
    rid: str | None = "kmr-1-1",
    **data,
) -> Obs:  # type: ignore[no-untyped-def]
    payload = {"url": url, "status": status, **({"request_id": rid} if rid else {}), **data}
    return Obs(str(uuid.uuid4()), kind, "error", f"https://{HOST}/", payload)


def run(status: str = "passed", path: str = "/api/products", http: int | None = 200, **kw) -> Run:  # type: ignore[no-untyped-def]
    return Run(str(uuid.uuid4()), "Products", path, status, http, kw.get("reasons", []))


def log(rid: str | None = "kmr-1-1", status: int | None = 500, level: str | None = "error") -> Log:
    return Log(str(uuid.uuid4()), rid, status, level, "/api/products", "msg")


def one(observations, runs=(), logs=()):  # type: ignore[no-untyped-def]
    result = correlate(list(observations), list(runs), list(logs), site_host=HOST)
    assert len(result) == 1
    return result[0]


def test_failure_on_both_sides_is_confirmed_but_claims_no_cause() -> None:
    c = one([obs()], logs=[log()])
    assert c.classification == "backend_confirmed" and not c.needs_review
    assert c.sources == ["browser_observed", "customer_supplied"]
    assert "does not yet say why" in c.statement


def test_a_failed_api_check_on_the_same_path_confirms_it() -> None:
    c = one([obs(rid=None)], runs=[run("failed", http=500)])
    assert c.classification == "backend_confirmed" and "api_observed" in c.sources


def test_browser_failure_with_a_healthy_backend_stays_uncertain() -> None:
    from_logs = one([obs()], logs=[log(status=200, level="info")])
    from_checks = one([obs(rid=None)], runs=[run("passed")])
    for c in (from_logs, from_checks):
        assert c.classification == "uncertain" and c.needs_review
        assert "not enough evidence" in c.statement


def test_no_backend_data_is_stated_plainly() -> None:
    c = one([obs(rid=None)])
    assert c.classification == "no_backend_data" and c.needs_review
    assert "neither blamed nor ruled out" in c.statement
    assert c.sources == ["browser_observed"]


def test_a_log_for_a_different_request_id_is_not_used() -> None:
    c = one([obs(rid="kmr-1-1")], logs=[log(rid="kmr-9-9")])
    assert c.classification == "no_backend_data"


def test_an_api_check_for_a_different_path_is_not_used() -> None:
    c = one([obs(rid=None)], runs=[run("failed", path="/api/orders", http=500)])
    assert c.classification == "no_backend_data"


def test_another_host_is_third_party_and_never_correlated() -> None:
    c = one([obs(url="https://cdn.other.com/lib.js", rid=None)], logs=[log(rid="kmr-1-1")])
    assert (
        c.classification == "third_party"
        and not c.needs_review
        and "not your verified site" in c.statement
    )


def test_a_status_both_sides_agree_on_counts_as_seen_on_both_sides() -> None:
    c = one([obs(status=404)], logs=[log(status=404, level="info")])
    assert c.classification == "backend_confirmed"


def test_network_failures_are_correlated_by_request_id() -> None:
    c = one([obs("failed_request", None, failure="net::ERR_FAILED")], logs=[log()])
    assert c.classification == "backend_confirmed"


def test_only_failures_appear_and_a_failing_api_check_counts_as_one() -> None:
    healthy_page = Obs(str(uuid.uuid4()), "page", "info", f"https://{HOST}/", {"status": 200})
    failing_page = Obs(str(uuid.uuid4()), "page", "error", f"https://{HOST}/x", {"status": 500})
    passing_check = Obs(
        str(uuid.uuid4()),
        "api_check",
        "info",
        f"https://{HOST}/h",
        {"status": "passed", "name": "H", "reasons": []},
    )
    failing_check = Obs(
        str(uuid.uuid4()),
        "api_check",
        "error",
        f"https://{HOST}/h",
        {"status": "failed", "name": "H", "reasons": [{"code": "too_slow"}]},
    )
    result = correlate(
        [healthy_page, failing_page, passing_check, failing_check], [], [], site_host=HOST
    )
    assert {c.observation_id for c in result} == {failing_page.id, failing_check.id}


@pytest.mark.parametrize(
    "make",
    [
        lambda: one([obs()], logs=[log()]),
        lambda: one([obs()], logs=[log(status=200, level="info")]),
        lambda: one([obs(rid=None)]),
    ],
)
def test_no_statement_ever_names_a_root_cause(make) -> None:  # type: ignore[no-untyped-def]
    statement = make().statement.lower()
    for forbidden in ("database", "root cause is", "caused by", "because the", "the bug is"):
        assert forbidden not in statement


# ----------------------------------------------------------------------------- the API


def _scan_with_failures(new_api, fake_verifier, fake_queue, owner_engine: Engine):  # type: ignore[no-untyped-def]
    api = registered(new_api)
    org, project = verified_project(api, fake_verifier, host=HOST)
    scan = api.post(f"{project_base(org, project)}/scans").json()["id"]
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO observations (org_id, scan_id, kind, severity, page_url, data) VALUES "
                "(:o, :s, 'http_error', 'error', 'https://shop.example.com/', CAST(:d AS jsonb)), "
                "(:o, :s, 'http_error', 'warning', 'https://shop.example.com/', CAST(:t AS jsonb))"
            ),
            {
                "o": org,
                "s": scan,
                "d": json.dumps(
                    {
                        "url": f"https://{HOST}/api/products",
                        "status": 500,
                        "request_id": "kmr-abc-1",
                    }
                ),
                "t": json.dumps({"url": "https://cdn.other.com/x.js", "status": 500}),
            },
        )
    return api, org, scan


def _logs_url(org: str, scan: str) -> str:
    return f"/v1/organizations/{org}/scans/{scan}"


def test_without_backend_data_the_notice_says_telemetry_is_unavailable(  # type: ignore[no-untyped-def]
    new_api, fake_verifier, fake_queue, owner_engine
) -> None:
    api, org, scan = _scan_with_failures(new_api, fake_verifier, fake_queue, owner_engine)
    body = api.get(f"{_logs_url(org, scan)}/backend-evidence").json()
    assert body["backend_telemetry_available"] is False
    assert "Backend telemetry is unavailable" in body["notice"]
    states = {s["key"]: s["state"] for s in body["sources"]}
    assert states == {
        "browser": "available",
        "api_checks": "not_connected",
        "customer_logs": "not_connected",
        "monitoring": "not_connected",
        "repository": "not_connected",
    }
    classes = {c["classification"] for c in body["correlations"]}
    assert classes == {"no_backend_data", "third_party"}


def test_uploading_matching_logs_turns_unknown_into_confirmed(  # type: ignore[no-untyped-def]
    new_api, fake_verifier, fake_queue, owner_engine
) -> None:
    api, org, scan = _scan_with_failures(new_api, fake_verifier, fake_queue, owner_engine)
    logs = "\n".join(
        [
            json.dumps(
                {
                    "request_id": "kmr-abc-1",
                    "status": 500,
                    "path": "/api/products",
                    "level": "error",
                    "msg": "boom jane@example.com",
                }
            ),
            "garbage line",
        ]
    )
    upload = api.post(f"{_logs_url(org, scan)}/backend-logs", {"format": "jsonl", "content": logs})
    assert upload.status_code == 200 and upload.json() == {
        "accepted": 1,
        "rejected": 1,
        "total_for_scan": 1,
    }
    body = api.get(f"{_logs_url(org, scan)}/backend-evidence").json()
    assert body["backend_telemetry_available"] is True and body["notice"] is None
    confirmed = next(c for c in body["correlations"] if c["classification"] == "backend_confirmed")
    assert set(confirmed["sources"]) == {"browser_observed", "customer_supplied"}
    with owner_engine.connect() as conn:
        stored = conn.execute(text("SELECT message FROM backend_log_entries")).scalar() or ""
    assert "jane@example.com" not in stored


def test_evidence_references_point_only_at_this_scans_stored_records(  # type: ignore[no-untyped-def]
    new_api, fake_verifier, fake_queue, owner_engine
) -> None:
    api, org, scan = _scan_with_failures(new_api, fake_verifier, fake_queue, owner_engine)
    api.post(
        f"{_logs_url(org, scan)}/backend-logs",
        {
            "format": "jsonl",
            "content": json.dumps(
                {"request_id": "kmr-abc-1", "status": 500, "path": "/api/products"}
            ),
        },
    )
    body = api.get(f"{_logs_url(org, scan)}/backend-evidence").json()
    refs = [(e["ref_type"], e["ref_id"]) for c in body["correlations"] for e in c["evidence"]]
    assert refs
    tables = {
        "observation": "observations",
        "log_entry": "backend_log_entries",
        "api_check_run": "api_check_runs",
    }
    with owner_engine.connect() as conn:
        for ref_type, ref_id in refs:
            row = conn.execute(
                text(f"SELECT org_id, scan_id FROM {tables[ref_type]} WHERE id = :i"),
                {"i": ref_id},  # noqa: S608
            ).one()
            assert (str(row.org_id), str(row.scan_id)) == (org, scan)


def test_other_tenants_cannot_read_upload_or_delete(
    new_api, fake_verifier, fake_queue, owner_engine
) -> None:  # type: ignore[no-untyped-def]
    api, org, scan = _scan_with_failures(new_api, fake_verifier, fake_queue, owner_engine)
    outsider = registered(new_api)
    other_org = outsider.create_org()
    base = _logs_url(org, scan)
    for response in (
        outsider.get(f"{base}/backend-evidence"),
        outsider.post(f"{base}/backend-logs", {"format": "jsonl", "content": "{}"}),
        outsider.delete(f"{base}/backend-logs"),
        # B's own org, A's scan id
        outsider.get(f"{_logs_url(other_org, scan)}/backend-evidence"),
        outsider.post(
            f"{_logs_url(other_org, scan)}/backend-logs", {"format": "jsonl", "content": "{}"}
        ),
    ):
        assert response.status_code == 404


def test_viewers_can_read_evidence_but_not_upload_or_delete(
    new_api, fake_verifier, fake_queue, owner_engine
) -> None:  # type: ignore[no-untyped-def]
    api, org, scan = _scan_with_failures(new_api, fake_verifier, fake_queue, owner_engine)
    viewer = registered(new_api)
    api.add_member(org, viewer.email, "viewer")  # type: ignore[arg-type]
    base = _logs_url(org, scan)
    assert viewer.get(f"{base}/backend-evidence").status_code == 200
    assert (
        viewer.post(f"{base}/backend-logs", {"format": "jsonl", "content": "{}"}).status_code == 403
    )
    assert viewer.delete(f"{base}/backend-logs").status_code == 403


def test_logs_can_be_removed_and_uploads_are_bounded(
    new_api, fake_verifier, fake_queue, owner_engine
) -> None:  # type: ignore[no-untyped-def]
    api, org, scan = _scan_with_failures(new_api, fake_verifier, fake_queue, owner_engine)
    base = _logs_url(org, scan)
    assert api.post(f"{base}/backend-logs", {"format": "jsonl", "content": ""}).status_code == 422
    assert api.post(f"{base}/backend-logs", {"format": "xml", "content": "x"}).status_code == 422
    assert (
        api.post(
            f"{base}/backend-logs", {"format": "jsonl", "content": "x" * 1_000_001}
        ).status_code
        == 422
    )
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO backend_log_entries (org_id, scan_id, path) SELECT :o, :s, '/p' FROM generate_series(1, :n)"
            ),
            {"o": org, "s": scan, "n": MAX_ENTRIES_PER_SCAN - 1},
        )
    result = api.post(
        f"{base}/backend-logs",
        {"format": "jsonl", "content": "\n".join(json.dumps({"path": f"/x{i}"}) for i in range(5))},
    ).json()
    assert result["accepted"] == 1 and result["rejected"] == 4  # the per-scan cap held
    assert api.delete(f"{base}/backend-logs").status_code == 204
    with owner_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM backend_log_entries")).scalar() == 0


def test_log_uploads_are_audited_without_their_content(
    new_api, fake_verifier, fake_queue, owner_engine
) -> None:  # type: ignore[no-untyped-def]
    api, org, scan = _scan_with_failures(new_api, fake_verifier, fake_queue, owner_engine)
    api.post(
        f"{_logs_url(org, scan)}/backend-logs",
        {
            "format": "jsonl",
            "content": json.dumps({"request_id": "kmr-abc-1", "msg": "SENSITIVE-PAYLOAD-TEXT"}),
        },
    )
    with owner_engine.connect() as conn:
        rows = (
            conn.execute(
                text("SELECT details::text FROM audit_events WHERE action = 'backend_logs.upload'")
            )
            .scalars()
            .all()
        )
    assert rows and all("SENSITIVE-PAYLOAD-TEXT" not in r for r in rows)
