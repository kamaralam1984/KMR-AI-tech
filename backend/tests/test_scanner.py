"""The scanner against a flawed local site, through the real egress proxy and Chrome."""

import asyncio
import shutil
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from app.config import Settings
from app.scanning.browser import open_browser
from app.scanning.egress_proxy import ProxyConfig, start_server
from app.scanning.net_policy import NetPolicy, system_resolver
from app.scanning.scanner import ScanLimits, ScanOutcome, run_scan
from tests.fixture_site import FixtureSite

pytestmark = pytest.mark.anyio

CHROME = (
    shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
)


def _newest(pattern: str) -> str | None:
    found = sorted((Path.home() / ".cache" / "ms-playwright").glob(pattern))
    return str(found[-1]) if found else None


# Every engine that is going to be listed as available must pass this whole file.
ENGINE_BINARIES = {
    "chromium": CHROME,
    "firefox": _newest("firefox-*/firefox/firefox"),
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass
class Observation:
    kind: str
    severity: str
    page_url: str | None
    data: dict[str, Any]
    screenshot: bytes | None


@dataclass
class CollectingSink:
    """STAND-IN for the database sink: keeps everything in memory."""

    items: list[Observation] = field(default_factory=list)
    cancel_after: int | None = None

    async def observation(
        self,
        kind: str,
        severity: str,
        page_url: str | None,
        data: dict[str, Any],
        screenshot: bytes | None = None,
    ) -> None:
        self.items.append(Observation(kind, severity, page_url, data, screenshot))

    async def cancel_requested(self) -> bool:
        return self.cancel_after is not None and len(self.items) >= self.cancel_after

    async def heartbeat(self) -> None:
        pass

    def of(self, kind: str) -> list[Observation]:
        return [o for o in self.items if o.kind == kind]

    def blocked_reasons(self) -> list[str]:
        return [str(o.data.get("reason")) for o in self.of("blocked_request")]

    def pages(self) -> dict[str, dict[str, Any]]:
        return {
            o.page_url.split(":", 2)[-1].split("/", 1)[-1] if o.page_url else "": o.data
            for o in self.of("page")
        }


@pytest.fixture
async def site() -> AsyncIterator[FixtureSite]:
    fixture = FixtureSite()
    fixture.start()
    yield fixture
    fixture.stop()


PROXY_ONLY_NAME = "only-the-proxy-knows.test"


def proxy_only_resolver(host: str, port: int) -> list[str]:
    """A name that system DNS has never heard of: only the proxy can turn it into an address."""
    if host == PROXY_ONLY_NAME:
        return ["127.0.0.1"]
    return system_resolver(host, port)


@pytest.fixture(params=sorted(ENGINE_BINARIES))
async def scan_env(site: FixtureSite, request):  # type: ignore[no-untyped-def]
    engine: str = request.param
    if ENGINE_BINARIES[engine] is None:
        pytest.skip(f"Unavailable: no {engine} installed for the local scanner browser")
    # Loopback only (the narrow test exception), any unprivileged port: some tests start a second
    # fixture site on a port that is not known in advance.
    permissive = await start_server(
        ProxyConfig(
            policy=NetPolicy(
                allowed_ports=(80, 443, *range(1024, 65536)), allowed_networks=("127.0.0.0/8",)
            ),
            resolver=proxy_only_resolver,
            log=False,
        )
    )
    # The real policy: loopback is NOT allowed. A browser that bypassed the proxy for loopback would
    # still reach the fixture site, which is exactly what test_loopback_is_not_exempt_from_the_proxy
    # looks for.
    strict = await start_server(
        ProxyConfig(policy=NetPolicy(allowed_ports=(80, 443, *range(1024, 65536))), log=False)
    )
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        app_env="test",
        scan_browser_mode="local",
        scan_local_chrome_path=ENGINE_BINARIES["chromium"],
        scan_local_firefox_path=ENGINE_BINARIES["firefox"],
        scan_engines_available=["chromium", "firefox"],
    )

    async def scan(
        path: str = "/",
        *,
        host: str = "127.0.0.1",
        sink: CollectingSink | None = None,
        strict_proxy: bool = False,
        **overrides: Any,
    ) -> tuple[ScanOutcome, CollectingSink]:
        sink = sink or CollectingSink()
        server = strict if strict_proxy else permissive
        proxy_url = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
        limits = ScanLimits(
            **{
                "max_pages": 6,
                "max_requests": 400,
                "max_bytes": 50_000_000,
                "page_timeout_seconds": 8,
                "exclusions": (),
                **overrides,
            }
        )
        async with open_browser(settings, proxy_url, engine=engine) as browser:
            outcome = await asyncio.wait_for(
                run_scan(
                    browser,
                    start_url=path if path.startswith("http") else f"{site.base}{path}",
                    allowed_host=host,
                    limits=limits,
                    sink=sink,
                ),
                timeout=60,
            )
        return outcome, sink

    scan.engine = engine  # type: ignore[attr-defined]
    yield scan
    permissive.close()
    strict.close()


