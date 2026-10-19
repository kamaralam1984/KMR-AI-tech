import json

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.config import get_settings
from app.scanning.api_checks import TransportResult
from tests.helpers import project_base, registered, verified_project

SECRET = "sk-test-SUPER-SECRET-credential-12345"  # noqa: S105 - a throwaway test value


def _base(org: str, project: str) -> str:
    return f"{project_base(org, project)}/api-checks"


def _ready(new_api, fake_verifier):  # type: ignore[no-untyped-def]
    api = registered(new_api)
    org, project = verified_project(api, fake_verifier)
    return api, org, project, _base(org, project)


def _check(api, base, **over):  # type: ignore[no-untyped-def]
    body = {"name": "Products", "path": "/api/products", **over}
    return api.post(base, body)


# ----------------------------------------------------------------------------- definitions


@pytest.mark.parametrize(
    "path",
    [
        "//evil.example/x",
        "http://evil.example/",
        "api/no-slash",
        "/a b",
        "/x#frag",
        "/a\\b",
        "/a\nb",
    ],
)
def test_a_path_cannot_point_anywhere_but_this_site(new_api, fake_verifier, path: str) -> None:  # type: ignore[no-untyped-def]
    api, _org, _project, base = _ready(new_api, fake_verifier)
    assert _check(api, base, path=path).status_code == 422


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "nonsense"},
        {"$ref": "http://evil.example/schema.json"},
        {"properties": {"a": {"pattern": "(a+)+$"}}},
        {"type": "object", "padding": "x" * 20_000},
    ],
)
def test_unsafe_or_invalid_schemas_are_refused(new_api, fake_verifier, schema) -> None:  # type: ignore[no-untyped-def]
    api, _org, _project, base = _ready(new_api, fake_verifier)
    assert _check(api, base, response_schema=schema).status_code == 422


def test_credential_header_rules(new_api, fake_verifier, encryption_key) -> None:  # type: ignore[no-untyped-def]
    api, _org, _project, base = _ready(new_api, fake_verifier)
    for bad in (
        {"header_name": "Host", "value": "x"},
        {"header_name": "X-Request-ID", "value": "x"},
        {"header_name": "bad name", "value": "x"},
        {"header_name": "Authorization", "value": "line\nbreak"},
    ):
        assert _check(api, base, credentials=bad).status_code == 422, bad


def test_create_list_update_delete(new_api, fake_verifier) -> None:  # type: ignore[no-untyped-def]
    api, _org, _project, base = _ready(new_api, fake_verifier)
    created = _check(api, base, expected_status=200, max_latency_ms=500)
    assert created.status_code == 201 and created.json()["has_credentials"] is False
    assert _check(api, base).status_code == 409  # same name
    check_id = created.json()["id"]
    assert [c["name"] for c in api.get(base).json()] == ["Products"]
    updated = api.patch(f"{base}/{check_id}", {"max_latency_ms": 900, "path": "/api/v2/products"})
    assert updated.json()["max_latency_ms"] == 900 and updated.json()["path"] == "/api/v2/products"
    assert api.delete(f"{base}/{check_id}").status_code == 204
    assert api.get(base).json() == []


def test_a_project_has_a_limit_on_checks(new_api, fake_verifier) -> None:  # type: ignore[no-untyped-def]
    api, _org, _project, base = _ready(new_api, fake_verifier)
    for i in range(20):
        assert _check(api, base, name=f"c{i}").status_code == 201
    assert _check(api, base, name="one-too-many").status_code == 409


# ----------------------------------------------------------------------------- credentials


def test_credentials_are_encrypted_at_rest_and_never_returned(  # type: ignore[no-untyped-def]
    new_api, fake_verifier, encryption_key, owner_engine: Engine
) -> None:
    api, _org, _project, base = _ready(new_api, fake_verifier)
    creds = {"header_name": "Authorization", "value": SECRET}
    created = _check(api, base, credentials=creds)
    assert created.status_code == 201
    assert created.json()["has_credentials"] is True
    assert created.json()["auth_header_name"] == "Authorization"
    assert SECRET not in created.text and SECRET not in api.get(base).text
    with owner_engine.connect() as conn:
        row = conn.execute(text("SELECT auth_secret_ct FROM api_checks")).scalar()
        dump = json.dumps(
            [
                dict(r)
                for r in conn.execute(
                    text("SELECT details::text AS d FROM audit_events")
                ).mappings()
            ]
        )
    assert row is not None and SECRET.encode() not in bytes(row)
    assert SECRET not in dump


def test_credentials_are_refused_when_no_encryption_key_is_configured(
    new_api, fake_verifier
) -> None:  # type: ignore[no-untyped-def]
    api, _org, _project, base = _ready(new_api, fake_verifier)
    response = _check(api, base, credentials={"header_name": "Authorization", "value": SECRET})
    assert response.status_code == 503 and SECRET not in response.text
    assert api.get(base).json() == []  # nothing half-saved


