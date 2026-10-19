import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.config import get_settings
from app.scanning.storage import EvidenceStore
from tests.helpers import project_base, registered, verified_project

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


def _scans(org: str) -> str:
    return f"/v1/organizations/{org}/scans"


def _ready(new_api, fake_verifier):  # type: ignore[no-untyped-def]
    api = registered(new_api)
    org, project = verified_project(api, fake_verifier)
    return api, org, project, f"{project_base(org, project)}/scans"


def test_scanning_requires_a_site_and_a_verified_owner(new_api, fake_verifier) -> None:  # type: ignore[no-untyped-def]
    api = registered(new_api)
    org = api.create_org()
    project = api.create_project(org)
    path = f"{project_base(org, project)}/scans"
    assert api.post(path).status_code == 409  # no site yet
    api.request("PUT", f"{project_base(org, project)}/site", {"site_url": "https://example.com/"})
    response = api.post(path)
    assert response.status_code == 409 and "own" in response.json()["error"]["message"]


def test_create_scan_is_queued_with_a_limits_snapshot(new_api, fake_verifier, fake_queue) -> None:  # type: ignore[no-untyped-def]
    api, org, project, path = _ready(new_api, fake_verifier)
    response = api.post(path)
    assert response.status_code == 202
    scan = response.json()
    assert scan["status"] == "queued" and scan["site_url"] == "https://shop.example.com/"
    assert scan["limits"]["max_pages"] == 10 and scan["limits"]["max_requests"] > 0
    assert scan["project_id"] == project
    assert fake_queue.enqueued == [uuid.UUID(scan["id"])]


def test_only_one_scan_can_be_active_per_project(new_api, fake_verifier, fake_queue) -> None:  # type: ignore[no-untyped-def]
    api, _org, _project, path = _ready(new_api, fake_verifier)
    assert api.post(path).status_code == 202
    assert api.post(path).status_code == 409


def test_idempotency_key_returns_the_original_scan(
    new_api, fake_verifier, fake_queue, owner_engine: Engine
) -> None:  # type: ignore[no-untyped-def]
    api, _org, _project, path = _ready(new_api, fake_verifier)
    first = api.post(path, headers={"Idempotency-Key": "retry-1"})
    again = api.post(path, headers={"Idempotency-Key": "retry-1"})
    assert first.status_code == 202 and again.status_code == 200
    assert first.json()["id"] == again.json()["id"]
    assert len(fake_queue.enqueued) == 1
    with owner_engine.begin() as conn:
        conn.execute(text("UPDATE scans SET status = 'completed', finished_at = now()"))
    # Even once finished, the same key never starts a second scan.
    assert api.post(path, headers={"Idempotency-Key": "retry-1"}).json()["id"] == first.json()["id"]
    assert api.post(path, headers={"Idempotency-Key": "retry-2"}).status_code == 202


def test_a_failing_queue_still_accepts_the_scan_for_later_recovery(
    new_api, fake_verifier, fake_queue
) -> None:  # type: ignore[no-untyped-def]
    api, _org, _project, path = _ready(new_api, fake_verifier)
    fake_queue.broken = True
    response = api.post(path)
    assert response.status_code == 202 and response.json()["status"] == "queued"