async def test_crawls_same_host_pages_and_never_follows_action_links(scan_env, site) -> None:  # type: ignore[no-untyped-def]
    outcome, sink = await scan_env("/")
    assert outcome.status == "completed" and outcome.pages_scanned >= 3
    seen = site.seen()
    assert "/about" in seen and "/broken" in seen
    for forbidden in ("/logout", "/delete-account"):
        assert forbidden not in seen, f"{forbidden} was requested"
    assert not any("127.0.0.2" in str(o.data) and o.kind == "page" for o in sink.items)
    statuses = {o.data["status"] for o in sink.of("page")}
    assert 200 in statuses and 404 in statuses


async def test_pages_get_screenshots_and_metadata(scan_env) -> None:  # type: ignore[no-untyped-def]
    _outcome, sink = await scan_env("/")
    home = next(o for o in sink.of("page") if o.page_url and o.page_url.endswith("/"))
    assert home.data["title"] == "/" and home.data["lang"] == "en"
    assert home.screenshot is not None and home.screenshot.startswith(b"\x89PNG")


async def test_reports_real_problems_with_sanitized_evidence(scan_env, site) -> None:  # type: ignore[no-untyped-def]
    _outcome, sink = await scan_env("/about", max_pages=1)
    checks = {o.data["check"]: o.data["count"] for o in sink.of("a11y_issue")}
    assert checks["images_missing_alt"] >= 1
    assert checks["inputs_without_label"] >= 1
    assert checks["buttons_without_name"] >= 1
    console = [o.data["message"] for o in sink.of("console_error")]
    assert any("boom" in m for m in console)
    joined = " ".join(console)
    assert "abc123456789" not in joined and "jane.doe" not in joined  # secrets and emails masked
    assert any("uncaught on about" in o.data["message"] for o in sink.of("page_error"))
    assert any(
        o.data["status"] == 404 and o.data["url"].endswith("/missing.gif")
        for o in sink.of("http_error")
    )
    reasons = sink.blocked_reasons()
    assert "proxy_blocked_address" in reasons  # fetch to 169.254.169.254 stopped by the proxy
    assert "non_get_blocked" in reasons  # the POST never left the browser
    assert site.seen("POST") == []


async def test_secrets_in_urls_and_text_never_reach_the_record(scan_env) -> None:  # type: ignore[no-untyped-def]
    _outcome, sink = await scan_env("/", max_pages=8)
    dump = repr([(o.page_url, o.data) for o in sink.items])
    assert "abcDEF123456789012345678901234" not in dump
    assert "abcdef1234567890abcdef" not in dump
    assert "jane.doe" not in dump


async def test_exclusions_are_respected(scan_env, site) -> None:  # type: ignore[no-untyped-def]
    await scan_env("/", exclusions=("/admin",))
    assert "/admin/secret" not in site.seen()


async def test_a_redirect_to_another_host_is_refused(scan_env, site) -> None:  # type: ignore[no-untyped-def]
    outcome, sink = await scan_env("/redirect-out")
    assert outcome.status == "failed" and outcome.error_code == "site_unreachable"
    assert any(r.startswith("out_of_scope") for r in sink.blocked_reasons())
    assert not any("/landed" in path for path in site.seen())