def test_credentials_can_be_replaced_and_cleared(new_api, fake_verifier, encryption_key) -> None:  # type: ignore[no-untyped-def]
    api, _org, _project, base = _ready(new_api, fake_verifier)
    check_id = _check(api, base, credentials={"header_name": "X-Api-Key", "value": SECRET}).json()[
        "id"
    ]
    replaced = api.patch(
        f"{base}/{check_id}",
        {"credentials": {"header_name": "Authorization", "value": "other-secret-value"}},
    )
    assert replaced.json()["auth_header_name"] == "Authorization"
    cleared = api.patch(f"{base}/{check_id}", {"clear_credentials": True})
    assert cleared.json()["has_credentials"] is False and cleared.json()["auth_header_name"] is None


# ----------------------------------------------------------------------------- running


def _result(**over) -> TransportResult:  # type: ignore[no-untyped-def]
    return TransportResult(**{"status": 200, "body": b'{"id": 1}', "latency_ms": 30, **over})


def _run(api, base, check_id):  # type: ignore[no-untyped-def]
    return api.post(f"{base}/{check_id}/run")


def test_running_requires_a_verified_site(new_api, fake_verifier, fake_transport) -> None:  # type: ignore[no-untyped-def]
    api = registered(new_api)
    org = api.create_org()
    project = api.create_project(org)
    base = _base(org, project)
    check_id = _check(api, base).json()["id"]
    assert _run(api, base, check_id).status_code == 409
    api.request(
        "PUT", f"{project_base(org, project)}/site", {"site_url": "https://shop.example.com/"}
    )
    assert _run(api, base, check_id).status_code == 409  # saved but not verified
    assert fake_transport.calls == []


def test_a_healthy_response_passes_and_only_the_verified_host_is_called(
    new_api, fake_verifier, fake_transport
) -> None:  # type: ignore[no-untyped-def]
    api, _org, _project, base = _ready(new_api, fake_verifier)
    fake_transport.result = _result()
    run = _run(api, base, _check(api, base, path="/api/products?page=1").json()["id"]).json()
    assert run["status"] == "passed" and run["reasons"] == [] and run["http_status"] == 200
    method, url, headers = fake_transport.calls[0]
    assert (method, url) == ("GET", "https://shop.example.com/api/products?page=1")
    assert headers["X-Request-ID"].startswith("kmr-chk-")


@pytest.mark.parametrize(
    ("result", "code", "status"),
    [
        (_result(status=500), "status_mismatch", "failed"),
        (_result(status=302), "status_mismatch", "failed"),
        (_result(latency_ms=9000), "too_slow", "failed"),
        (_result(body_truncated=True), "response_too_large", "failed"),
        (_result(failure="timeout"), "timeout", "error"),
        (_result(failure="proxy_unavailable"), "proxy_unavailable", "error"),
        (_result(headers={"x-kmr-blocked": "blocked_address"}, status=403), "blocked", "error"),
    ],
)
def test_failures_are_reported_with_a_reason(
    new_api, fake_verifier, fake_transport, result, code, status
) -> None:  # type: ignore[no-untyped-def]
    api, _org, _project, base = _ready(new_api, fake_verifier)
    fake_transport.result = result
    run = _run(api, base, _check(api, base).json()["id"]).json()
    assert run["status"] == status
    assert code in [r["code"] for r in run["reasons"]]


def test_schema_violations_name_the_path_and_rule_but_never_the_value(
    new_api, fake_verifier, fake_transport, owner_engine: Engine
) -> None:  # type: ignore[no-untyped-def]
    api, _org, _project, base = _ready(new_api, fake_verifier)
    schema = {
        "type": "object",
        "required": ["id", "name"],
        "properties": {"id": {"type": "integer"}},
    }
    check_id = _check(api, base, response_schema=schema).json()["id"]
    fake_transport.result = _result(body=b'{"id": "SECRET-VALUE-123"}')
    run = _run(api, base, check_id)
    assert run.status_code == 200 and run.json()["status"] == "failed"
    reason = next(r for r in run.json()["reasons"] if r["code"] == "schema_mismatch")
    assert {"path": "$.id", "rule": "type"} in reason["violations"]
    assert {"path": "$", "rule": "required"} in reason["violations"]
    assert "SECRET-VALUE-123" not in run.text
    with owner_engine.connect() as conn:
        stored = conn.execute(text("SELECT reasons::text FROM api_check_runs")).scalar() or ""
    assert "SECRET-VALUE-123" not in stored