def test_daily_quota_per_organization(
    new_api, fake_verifier, fake_queue, owner_engine: Engine, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    api, _org, _project, path = _ready(new_api, fake_verifier)
    monkeypatch.setenv("KMR_SCAN_DAILY_QUOTA_PER_ORG", "2")
    get_settings.cache_clear()
    for _ in range(2):
        assert api.post(path).status_code == 202
        with owner_engine.begin() as conn:
            conn.execute(text("UPDATE scans SET status = 'completed', finished_at = now()"))
    assert api.post(path).status_code == 429


def test_viewers_cannot_scan_and_outsiders_see_nothing(new_api, fake_verifier, fake_queue) -> None:  # type: ignore[no-untyped-def]
    owner, org, _project, path = _ready(new_api, fake_verifier)
    viewer, outsider = registered(new_api), registered(new_api)
    owner.add_member(org, viewer.email, "viewer")  # type: ignore[arg-type]
    scan = owner.post(path).json()
    assert viewer.post(path).status_code == 403
    assert viewer.get(f"{_scans(org)}/{scan['id']}").status_code == 200
    assert viewer.post(f"{_scans(org)}/{scan['id']}/cancel").status_code == 403
    for request in (
        outsider.post(path),
        outsider.get(_scans(org)),
        outsider.get(f"{_scans(org)}/{scan['id']}"),
        outsider.get(f"{_scans(org)}/{scan['id']}/observations"),
        outsider.post(f"{_scans(org)}/{scan['id']}/cancel"),
    ):
        assert request.status_code == 404


def test_scan_ids_do_not_cross_tenants(new_api, fake_verifier, fake_queue) -> None:  # type: ignore[no-untyped-def]
    a, org_a, _pa, path_a = _ready(new_api, fake_verifier)
    b = registered(new_api)
    org_b, _pb = verified_project(b, fake_verifier, host="other.example.com")
    scan_a = a.post(path_a).json()
    # B's own organization, A's scan id: still 404.
    assert b.get(f"{_scans(org_b)}/{scan_a['id']}").status_code == 404
    assert b.get(f"{_scans(org_b)}/{scan_a['id']}/observations").status_code == 404
    assert b.post(f"{_scans(org_b)}/{scan_a['id']}/cancel").status_code == 404
    assert b.get(_scans(org_b)).json() == []


def test_listing_and_pagination_bounds(
    new_api, fake_verifier, fake_queue, owner_engine: Engine
) -> None:  # type: ignore[no-untyped-def]
    api, org, project, path = _ready(new_api, fake_verifier)
    for _ in range(3):
        api.post(path)
        with owner_engine.begin() as conn:
            conn.execute(text("UPDATE scans SET status = 'completed', finished_at = now()"))
    listing = api.get(_scans(org)).json()
    assert len(listing) == 3 and listing[0]["project_name"] == "Site"
    assert len(api.get(f"{_scans(org)}?limit=2").json()) == 2
    assert api.get(f"{_scans(org)}?limit=0").status_code == 422
    assert api.get(f"{_scans(org)}?limit=101").status_code == 422
    assert len(api.get(f"{project_base(org, project)}/scans").json()) == 3


def test_cancel_queued_and_running_scans(
    new_api, fake_verifier, fake_queue, owner_engine: Engine
) -> None:  # type: ignore[no-untyped-def]
    api, org, _project, path = _ready(new_api, fake_verifier)
    queued = api.post(path).json()
    cancelled = api.post(f"{_scans(org)}/{queued['id']}/cancel").json()
    assert cancelled["status"] == "cancelled" and cancelled["finished_at"] is not None
    assert api.post(f"{_scans(org)}/{queued['id']}/cancel").status_code == 409

    running = api.post(path).json()
    with owner_engine.begin() as conn:
        conn.execute(
            text("UPDATE scans SET status = 'running' WHERE id = :s"), {"s": running["id"]}
        )
    flagged = api.post(f"{_scans(org)}/{running['id']}/cancel").json()
    assert flagged["status"] == "running" and flagged["cancel_requested"] is True


def _seed_results(owner_engine: Engine, org: str, scan: str) -> tuple[str, str]:
    evidence = str(uuid.uuid4())
    key = EvidenceStore.key_for(uuid.UUID(org), uuid.UUID(scan), uuid.UUID(evidence))
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO evidence (id, org_id, scan_id, kind, storage_key, content_type, "
                "size_bytes, sha256) VALUES (:i, :o, :s, 'screenshot', :k, 'image/png', :n, 'x')"
            ),
            {"i": evidence, "o": org, "s": scan, "k": key, "n": len(PNG)},
        )
        for kind, severity in (
            ("page", "info"),
            ("console_error", "error"),
            ("http_error", "warning"),
        ):
            conn.execute(
                text(
                    "INSERT INTO observations (org_id, scan_id, kind, severity, page_url, data) "
                    "VALUES (:o, :s, :k, :v, 'https://shop.example.com/', '{\"n\": 1}')"
                ),
                {"o": org, "s": scan, "k": kind, "v": severity},
            )
    return evidence, key


