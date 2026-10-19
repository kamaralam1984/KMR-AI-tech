"""The worker end to end: real database, real Chrome, real egress proxy, flawed local site."""

import asyncio
import json
import shutil
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import Settings
from app.db.engine import create_engine
from app.scanning.browser import open_browser
from app.scanning.egress_proxy import ProxyConfig, start_server
from app.scanning.net_policy import NetPolicy
from app.scanning.storage import EvidenceStore
from app.scanning.worker import DbSink, WorkerDeps, process_scan, recover_scans
from tests.conftest import Seeded, TestDatabase
from tests.fakes import FakeQueue
from tests.fixture_site import FixtureSite

pytestmark = pytest.mark.anyio

CHROME = (
    shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
)
LIMITS = {
    "max_pages": 6,
    "max_requests": 400,
    "max_bytes": 50_000_000,
    "timeout_seconds": 60,
    "page_timeout_seconds": 8,
    "exclusions": [],
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def engine(pg: TestDatabase) -> AsyncIterator[AsyncEngine]:
    async_engine = create_engine(pg.app_url)
    yield async_engine
    await async_engine.dispose()


@pytest.fixture
async def site() -> AsyncIterator[FixtureSite]:
    fixture = FixtureSite()
    fixture.start()
    yield fixture
    fixture.stop()


@pytest.fixture
async def deps(engine: AsyncEngine, site: FixtureSite, tmp_path):  # type: ignore[no-untyped-def]
    if CHROME is None:
        pytest.skip("Unavailable: no Chrome/Chromium installed for the local scanner browser")
    proxy = await start_server(
        ProxyConfig(
            policy=NetPolicy(allowed_ports=(site.port, 80, 443), allowed_networks=("127.0.0.0/8",)),
            log=False,
        )
    )
    proxy_url = f"http://127.0.0.1:{proxy.sockets[0].getsockname()[1]}"
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        app_env="test",
        scan_browser_mode="local",
        scan_local_chrome_path=CHROME,
        scan_allow_private_targets=True,
        scan_allowed_ports=[site.port, 80, 443],
    )
    yield WorkerDeps(
        engine=engine,
        settings=settings,
        store=EvidenceStore(tmp_path),
        browser_factory=lambda s: open_browser(s, proxy_url),
    )
    proxy.close()


def make_scan(
    owner_engine: Engine,
    seeded: Seeded,
    site_url: str,
    host: str = "127.0.0.1",
    *,
    status: str = "queued",
    attempts: int = 0,
    **limit_overrides: Any,
) -> uuid.UUID:
    scan_id = uuid.uuid4()
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO scans (id, org_id, project_id, requested_by, status, site_url, "
                "allowed_host, limits, attempts) VALUES (:i, :o, :p, :u, :s, :url, :h, "
                "CAST(:l AS jsonb), :a)"
            ),
            {
                "i": scan_id,
                "o": seeded.org_a,
                "p": seeded.project_a,
                "u": seeded.user_a,
                "s": status,
                "url": site_url,
                "h": host,
                "a": attempts,
                "l": json.dumps({**LIMITS, **limit_overrides}),
            },
        )
    return scan_id


def scan_row(owner_engine: Engine, scan_id: uuid.UUID) -> Any:
    with owner_engine.connect() as conn:
        return (
            conn.execute(text("SELECT * FROM scans WHERE id = :i"), {"i": scan_id}).mappings().one()
        )


def count(owner_engine: Engine, sql: str, **params: Any) -> int:
    with owner_engine.connect() as conn:
        return int(conn.execute(text(sql), params).scalar() or 0)


async def test_a_scan_runs_to_completion_and_stores_tenant_scoped_results(  # type: ignore[no-untyped-def]
    deps, site, owner_engine, seeded
) -> None:
    scan_id = make_scan(owner_engine, seeded, f"{site.base}/")
    assert await process_scan(deps, scan_id) == "completed"

    row = scan_row(owner_engine, scan_id)
    assert row["status"] == "completed" and row["attempts"] == 1
    assert row["finished_at"] is not None and row["heartbeat_at"] is not None
    assert row["summary"]["pages_scanned"] >= 3 and row["summary"]["requests"] > 0
    with owner_engine.connect() as conn:
        observations = conn.execute(
            text("SELECT org_id, kind FROM observations WHERE scan_id = :i"), {"i": scan_id}
        ).all()
        evidence = conn.execute(
            text("SELECT org_id, storage_key FROM evidence WHERE scan_id = :i"), {"i": scan_id}
        ).all()
        audit = conn.execute(
            text(
                "SELECT action, outcome FROM audit_events WHERE target_id = :i ORDER BY created_at"
            ),
            {"i": str(scan_id)},
        ).all()
    assert {o.kind for o in observations} >= {"page", "a11y_issue", "console_error"}
    assert {o.org_id for o in observations} == {seeded.org_a}  # nothing written to another tenant
    assert evidence and {e.org_id for e in evidence} == {seeded.org_a}
    for item in evidence:
        data = deps.store.read(item.storage_key)
        assert data is not None and data.startswith(b"\x89PNG")
        assert item.storage_key.startswith(f"{seeded.org_a}/{scan_id}/")
    assert ("scan.finish", "success") in audit