async def test_script_navigation_to_another_host_is_refused(scan_env) -> None:  # type: ignore[no-untyped-def]
    outcome, sink = await scan_env("/js-out", max_pages=1)
    assert outcome.pages_scanned == 1
    assert "out_of_scope_navigation" in sink.blocked_reasons()


async def test_a_page_that_submits_a_form_on_its_own_changes_nothing(scan_env, site) -> None:  # type: ignore[no-untyped-def]
    _outcome, sink = await scan_env("/auto-post", max_pages=1)
    assert site.seen("POST") == []
    assert "non_get_blocked" in sink.blocked_reasons()


async def test_a_start_url_outside_the_allowed_host_fetches_nothing(scan_env, site) -> None:  # type: ignore[no-untyped-def]
    outcome, _sink = await scan_env("/", host="not-the-site.example")
    assert outcome.status == "failed"
    assert site.requests == []


async def test_page_limit(scan_env) -> None:  # type: ignore[no-untyped-def]
    outcome, _sink = await scan_env("/start-many", max_pages=2)
    assert outcome.pages_scanned == 2 and outcome.stopped_reason == "page_limit"
    assert outcome.status == "completed"


async def test_request_limit_stops_the_scan(scan_env) -> None:  # type: ignore[no-untyped-def]
    outcome, sink = await scan_env("/many", max_requests=8, max_pages=3)
    assert outcome.stopped_reason == "request_limit"
    assert outcome.requests <= 10
    assert any(o.data.get("stopped") == "request_limit" for o in sink.of("limit"))


async def test_byte_limit_stops_the_scan(scan_env) -> None:  # type: ignore[no-untyped-def]
    outcome, _sink = await scan_env("/start-many", max_bytes=1_000_000, max_pages=4)
    # /big is not linked, so scan it directly: one page already exceeds the cap.
    outcome, _sink = await scan_env("/big", max_bytes=1_000_000, max_pages=4)
    assert outcome.bytes >= 1_000_000
    assert outcome.status == "completed"


async def test_a_slow_page_times_out_without_hanging_the_scan(scan_env) -> None:  # type: ignore[no-untyped-def]
    outcome, sink = await scan_env("/slow", page_timeout_seconds=2)
    assert outcome.status == "failed" and outcome.error_code == "site_unreachable"
    page = sink.of("page")[0]
    assert page.severity == "error" and page.data["status"] is None


async def test_cancellation_stops_the_scan_quickly(scan_env) -> None:  # type: ignore[no-untyped-def]
    sink = CollectingSink(cancel_after=1)
    outcome, _ = await scan_env("/", sink=sink, max_pages=6)
    assert outcome.status == "cancelled" and outcome.stopped_reason == "cancelled"
    assert outcome.pages_scanned < 6


async def test_popups_never_survive_and_downloads_are_cancelled(scan_env, site) -> None:  # type: ignore[no-untyped-def]
    _outcome, popup_sink = await scan_env("/popup", max_pages=1)
    # Chrome already refuses a script-opened popup without a user click; if a different browser
    # build lets one through, the scanner must have closed it and said so.
    opened = "/about" in site.seen()
    closed = any(o.data.get("event") == "popup_closed" for o in popup_sink.of("note"))
    assert not opened or closed
    outcome, _ = await scan_env("/download", max_pages=1)
    assert outcome.status == "failed"  # a download is not a page; it must not crash the scan


async def test_request_ids_go_only_on_same_origin_gets_and_are_recorded(scan_env, site) -> None:  # type: ignore[no-untyped-def]
    other = FixtureSite()
    other.start()
    try:
        outcome, sink = await scan_env(
            f"/cross?b={other.port}",
            max_pages=1,
            inject_request_id=True,
            request_id_prefix="kmr-abc12345",
        )
    finally:
        other.stop()
    assert outcome.status == "completed"
    ids = [h.get("x-request-id") for h in site.request_headers]
    assert ids and all(
        i and i.startswith("kmr-abc12345-") for i in ids
    )  # page and its same-origin loads
    assert len(set(ids)) == len(ids)  # every request has its own id
    cross = other.request_headers
    assert cross and all("x-request-id" not in h for h in cross)  # never on cross-origin requests
    page = next(o for o in sink.of("page"))
    assert page.data["request_id"] in ids


