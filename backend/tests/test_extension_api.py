"""Phase 13 server side: tokens, fail-closed ingest, analysis, and tenant isolation."""

import base64
import json
import re
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.config import get_settings
from tests.helpers import project_base, registered, verified_project

HOST = "shop.example.com"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture(autouse=True)
def _evidence_dir(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("KMR_EVIDENCE_DIR", str(tmp_path / "evidence"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _token(api, org: str, name: str = "Laptop", days: int = 30) -> dict:  # type: ignore[no-untyped-def,type-arg]
    response = api.post(f"/v1/organizations/{org}/extension-tokens", {"name": name, "days": days})
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def _setup(new_api, fake_verifier):  # type: ignore[no-untyped-def]
    api = registered(new_api)
    org, project = verified_project(api, fake_verifier, host=HOST)
    return api, org, project, _token(api, org)["token"]


def _payload(project: str, **over) -> dict:  # type: ignore[no-untyped-def,type-arg]
    page = f"https://{HOST}/about"
    body = {
        "project_id": project,
        "page_url": page,
        "client": {
            "version": "0.1.0",
            "redaction_version": "1",
            "redacted_counts": {"email": 1},
            "masked_regions": 0,
        },
        "observations": [
            {
                "kind": "page",
                "page_url": page,
                "data": {"title": "About us", "lang": "en", "h1_count": 1},
            },
            {
                "kind": "a11y_issue",
                "page_url": page,
                "data": {
                    "check": "images_missing_alt",
                    "count": 2,
                    "selectors": [
                        "body > main > img:nth-of-type(1)",
                        "body > main > img:nth-of-type(2)",
                    ],
                },
            },
            {
                "kind": "page_error",
                "page_url": page,
                "data": {"message": "TypeError: Cannot read properties of undefined (reading 'x')"},
            },
        ],
    }
    body.update(over)
    return body


def _post(api, token: str, body: dict):  # type: ignore[no-untyped-def,type-arg]
    return api.request("POST", "/ext/v1/evidence", body, csrf=False, headers=_bearer(token))


def _scan_count(owner_engine: Engine) -> int:
    with owner_engine.begin() as conn:
        return int(conn.execute(text("SELECT count(*) FROM scans")).scalar() or 0)


# ------------------------------------------------------------------------------- tokens


def test_token_is_shown_once_and_only_its_hash_is_stored(
    new_api, fake_verifier, owner_engine: Engine
) -> None:  # type: ignore[no-untyped-def]
    api, org, _project, token = _setup(new_api, fake_verifier)
    listing = api.get(f"/v1/organizations/{org}/extension-tokens").json()
    assert len(listing) == 1 and "token" not in listing[0]
    assert token.startswith("kmr_xt_") and listing[0]["token_prefix"] == token[:11]
    with owner_engine.begin() as conn:
        stored = conn.execute(text("SELECT token_hash, token_prefix FROM extension_tokens")).one()
    assert bytes(stored.token_hash) != token.encode() and token not in str(stored)


def test_revoked_expired_and_garbage_tokens_are_refused(
    new_api, fake_verifier, owner_engine: Engine
) -> None:  # type: ignore[no-untyped-def]
    api, org, _project, token = _setup(new_api, fake_verifier)
    assert api.request("GET", "/ext/v1/projects", headers=_bearer(token)).status_code == 200
    assert api.request("GET", "/ext/v1/projects", headers=_bearer("kmr_xt_nope")).status_code == 401
    assert new_api().request("GET", "/ext/v1/projects").status_code == 401

    other = _token(api, org, "second")
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE extension_tokens SET expires_at = now() - interval '1 minute' WHERE id = :i"
            ),
            {"i": other["id"]},
        )
    assert (
        api.request("GET", "/ext/v1/projects", headers=_bearer(other["token"])).status_code == 401
    )

    tid = api.get(f"/v1/organizations/{org}/extension-tokens").json()[-1]["id"]
    revoked = api.post(f"/v1/organizations/{org}/extension-tokens/{tid}/revoke")
    assert revoked.status_code == 200 and revoked.json()["revoked"] is True
    for t in (token,):
        if _is_revoked(api, org, tid, t):
            assert api.request("GET", "/ext/v1/projects", headers=_bearer(t)).status_code == 401


def _is_revoked(api, org, tid, token) -> bool:  # type: ignore[no-untyped-def]
    listing = api.get(f"/v1/organizations/{org}/extension-tokens").json()
    return any(row["id"] == tid and row["revoked"] for row in listing)


def test_a_token_stops_working_when_its_user_leaves_the_organization(
    new_api, fake_verifier, owner_engine: Engine
) -> None:  # type: ignore[no-untyped-def]
    owner = registered(new_api)
    org, _project = verified_project(owner, fake_verifier, host=HOST)
    member = registered(new_api)
    assert owner.add_member(org, member.email or "", "member").status_code in (200, 201)
    token = _token(member, org)["token"]
    assert member.request("GET", "/ext/v1/projects", headers=_bearer(token)).status_code == 200
    with owner_engine.begin() as conn:
        conn.execute(text("DELETE FROM memberships WHERE user_id = :u"), {"u": member.user_id})
    assert member.request("GET", "/ext/v1/projects", headers=_bearer(token)).status_code == 403


def test_viewers_cannot_make_tokens_and_members_see_only_their_own(new_api, fake_verifier) -> None:  # type: ignore[no-untyped-def]
    owner = registered(new_api)
    org, _p = verified_project(owner, fake_verifier, host=HOST)
    viewer = registered(new_api)
    member = registered(new_api)
    owner.add_member(org, viewer.email or "", "viewer")
    owner.add_member(org, member.email or "", "member")
    denied = viewer.post(f"/v1/organizations/{org}/extension-tokens", {"name": "x", "days": 5})
    assert denied.status_code == 403
    _token(owner, org, "owners")
    _token(member, org, "members")
    assert len(member.get(f"/v1/organizations/{org}/extension-tokens").json()) == 1
    assert len(owner.get(f"/v1/organizations/{org}/extension-tokens").json()) == 2


def test_every_ext_route_rejects_anonymous_requests(api_app, new_api) -> None:  # type: ignore[no-untyped-def]
    routes = [
        (m.upper(), p)
        for p, ops in api_app.openapi()["paths"].items()
        if p.startswith("/ext/v1")
        for m in ops.keys() & {"get", "post", "put", "patch", "delete"}
    ]
    assert len(routes) >= 3
    anonymous = new_api()
    for method, template in routes:
        path = re.sub(r"\{[a-z_]+\}", lambda _: str(uuid.uuid4()), template)
        assert anonymous.request(method, path, json={}, csrf=False).status_code == 401, template


# ------------------------------------------------------------------------------- ingest


def test_projects_list_only_includes_verified_projects(new_api, fake_verifier) -> None:  # type: ignore[no-untyped-def]
    api, org, project, token = _setup(new_api, fake_verifier)
    api.create_project(org, "Unverified")
    listing = api.request("GET", "/ext/v1/projects", headers=_bearer(token)).json()
    assert [p["id"] for p in listing] == [project] and listing[0]["site_host"] == HOST


def test_clean_evidence_is_stored_with_selectors_and_an_audit_entry(
    new_api, fake_verifier, owner_engine: Engine
) -> None:  # type: ignore[no-untyped-def]
    api, org, project, token = _setup(new_api, fake_verifier)
    response = _post(
        api, token, _payload(project, screenshot_png_b64=base64.b64encode(PNG).decode())
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["observations_stored"] == 3 and body["screenshot_stored"] is True
    with owner_engine.begin() as conn:
        scan = conn.execute(
            text("SELECT limits, status, requested_by FROM scans WHERE id = :s"),
            {"s": body["scan_id"]},
        ).one()
        a11y = conn.execute(
            text("SELECT data FROM observations WHERE scan_id = :s AND kind = 'a11y_issue'"),
            {"s": body["scan_id"]},
        ).scalar_one()
        audit = conn.execute(
            text("SELECT count(*) FROM audit_events WHERE action = 'extension.ingest'")
        ).scalar()
    assert scan.limits["source"] == "extension" and scan.status == "completed"
    assert a11y["selectors"] == [
        "body > main > img:nth-of-type(1)",
        "body > main > img:nth-of-type(2)",
    ]
    assert a11y["origin"] == "browser_extension" and audit == 1


@pytest.mark.parametrize(
    ("page_url", "client_flag"),
    [
        (f"https://{HOST}/login", False),
        (f"https://{HOST}/checkout/pay", False),
        (f"https://{HOST}/account/verify-otp", False),
        ("https://mail.google.com/mail/u/0/", False),
        ("https://www.hdfcbank.com/personal", False),
        ("https://portal.example.gov.in/", False),
        (f"https://{HOST}/about", True),
    ],
)
def test_sensitive_pages_are_refused_and_nothing_is_stored(
    new_api, fake_verifier, owner_engine: Engine, page_url: str, client_flag: bool
) -> None:  # type: ignore[no-untyped-def]
    api, _org, project, token = _setup(new_api, fake_verifier)
    body = _payload(project, page_url=page_url)
    body["client"]["sensitive_page"] = client_flag
    response = _post(api, token, body)
    assert response.status_code == 422, response.text
    assert _scan_count(owner_engine) == 0


FAKE_PII = [
    "write to jane.doe@example.com for details",
    "call +91 98765 43210 today",
    "card 4111 1111 1111 1111 declined",
    "aadhaar 1234 5678 9012",
    "PAN ABCDE1234F",
    "Authorization: Bearer abcdefghijklmnop1234",
    "session eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.abc123def456",
    "password=hunter2hunter2",
    "Name: Rahul Kumar",
    "token 9f8a7b6c5d4e3f2a1b0c9d8e7f6a5b4c3d2e1f0a",
]


@pytest.mark.parametrize("leaky", FAKE_PII)
def test_payloads_that_still_hold_personal_data_are_rejected_whole(
    new_api, fake_verifier, owner_engine: Engine, leaky: str
) -> None:  # type: ignore[no-untyped-def]
    api, _org, project, token = _setup(new_api, fake_verifier)
    body = _payload(project)
    body["observations"].append(
        {"kind": "console_error", "page_url": body["page_url"], "data": {"message": leaky}}
    )
    response = _post(api, token, body)
    assert response.status_code == 422, response.text
    assert "personal data or secrets" in response.json()["error"]["message"]
    assert leaky not in response.text  # the value is never echoed back
    assert _scan_count(owner_engine) == 0
    with owner_engine.begin() as conn:
        audits = conn.execute(
            text("SELECT details FROM audit_events WHERE action = 'extension.rejected'")
        ).all()
    assert len(audits) == 1 and leaky not in json.dumps(audits[0].details)


def test_redacted_placeholders_pass_and_query_values_must_be_removed(
    new_api, fake_verifier
) -> None:  # type: ignore[no-untyped-def]
    api, _org, project, token = _setup(new_api, fake_verifier)
    ok = _payload(project)
    ok["observations"].append(
        {
            "kind": "http_error",
            "page_url": ok["page_url"],
            "data": {"url": f"https://{HOST}/api/orders/:id?page=[V]", "status": 500},
        }
    )
    ok["observations"].append(
        {
            "kind": "console_error",
            "page_url": ok["page_url"],
            "data": {"message": "failed for [EMAIL] with token=[TOKEN] and [PHONE]"},
        }
    )
    assert _post(api, token, ok).status_code == 201
    bad = _payload(project)
    bad["observations"].append(
        {
            "kind": "http_error",
            "page_url": bad["page_url"],
            "data": {"url": f"https://{HOST}/api/orders?customer=alice", "status": 500},
        }
    )
    assert _post(api, token, bad).status_code == 422


def test_pages_must_belong_to_a_verified_project_on_its_own_host(new_api, fake_verifier) -> None:  # type: ignore[no-untyped-def]
    api, org, project, token = _setup(new_api, fake_verifier)
    other_host = _payload(project, page_url="https://evil.example.org/about")
    for obs in other_host["observations"]:
        obs["page_url"] = "https://evil.example.org/about"
    assert _post(api, token, other_host).status_code == 409
    unverified = api.create_project(org, "Draft")
    assert _post(api, token, _payload(unverified)).status_code == 409
    assert _post(api, token, _payload(str(uuid.uuid4()))).status_code == 404


def test_one_tenants_token_cannot_write_into_anothers_project(
    new_api, fake_verifier, owner_engine: Engine
) -> None:  # type: ignore[no-untyped-def]
    _a, _org_a, project_a, _token_a = _setup(new_api, fake_verifier)
    b = registered(new_api)
    org_b, _project_b = verified_project(b, fake_verifier, host="other.example.com")
    token_b = _token(b, org_b)["token"]
    assert _post(b, token_b, _payload(project_a)).status_code == 404
    assert _scan_count(owner_engine) == 0


def test_unknown_fields_and_oversized_payloads_are_rejected(new_api, fake_verifier) -> None:  # type: ignore[no-untyped-def]
    api, _org, project, token = _setup(new_api, fake_verifier)
    body = _payload(project)
    body["cookies"] = "a=b"
    assert _post(api, token, body).status_code == 422
    body = _payload(project)
    body["observations"] = body["observations"] * 60
    assert _post(api, token, body).status_code == 422
    body = _payload(project, screenshot_png_b64=base64.b64encode(b"not a png").decode())
    assert _post(api, token, body).status_code == 422


def test_unknown_data_fields_are_dropped_before_storage(
    new_api, fake_verifier, owner_engine: Engine
) -> None:  # type: ignore[no-untyped-def]
    api, _org, project, token = _setup(new_api, fake_verifier)
    body = _payload(project)
    body["observations"][0]["data"]["secret_note"] = "hello"  # noqa: S105 - a plain test string
    body["observations"][1]["data"]["selectors"].append("not a selector!!; drop table")
    scan = _post(api, token, body).json()["scan_id"]
    with owner_engine.begin() as conn:
        rows = conn.execute(
            text("SELECT kind, data FROM observations WHERE scan_id = :s"), {"s": scan}
        ).all()
    data = {r.kind: r.data for r in rows}
    assert "secret_note" not in data["page"]
    assert all("drop" not in s for s in data["a11y_issue"]["selectors"])


# ------------------------------------------------------------------------------- analyze


def test_analysis_returns_labeled_findings_with_the_selectors_to_highlight(
    new_api, fake_verifier
) -> None:  # type: ignore[no-untyped-def]
    api, org, project, token = _setup(new_api, fake_verifier)
    scan = _post(api, token, _payload(project)).json()["scan_id"]
    response = api.request(
        "POST", f"/ext/v1/analyze/{scan}", {}, csrf=False, headers=_bearer(token)
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["is_ai"] is False and body["provider_label"] == "Built-in rules (not AI)"
    alt = next(f for f in body["findings"] if "alt" in f["title"].lower() or f["selectors"])
    assert alt["selectors"] == [
        "body > main > img:nth-of-type(1)",
        "body > main > img:nth-of-type(2)",
    ]
    assert alt["web_path"].startswith("/issues/") and alt["observed"]
    assert alt["fix_targets"] == [
        {"selector": sel, "check": "images_missing_alt"} for sel in alt["selectors"]
    ]
    assert "Nothing on your site" in body["notice"]
    # The issue exists in the normal issue list, so "Fix this" goes through the normal flow.
    listed = api.get(f"/v1/organizations/{org}/issues").json()
    assert alt["issue_id"] in [i["id"] for i in listed]


def test_a_token_cannot_analyze_ordinary_scans_or_other_users_uploads(
    new_api, fake_verifier, owner_engine: Engine
) -> None:  # type: ignore[no-untyped-def]
    api, org, project, token = _setup(new_api, fake_verifier)
    regular = api.post(f"{project_base(org, project)}/scans").json()["id"]
    with owner_engine.begin() as conn:
        conn.execute(text("UPDATE scans SET status = 'completed' WHERE id = :s"), {"s": regular})
    call = lambda t, s: api.request(  # noqa: E731
        "POST", f"/ext/v1/analyze/{s}", {}, csrf=False, headers=_bearer(t)
    )
    assert call(token, regular).status_code == 404

    uploaded = _post(api, token, _payload(project)).json()["scan_id"]
    teammate = registered(new_api)
    api.add_member(org, teammate.email or "", "member")
    other_token = _token(teammate, org)["token"]
    assert call(other_token, uploaded).status_code == 404


def test_the_daily_scan_quota_also_covers_extension_uploads(
    new_api, fake_verifier, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    api, _org, project, token = _setup(new_api, fake_verifier)
    monkeypatch.setenv("KMR_SCAN_DAILY_QUOTA_PER_ORG", "1")
    get_settings.cache_clear()
    assert _post(api, token, _payload(project)).status_code == 201
    assert _post(api, token, _payload(project)).status_code == 429


def test_the_payload_the_extension_really_builds_is_accepted_by_the_server(
    new_api, fake_verifier, owner_engine: Engine
) -> None:  # type: ignore[no-untyped-def]
    """extension/test/fixtures/payload.json is asserted equal to the extension's own output."""
    fixture = Path(__file__).resolve().parents[2] / "extension/test/fixtures/payload.json"
    if not fixture.exists():
        pytest.skip("Unavailable: extension fixture not present")
    api, _org, project, token = _setup(new_api, fake_verifier)
    body = json.loads(fixture.read_text())
    body["project_id"] = project
    response = _post(api, token, body)
    assert response.status_code == 201, response.text
    scan = response.json()["scan_id"]
    result = api.request("POST", f"/ext/v1/analyze/{scan}", {}, csrf=False, headers=_bearer(token))
    assert result.status_code == 200, result.text
    selectors = {s for f in result.json()["findings"] for s in f["selectors"]}
    assert "#hero" in selectors  # the image finding points at the element to highlight