async def test_processing_the_same_scan_twice_does_nothing_the_second_time(  # type: ignore[no-untyped-def]
    deps, site, owner_engine, seeded
) -> None:
    scan_id = make_scan(owner_engine, seeded, f"{site.base}/start-many")
    assert await process_scan(deps, scan_id) == "completed"
    before = count(owner_engine, "SELECT count(*) FROM observations WHERE scan_id = :i", i=scan_id)
    requests_before = len(site.requests)
    assert await process_scan(deps, scan_id) == "skipped"
    assert (
        count(owner_engine, "SELECT count(*) FROM observations WHERE scan_id = :i", i=scan_id)
        == before
    )
    assert len(site.requests) == requests_before
    assert scan_row(owner_engine, scan_id)["attempts"] == 1


async def test_a_running_scan_can_be_cancelled(deps, site, owner_engine, seeded) -> None:  # type: ignore[no-untyped-def]
    scan_id = make_scan(owner_engine, seeded, f"{site.base}/slow", page_timeout_seconds=25)
    task = asyncio.create_task(process_scan(deps, scan_id))
    for _ in range(100):  # wait until the worker has really started
        await asyncio.sleep(0.1)
        if scan_row(owner_engine, scan_id)["status"] == "running":
            break
    await asyncio.sleep(1.0)
    with owner_engine.begin() as conn:
        conn.execute(
            text("UPDATE scans SET cancel_requested_at = now() WHERE id = :i"), {"i": scan_id}
        )
    assert await asyncio.wait_for(task, 20) == "cancelled"
    row = scan_row(owner_engine, scan_id)
    assert row["status"] == "cancelled" and row["stopped_reason"] == "cancelled"


async def test_the_overall_time_limit_fails_the_scan(deps, site, owner_engine, seeded) -> None:  # type: ignore[no-untyped-def]
    scan_id = make_scan(
        owner_engine, seeded, f"{site.base}/slow", timeout_seconds=2, page_timeout_seconds=25
    )
    assert await asyncio.wait_for(process_scan(deps, scan_id), 30) == "failed"
    row = scan_row(owner_engine, scan_id)
    assert row["error_code"] == "timeout" and row["finished_at"] is not None


async def test_a_user_who_left_the_organization_cannot_run_a_scan(  # type: ignore[no-untyped-def]
    deps, site, owner_engine, seeded
) -> None:
    scan_id = make_scan(owner_engine, seeded, f"{site.base}/")
    with owner_engine.begin() as conn:
        conn.execute(text("DELETE FROM memberships WHERE user_id = :u"), {"u": seeded.user_a})

    @asynccontextmanager
    async def must_not_open(settings: Settings):  # type: ignore[no-untyped-def]
        raise AssertionError("the browser must not start for an unauthorized scan")
        yield  # pragma: no cover

    deps.browser_factory = must_not_open
    assert await process_scan(deps, scan_id) == "failed"
    assert scan_row(owner_engine, scan_id)["error_code"] == "not_authorized"
    assert site.requests == []


async def test_a_target_that_resolves_to_a_private_address_is_refused_before_any_traffic(  # type: ignore[no-untyped-def]
    deps, site, owner_engine, seeded
) -> None:
    deps.settings = Settings(_env_file=None, app_env="test")  # type: ignore[call-arg]  # production-like policy
    scan_id = make_scan(owner_engine, seeded, f"{site.base}/")
    assert await process_scan(deps, scan_id) == "failed"
    assert scan_row(owner_engine, scan_id)["error_code"] in ("blocked_address", "blocked_port")
    assert site.requests == []


async def test_an_unavailable_browser_fails_the_scan_cleanly(  # type: ignore[no-untyped-def]
    deps, site, owner_engine, seeded
) -> None:
    @asynccontextmanager
    async def broken(settings: Settings):  # type: ignore[no-untyped-def]
        raise OSError("browser container is down")
        yield  # pragma: no cover

    deps.browser_factory = broken
    scan_id = make_scan(owner_engine, seeded, f"{site.base}/")
    assert await process_scan(deps, scan_id) == "failed"
    row = scan_row(owner_engine, scan_id)
    assert row["error_code"] == "browser_unavailable" and "container" not in json.dumps(
        row["summary"]
    )