def test_invalid_json_when_a_schema_is_expected(new_api, fake_verifier, fake_transport) -> None:  # type: ignore[no-untyped-def]
    api, _org, _project, base = _ready(new_api, fake_verifier)
    check_id = _check(api, base, response_schema={"type": "object"}).json()["id"]
    fake_transport.result = _result(body=b"<html>")
    assert "invalid_json" in [r["code"] for r in _run(api, base, check_id).json()["reasons"]]


def test_rejected_credentials_say_so_without_leaking_them(
    new_api, fake_verifier, fake_transport, encryption_key
) -> None:  # type: ignore[no-untyped-def]
    api, _org, _project, base = _ready(new_api, fake_verifier)
    check_id = _check(
        api, base, credentials={"header_name": "Authorization", "value": SECRET}
    ).json()["id"]
    fake_transport.result = _result(status=401, body=b'{"error": "bad token sk-test-SUPER-SECRET"}')
    run = _run(api, base, check_id)
    codes = [r["code"] for r in run.json()["reasons"]]
    assert "auth_rejected" in codes and "status_mismatch" not in codes
    assert SECRET not in run.text
    sent = fake_transport.calls[0][2]
    assert sent["Authorization"] == SECRET  # decrypted only for the request itself


def test_unreadable_credentials_are_an_error_not_a_silent_unauthenticated_call(
    new_api, fake_verifier, fake_transport, monkeypatch, encryption_key
) -> None:  # type: ignore[no-untyped-def]
    from cryptography.fernet import Fernet

    api, _org, _project, base = _ready(new_api, fake_verifier)
    check_id = _check(
        api, base, credentials={"header_name": "Authorization", "value": SECRET}
    ).json()["id"]
    monkeypatch.setenv("KMR_ENCRYPTION_KEY", Fernet.generate_key().decode())  # key was rotated
    get_settings.cache_clear()
    fake_transport.result = _result()
    run = _run(api, base, check_id).json()
    assert run["status"] == "error" and "credentials_unreadable" in [
        r["code"] for r in run["reasons"]
    ]
    assert "Authorization" not in fake_transport.calls[0][2]


def test_runs_are_stored_listed_and_shown_as_the_last_result(
    new_api, fake_verifier, fake_transport
) -> None:  # type: ignore[no-untyped-def]
    api, _org, _project, base = _ready(new_api, fake_verifier)
    check_id = _check(api, base).json()["id"]
    fake_transport.result = _result(status=500)
    _run(api, base, check_id)
    fake_transport.result = _result()
    _run(api, base, check_id)
    runs = api.get(f"{base}/{check_id}/runs").json()
    assert [r["status"] for r in runs] == ["passed", "failed"]
    assert api.get(base).json()[0]["last_run"]["status"] == "passed"


def test_runs_are_rate_limited(new_api, fake_verifier, fake_transport, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    api, _org, _project, base = _ready(new_api, fake_verifier)
    check_id = _check(api, base).json()["id"]
    monkeypatch.setenv("KMR_API_CHECK_RUNS_PER_HOUR", "2")
    get_settings.cache_clear()
    assert [_run(api, base, check_id).status_code for _ in range(4)] == [200, 200, 429, 429]


# ----------------------------------------------------------------------------- who may do what


def test_viewers_can_look_but_not_change_or_run(new_api, fake_verifier, fake_transport) -> None:  # type: ignore[no-untyped-def]
    owner, org, _project, base = _ready(new_api, fake_verifier)
    viewer = registered(new_api)
    owner.add_member(org, viewer.email, "viewer")  # type: ignore[arg-type]
    check_id = _check(owner, base).json()["id"]
    assert viewer.get(base).status_code == 200
    assert viewer.get(f"{base}/{check_id}/runs").status_code == 200
    assert _check(viewer, base, name="x").status_code == 403
    assert _run(viewer, base, check_id).status_code == 403
    assert viewer.delete(f"{base}/{check_id}").status_code == 403
    assert viewer.patch(f"{base}/{check_id}", {"name": "z"}).status_code == 403


def test_other_tenants_and_other_projects_cannot_reach_a_check(
    new_api, fake_verifier, fake_transport
) -> None:  # type: ignore[no-untyped-def]
    owner, org, project, base = _ready(new_api, fake_verifier)
    check_id = _check(owner, base).json()["id"]
    outsider = registered(new_api)
    for response in (
        outsider.get(base),
        _check(outsider, base, name="x"),
        _run(outsider, base, check_id),
        outsider.delete(f"{base}/{check_id}"),
        outsider.get(f"{base}/{check_id}/runs"),
    ):
        assert response.status_code == 404
    other_project = owner.create_project(org, "Second")
    other_base = _base(org, other_project)
    # A real check id, but addressed through a different project of the same organization.
    assert _run(owner, other_base, check_id).status_code == 404
    assert owner.get(f"{other_base}/{check_id}/runs").status_code == 404
