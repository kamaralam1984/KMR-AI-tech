"""Gets a Playwright browser for the scanner.

`container` (the default) connects to the isolated browser container. Its launch options, the
proxy and the hardening flags are fixed on the server side (docker/scanner-browser/server.mjs), so
the worker cannot weaken them. `local` starts a browser on this machine behind the same egress
proxy. It exists for tests and is refused outside local/test (see validate_scan_settings).

Chromium is the default. Firefox and WebKit are optional engines (Settings.scan_engines_available)
used only when a scan asks for one that the deployment lists. Every engine must reach the
network only through the egress proxy, loopback included, and must not resolve names itself; the
tests in tests/test_scanner_engines.py check exactly that for each engine before it is listed.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlsplit

from playwright.async_api import Browser, async_playwright

from app.config import Settings

# Flags for the local browser. In the container the same list lives in server.mjs.
COMMON_ARGS = [
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-sync",
    "--no-first-run",
    "--mute-audio",
]

# Firefox has no host-resolver rule, so name resolution is kept away from the page by turning off
# prefetching and DNS-over-HTTPS and by forcing every request, loopback included, through the proxy.
FIREFOX_PREFS: dict[str, str | float | bool] = {
    "network.proxy.allow_hijacking_localhost": True,
    "network.proxy.no_proxies_on": "",
    "network.proxy.failover_timeout": 0,
    "network.dns.disablePrefetch": True,
    "network.dns.disablePrefetchFromHTTPS": True,
    "network.prefetch-next": False,
    "network.predictor.enabled": False,
    "network.http.speculative-parallel-limit": 0,
    "network.trr.mode": 5,
    "media.peerconnection.enabled": False,
    "browser.safebrowsing.enabled": False,
    "extensions.autoDisableScopes": 15,
}


class EngineUnavailableError(RuntimeError):
    """The engine is not configured or not listed for this deployment."""


def local_args(proxy_url: str) -> list[str]:
    """The browser resolves no names itself (the proxy does), except the proxy's own address."""
    proxy_host = urlsplit(proxy_url).hostname or ""
    return [f"--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE {proxy_host}", *COMMON_ARGS]


@asynccontextmanager
async def open_browser(
    settings: Settings, proxy_url: str | None = None, engine: str = "chromium"
) -> AsyncIterator[Browser]:
    if engine not in ("chromium", "firefox", "webkit"):
        raise EngineUnavailableError(f"Unknown browser engine: {engine}")
    if engine != "chromium" and engine not in settings.scan_engines_available:
        raise EngineUnavailableError(f"The {engine} browser is not enabled on this server.")
    proxy = proxy_url or settings.scan_proxy_url
    async with async_playwright() as playwright:
        browser_type = getattr(playwright, engine)
        if settings.scan_browser_mode == "container":
            ws = (
                settings.scan_browser_ws
                if engine == "chromium"
                else getattr(settings, f"scan_browser_ws_{engine}")
            )
            if not ws:
                raise EngineUnavailableError(f"The {engine} browser service is not configured.")
            browser = await browser_type.connect(ws, timeout=30_000)
        else:
            path = (
                settings.scan_local_chrome_path
                if engine == "chromium"
                else getattr(settings, f"scan_local_{engine}_path")
            )
            options: dict[str, Any] = {
                "executable_path": path,
                "headless": True,
                "proxy": {"server": proxy, "bypass": "<-loopback>"},
            }
            if engine == "chromium":
                options["args"] = local_args(proxy)
            elif engine == "firefox":
                options["firefox_user_prefs"] = FIREFOX_PREFS
                options["proxy"] = {"server": proxy}
            browser = await browser_type.launch(**options)
        try:
            yield browser
        finally:
            await browser.close()