async def test_a_dead_worker_is_recovered_once_then_given_up_on(  # type: ignore[no-untyped-def]
    deps, site, owner_engine, seeded, engine
) -> None:
    queue = FakeQueue()
    scan_id = make_scan(
        owner_engine, seeded, f"{site.base}/start-many", status="running", attempts=1
    )
    with owner_engine.begin() as conn:
        conn.execute(
            text("UPDATE scans SET heartbeat_at = now() - interval '10 minutes' WHERE id = :i"),
            {"i": scan_id},
        )
    assert await recover_scans(engine, queue) == 1  # type: ignore[arg-type]
    assert queue.enqueued == [scan_id] and scan_row(owner_engine, scan_id)["status"] == "queued"
    assert await process_scan(deps, scan_id) == "completed"  # the retry succeeds
    assert scan_row(owner_engine, scan_id)["attempts"] == 2

    exhausted = make_scan(owner_engine, seeded, f"{site.base}/", status="running", attempts=2)
    with owner_engine.begin() as conn:
        conn.execute(
            text("UPDATE scans SET heartbeat_at = now() - interval '10 minutes' WHERE id = :i"),
            {"i": exhausted},
        )
    assert await recover_scans(engine, queue) == 0  # type: ignore[arg-type]
    row = scan_row(owner_engine, exhausted)
    assert row["status"] == "failed" and row["error_code"] == "worker_lost"


async def test_observations_are_capped_per_scan(engine, owner_engine, seeded, tmp_path) -> None:  # type: ignore[no-untyped-def]
    scan_id = make_scan(owner_engine, seeded, "http://127.0.0.1/", status="running")
    sink = DbSink(
        engine,
        EvidenceStore(tmp_path),
        org_id=seeded.org_a,
        user_id=seeded.user_a,
        scan_id=scan_id,
        max_observations=3,
    )
    for i in range(10):
        await sink.observation("note", "info", None, {"i": i})
    assert sink.stored == 3 and sink.dropped == 7
    assert (
        count(owner_engine, "SELECT count(*) FROM observations WHERE scan_id = :i", i=scan_id) == 3
    )


# ----------------------------------------------------------------------------- API-check phase


def add_check(owner_engine: Engine, seeded: Seeded, name: str = "Health", **over: Any) -> uuid.UUID:
    check_id = uuid.uuid4()
    columns = {"path": "/api/ok", "expected_status": 200, "max_latency_ms": 3000, **over}
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO api_checks (id, org_id, project_id, name, path, expected_status, "
                "max_latency_ms, auth_header_name, auth_secret_ct, created_by) VALUES "
                "(:i, :o, :p, :n, :path, :es, :ml, :h, :ct, :u)"
            ),
            {
                "i": check_id,
                "o": seeded.org_a,
                "p": seeded.project_a,
                "n": name,
                "path": columns["path"],
                "es": columns["expected_status"],
                "ml": columns["max_latency_ms"],
                "h": columns.get("header"),
                "ct": columns.get("ciphertext"),
                "u": seeded.user_a,
            },
        )
    return check_id


async def test_the_projects_api_checks_run_after_the_crawl_and_are_stored(  # type: ignore[no-untyped-def]
    deps, site, owner_engine, seeded
) -> None:
    from tests.fakes import FakeTransport

    transport = FakeTransport()
    deps.api_transport = transport
    add_check(owner_engine, seeded, "Health")
    scan_id = make_scan(owner_engine, seeded, f"{site.base}/start-many", max_pages=2)
    assert await process_scan(deps, scan_id) == "completed"

    assert transport.calls and transport.calls[0][1] == f"{site.base}/api/ok"
    assert transport.calls[0][2]["X-Request-ID"] == f"kmr-{str(scan_id)[:8]}-chk-1"
    with owner_engine.connect() as conn:
        run = conn.execute(
            text("SELECT status, triggered_by, scan_id, request_id FROM api_check_runs")
        ).one()
        obs = conn.execute(
            text(
                "SELECT severity, data FROM observations WHERE scan_id = :i AND kind = 'api_check'"
            ),
            {"i": scan_id},
        ).one()
    assert (run.status, run.triggered_by, run.scan_id) == ("passed", "scan", scan_id)
    assert (
        obs.severity == "info" and obs.data["name"] == "Health" and obs.data["status"] == "passed"
    )
    assert scan_row(owner_engine, scan_id)["summary"]["api_checks"] == {
        "run": 1,
        "passed": 1,
        "failed": 0,
        "error": 0,
    }