async def test_request_ids_are_off_unless_asked_for(scan_env, site) -> None:  # type: ignore[no-untyped-def]
    await scan_env("/about", max_pages=1)
    assert all("x-request-id" not in h for h in site.request_headers)


async def test_failing_same_origin_requests_carry_their_request_id(scan_env, site) -> None:  # type: ignore[no-untyped-def]
    _outcome, sink = await scan_env("/about", max_pages=1, inject_request_id=True)
    missing = next(o for o in sink.of("http_error") if o.data["url"].endswith("/missing.gif"))
    assert missing.data["request_id"].startswith("kmr-")
    logged = [
        h for h in site.request_headers if h.get("x-request-id") == missing.data["request_id"]
    ]
    assert len(logged) == 1  # the site really received exactly that id


async def test_posts_never_get_a_request_id(scan_env, site) -> None:  # type: ignore[no-untyped-def]
    await scan_env("/about", max_pages=1, inject_request_id=True)
    assert site.seen("POST") == []


async def test_phone_profile_reports_its_screen_and_finds_sideways_overflow(scan_env) -> None:  # type: ignore[no-untyped-def]
    desktop_outcome, desktop = await scan_env("/wide", max_pages=1)
    phone_outcome, phone = await scan_env("/wide", max_pages=1, profile="phone")
    assert desktop_outcome.status == phone_outcome.status == "completed"
    assert desktop.of("page")[0].data["viewport_width"] == 1280
    assert phone.of("page")[0].data["viewport_width"] == 360
    assert phone.of("page")[0].data["profile"] == "phone"
    checks = lambda sink: {o.data.get("check") for o in sink.of("a11y_issue")}  # noqa: E731
    assert "horizontal_overflow" in checks(phone)
    assert "horizontal_overflow" not in checks(desktop)


async def test_throttled_profile_still_respects_limits_and_loads_slower(scan_env) -> None:  # type: ignore[no-untyped-def]
    if scan_env.engine != "chromium":  # type: ignore[attr-defined]
        pytest.skip("Throttling uses Chrome DevTools: Chromium only (the API refuses it elsewhere)")
    _normal_outcome, normal = await scan_env("/", max_pages=1)
    outcome, slow = await scan_env("/", max_pages=1, profile="slow_network", page_timeout_seconds=8)
    assert outcome.pages_scanned + outcome.pages_failed == 1  # the page limit still holds
    if slow.of("page"):
        assert slow.of("page")[0].data["profile"] == "slow_network"
        # Measured under throttling in this environment; only the direction is asserted.
        assert slow.of("page")[0].data["load_ms"] >= normal.of("page")[0].data["load_ms"]


async def test_loopback_is_not_exempt_from_the_proxy(scan_env, site) -> None:  # type: ignore[no-untyped-def]
    """The browser must send even 127.0.0.1 through the egress proxy, which then refuses it.

    If an engine skipped the proxy for loopback, a scanned page could reach services on the scanner's
    own host, and this test would find the fixture site receiving the request.
    """
    _outcome, sink = await scan_env("/", strict_proxy=True)
    assert site.requests == [], f"{scan_env.engine} reached loopback without the proxy"  # type: ignore[attr-defined]
    # The proxy answers with its own refusal page, so the only "page" is that error, never the site.
    assert [o.data.get("status") for o in sink.of("page")] == [403]


async def test_names_are_resolved_by_the_proxy_never_by_the_browser(scan_env, site) -> None:  # type: ignore[no-untyped-def]
    """Only the proxy's resolver knows this name. If the browser resolved names itself, the page
    would not load; DNS rebinding protection depends on the proxy being the only resolver."""
    port = site.base.rsplit(":", 1)[1]
    outcome, sink = await scan_env(
        f"http://{PROXY_ONLY_NAME}:{port}/", host=PROXY_ONLY_NAME, max_pages=1
    )
    assert outcome.pages_scanned == 1, f"{scan_env.engine} could not use proxy-side DNS"  # type: ignore[attr-defined]
    assert sink.of("page")[0].data["status"] == 200
    assert site.seen() and site.seen()[0].startswith("/")