def test_scan_detail_counts_and_observation_filters(
    new_api, fake_verifier, fake_queue, owner_engine: Engine
) -> None:  # type: ignore[no-untyped-def]
    api, org, _project, path = _ready(new_api, fake_verifier)
    scan = api.post(path).json()
    _seed_results(owner_engine, org, scan["id"])
    detail = api.get(f"{_scans(org)}/{scan['id']}").json()
    assert detail["counts"] == {"page": 1, "console_error": 1, "http_error": 1}
    assert detail["severity_counts"] == {"info": 1, "error": 1, "warning": 1}
    base = f"{_scans(org)}/{scan['id']}/observations"
    assert api.get(base).json()["total"] == 3
    assert [o["kind"] for o in api.get(f"{base}?kind=console_error").json()["items"]] == [
        "console_error"
    ]
    assert api.get(f"{base}?severity=warning").json()["total"] == 1
    assert len(api.get(f"{base}?limit=1").json()["items"]) == 1
    assert api.get(f"{base}?limit=501").status_code == 422


def test_screenshots_are_served_only_through_the_tenant_checked_route(  # type: ignore[no-untyped-def]
    new_api, fake_verifier, fake_queue, owner_engine: Engine, api_app, tmp_path
) -> None:
    store = EvidenceStore(tmp_path)
    api_app.state.evidence_store = store
    api, org, _project, path = _ready(new_api, fake_verifier)
    scan = api.post(path).json()
    evidence, key = _seed_results(owner_engine, org, scan["id"])
    store.save(key, PNG)
    url = f"{_scans(org)}/{scan['id']}/evidence/{evidence}"

    response = api.get(url)
    assert response.status_code == 200 and response.content == PNG
    assert response.headers["content-type"] == "image/png"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "no-store" in response.headers["cache-control"]

    outsider = registered(new_api)
    assert outsider.get(url).status_code == 404
    other = uuid.uuid4()
    assert api.get(f"{_scans(org)}/{other}/evidence/{evidence}").status_code == 404
    assert api.get(f"{_scans(org)}/{scan['id']}/evidence/{uuid.uuid4()}").status_code == 404


def test_a_bad_storage_key_in_the_database_cannot_read_arbitrary_files(  # type: ignore[no-untyped-def]
    new_api, fake_verifier, fake_queue, owner_engine: Engine, api_app, tmp_path
) -> None:
    secret = tmp_path.parent / "secret.png"
    secret.write_bytes(PNG)
    api_app.state.evidence_store = EvidenceStore(tmp_path)
    api, org, _project, path = _ready(new_api, fake_verifier)
    scan = api.post(path).json()
    evidence, _key = _seed_results(owner_engine, org, scan["id"])
    with owner_engine.begin() as conn:
        conn.execute(
            text("UPDATE evidence SET storage_key = '../secret.png' WHERE id = :i"), {"i": evidence}
        )
    assert api.get(f"{_scans(org)}/{scan['id']}/evidence/{evidence}").status_code == 404


def test_missing_evidence_file_is_a_404(
    new_api, fake_verifier, fake_queue, owner_engine: Engine, api_app, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    api_app.state.evidence_store = EvidenceStore(tmp_path)
    api, org, _project, path = _ready(new_api, fake_verifier)
    scan = api.post(path).json()
    evidence, _key = _seed_results(owner_engine, org, scan["id"])
    assert api.get(f"{_scans(org)}/{scan['id']}/evidence/{evidence}").status_code == 404


def test_scan_actions_are_audited(new_api, fake_verifier, fake_queue, owner_engine: Engine) -> None:  # type: ignore[no-untyped-def]
    api, org, _project, path = _ready(new_api, fake_verifier)
    scan = api.post(path).json()
    api.post(f"{_scans(org)}/{scan['id']}/cancel")
    with owner_engine.connect() as conn:
        actions = {
            r[0]
            for r in conn.execute(
                text("SELECT action FROM audit_events WHERE org_id = :o"), {"o": org}
            )
        }
    assert {"scan.create", "scan.cancel"} <= actions


@pytest.mark.parametrize("value", ["../x", "a/b", "a b", ""])
def test_evidence_store_refuses_keys_outside_its_format(value: str, tmp_path) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError):
        EvidenceStore(tmp_path).read(value)