async def test_a_failing_check_is_an_error_observation_but_the_scan_still_completes(  # type: ignore[no-untyped-def]
    deps, site, owner_engine, seeded
) -> None:
    from app.scanning.api_checks import TransportResult
    from tests.fakes import FakeTransport

    deps.api_transport = FakeTransport(TransportResult(status=500, body=b"{}", latency_ms=12))
    add_check(owner_engine, seeded)
    scan_id = make_scan(owner_engine, seeded, f"{site.base}/start-many", max_pages=1)
    assert await process_scan(deps, scan_id) == "completed"
    with owner_engine.connect() as conn:
        obs = conn.execute(
            text(
                "SELECT severity, data FROM observations WHERE scan_id = :i AND kind = 'api_check'"
            ),
            {"i": scan_id},
        ).one()
    assert obs.severity == "error" and obs.data["reasons"][0]["code"] == "status_mismatch"
    assert scan_row(owner_engine, scan_id)["summary"]["api_checks"]["failed"] == 1


async def test_credentials_are_decrypted_only_for_the_request_and_never_stored_again(  # type: ignore[no-untyped-def]
    deps, site, owner_engine, seeded
) -> None:
    from cryptography.fernet import Fernet

    from app.security.secrets_box import encrypt_secret
    from tests.fakes import FakeTransport

    secret = "Bearer worker-secret-token-98765"  # noqa: S105 - throwaway test value
    deps.settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        app_env="test",
        scan_browser_mode="local",
        scan_local_chrome_path=CHROME,
        scan_allow_private_targets=True,
        scan_allowed_ports=[site.port, 80, 443],
        encryption_key=Fernet.generate_key().decode(),
    )
    transport = FakeTransport()
    deps.api_transport = transport
    add_check(
        owner_engine,
        seeded,
        header="Authorization",
        ciphertext=encrypt_secret(deps.settings, secret),
    )
    scan_id = make_scan(owner_engine, seeded, f"{site.base}/start-many", max_pages=1)
    assert await process_scan(deps, scan_id) == "completed"
    assert transport.calls[0][2]["Authorization"] == secret
    with owner_engine.connect() as conn:
        dump = " ".join(
            str(r) for r in conn.execute(text("SELECT data::text FROM observations")).scalars()
        ) + " ".join(
            str(r) for r in conn.execute(text("SELECT reasons::text FROM api_check_runs")).scalars()
        )
        dump += json.dumps(scan_row(owner_engine, scan_id)["summary"])
    assert "worker-secret-token" not in dump


async def test_api_checks_are_skipped_when_the_scan_itself_failed(  # type: ignore[no-untyped-def]
    deps, site, owner_engine, seeded
) -> None:
    from tests.fakes import FakeTransport

    transport = FakeTransport()
    deps.api_transport = transport
    add_check(owner_engine, seeded)
    scan_id = make_scan(owner_engine, seeded, f"{site.base}/redirect-out", max_pages=1)
    assert await process_scan(deps, scan_id) == "failed"
    assert transport.calls == []
    assert count(owner_engine, "SELECT count(*) FROM api_check_runs") == 0


async def test_the_scan_adds_request_ids_when_the_project_asked_for_them(  # type: ignore[no-untyped-def]
    deps, site, owner_engine, seeded
) -> None:
    scan_id = make_scan(
        owner_engine, seeded, f"{site.base}/about", max_pages=1, inject_request_id=True
    )
    assert await process_scan(deps, scan_id) == "completed"
    ids = [h.get("x-request-id") for h in site.request_headers]
    assert ids and all(i and i.startswith(f"kmr-{str(scan_id)[:8]}-") for i in ids)
    plain = make_scan(owner_engine, seeded, f"{site.base}/about", max_pages=1)
    site.request_headers.clear()
    # one active scan per project, so the first must be finished (it is) before the next
    assert await process_scan(deps, plain) == "completed"
    assert all("x-request-id" not in h for h in site.request_headers)


async def test_the_scans_engine_reaches_the_browser_factory(  # type: ignore[no-untyped-def]
    deps, site, owner_engine, seeded
) -> None:
    from app.scanning.browser import EngineUnavailableError

    asked: list[object] = []

    @asynccontextmanager
    async def factory(settings: Settings, engine: str = "chromium"):  # type: ignore[no-untyped-def]
        asked.append(engine)
        raise EngineUnavailableError("firefox is not enabled")
        yield  # pragma: no cover

    deps.browser_factory = factory
    scan_id = make_scan(
        owner_engine, seeded, f"{site.base}/", profile={"key": "desktop", "engine": "firefox"}
    )
    assert await process_scan(deps, scan_id) == "failed"
    assert asked == ["firefox"]
    assert scan_row(owner_engine, scan_id)["error_code"] == "engine_unavailable"
    assert site.requests == []
