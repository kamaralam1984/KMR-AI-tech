"""The passive crawler. It drives a browser it did not start, through a sink it does not own.

Rules this module enforces, independent of the network layer:
  * Only GET/HEAD/OPTIONS requests leave the browser. Anything else (form posts, XHR writes) is
    blocked and recorded, so a scan cannot change anything on the site.
  * Top-level navigations stay on the verified host and outside excluded paths. Pages on other
    hosts, including after redirects, are refused. Sub-resources may come from any public host
    (the egress proxy guarantees "public"), because pages need their CDNs to render.
  * Limits on pages, requests, bytes and per-page time; popups, dialogs and downloads are shut.
  * Nothing is clicked and no form is submitted. Links that look like actions (logout, delete,
    unsubscribe) are never followed.
Everything recorded goes through `sanitize` first.
"""

import asyncio
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol
from urllib.parse import urldefrag, urlsplit

from playwright.async_api import Browser, BrowserContext, Page, Request, Response, Route
from playwright.async_api import Error as PlaywrightError

from app.scanning.profiles import apply_throttling, context_options, get_profile
from app.scanning.sanitize import sanitize_text, sanitize_url

USER_AGENT = "KMRAI-Scanner/1.0 (passive website check; +https://kmr.ai/scanner)"
MAX_DEPTH = 2
MAX_PER_KIND = 100
MAX_SCREENSHOT_BYTES = 2_000_000
ALLOWED_METHODS = {"GET", "HEAD", "OPTIONS"}
_ACTION_LINK = re.compile(
    r"(log-?out|sign-?out|log-?off|delete|remove|unsubscribe|destroy|cancel-?account|deactivate)",
    re.IGNORECASE,
)

Outcome = Literal["completed", "failed", "cancelled"]


@dataclass(frozen=True)
class ScanLimits:
    max_pages: int
    max_requests: int
    max_bytes: int
    page_timeout_seconds: int
    exclusions: tuple[str, ...] = ()
    # Adds "X-Request-ID: <prefix>-<n>" to same-origin GET requests so a customer's backend logs can
    # be matched to what the browser saw. Never added to cross-origin requests (it would trigger
    # CORS preflights and could break them).
    inject_request_id: bool = False
    request_id_prefix: str = "kmr"
    # Browser environment (screen, touch, CPU and network speed). See app/scanning/profiles.py.
    profile: str = "desktop"


class ScanSink(Protocol):
    async def observation(
        self,
        kind: str,
        severity: str,
        page_url: str | None,
        data: dict[str, Any],
        screenshot: bytes | None = None,
    ) -> None: ...

    async def cancel_requested(self) -> bool: ...

    async def heartbeat(self) -> None: ...


@dataclass
class ScanOutcome:
    status: Outcome
    stopped_reason: str | None = None
    error_code: str | None = None
    pages_scanned: int = 0
    pages_failed: int = 0
    requests: int = 0
    bytes: int = 0
    duration_ms: int = 0
    api_checks: dict[str, int] | None = None

    def summary(self) -> dict[str, Any]:
        extra = {"api_checks": self.api_checks} if self.api_checks is not None else {}
        return {
            **extra,
            "pages_scanned": self.pages_scanned,
            "pages_failed": self.pages_failed,
            "requests": self.requests,
            "bytes": self.bytes,
            "duration_ms": self.duration_ms,
        }


@dataclass
class _State:
    requests: int = 0
    bytes: int = 0
    document_status: int | None = None  # status of the last top-level document response
    request_ids: dict[Request, str] = field(default_factory=dict)
    request_seq: int = 0
    stop_reason: str | None = None
    cancelled: bool = False
    counts: dict[str, int] = field(default_factory=dict)
    dropped: dict[str, int] = field(default_factory=dict)


# ----------------------------------------------------------------------------- pure helpers


def is_excluded(url: str, exclusions: tuple[str, ...]) -> bool:
    path = urlsplit(url).path or "/"
    return any(
        path == item or path.startswith(item.rstrip("/") + "/") or path.startswith(item)
        for item in exclusions
    )


def crawl_candidate(url: str, allowed_host: str, exclusions: tuple[str, ...]) -> str | None:
    """A normalized, same-host, non-excluded, non-action link, or None."""
    clean, _ = urldefrag(url.strip())
    parts = urlsplit(clean)
    if parts.scheme not in ("http", "https") or parts.hostname != allowed_host:
        return None
    if parts.username or parts.password:
        return None
    if _ACTION_LINK.search(parts.path) or _ACTION_LINK.search(parts.query):
        return None
    if is_excluded(clean, exclusions):
        return None
    return clean


_PAGE_SCRIPT = """
() => {
  const text = (el) => (el.textContent || "").trim();
  const visible = (el) => {
    const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none";
  };
  const labelFor = (el) =>
    el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
  const labelled = (el) =>
    el.getAttribute("aria-label") || el.getAttribute("aria-labelledby") ||
    el.getAttribute("title") || labelFor(el) || el.closest("label");
  const hasAltImage = (el) => (el.querySelector("img[alt]:not([alt=''])") ? "x" : "");
  const name = (el) =>
    el.getAttribute("aria-label") || el.getAttribute("aria-labelledby") ||
    el.getAttribute("title") || text(el) || hasAltImage(el);
  const images = [...document.images].filter((i) => !i.hasAttribute("alt") && visible(i));
  const fieldSelector = [
    "input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=image])",
    "select", "textarea",
  ].join(", ");
  const inputs = [...document.querySelectorAll(fieldSelector)]
    .filter(visible).filter((el) => !labelled(el));
  const buttons = [...document.querySelectorAll("button, [role=button]")]
    .filter(visible).filter((el) => !name(el));
  const links = [...document.querySelectorAll("a[href]")]
    .filter(visible).filter((el) => !name(el));
  const nav = performance.getEntriesByType("navigation")[0];
  return {
    title: document.title || "",
    lang: document.documentElement.getAttribute("lang") || "",
    description: (document.querySelector("meta[name=description]") || {}).content || "",
    h1Count: document.querySelectorAll("h1").length,
    hasViewport: !!document.querySelector("meta[name=viewport]"),
    imagesMissingAlt: images.length,
    inputsWithoutLabel: inputs.length,
    buttonsWithoutName: buttons.length,
    linksWithoutName: links.length,
    loadMs: nav ? Math.round(nav.loadEventEnd) : null,
    // On an emulated phone the layout viewport grows to fit wide content, so the screen's own
    // width is the reference for "wider than the screen".
    viewportWidth: Math.min(window.innerWidth, screen.width),
    overflowX: document.documentElement.scrollWidth > Math.min(window.innerWidth, screen.width) + 1,
    hrefs: [...document.querySelectorAll("a[href]")].slice(0, 300).map((a) => a.href),
  };
}
"""


def _with_id(data: dict[str, Any], request_id: str | None) -> dict[str, Any]:
    if request_id:
        data["request_id"] = request_id
    return data


def _inject_here(request: Request, allowed_host: str, main_frame: Any) -> bool:
    """True for GETs to the verified host that are same-origin with the page making them."""
    if request.method.upper() != "GET":
        return False
    target = urlsplit(request.url)
    if target.scheme not in ("http", "https") or target.hostname != allowed_host:
        return False
    if request.is_navigation_request() and request.frame == main_frame:
        return True  # a top-level navigation is not subject to CORS
    frame_url = urlsplit(request.frame.url)
    return frame_url.scheme in ("http", "https") and frame_url.netloc == target.netloc


# ----------------------------------------------------------------------------- the crawler


async def run_scan(
    browser: Browser,
    *,
    start_url: str,
    allowed_host: str,
    limits: ScanLimits,
    sink: ScanSink,
) -> ScanOutcome:
    started = time.monotonic()
    state = _State()
    outcome = ScanOutcome(status="completed")
    background: set[asyncio.Task[Any]] = set()

    def spawn(coro: Any) -> None:
        task = asyncio.create_task(coro)
        background.add(task)
        task.add_done_callback(background.discard)

    async def record(
        kind: str,
        severity: str,
        page_url: str | None,
        data: dict[str, Any],
        screenshot: bytes | None = None,
    ) -> None:
        if state.counts.get(kind, 0) >= MAX_PER_KIND and kind != "limit":
            state.dropped[kind] = state.dropped.get(kind, 0) + 1
            return
        state.counts[kind] = state.counts.get(kind, 0) + 1
        await sink.observation(
            kind, severity, sanitize_url(page_url) if page_url else None, data, screenshot
        )

    profile = get_profile(limits.profile)
    engine = browser.browser_type.name
    context = await browser.new_context(
        **context_options(profile, engine),
        accept_downloads=False,
        service_workers="block",
        permissions=[],
        ignore_https_errors=False,
        user_agent=USER_AGENT,
        locale="en-US",
    )
    page = await context.new_page()
    await apply_throttling(context, page, profile, engine)
    main_frame = page.main_frame

    async def handle_route(route: Route) -> None:
        request = route.request
        url = request.url
        try:
            scheme = urlsplit(url).scheme
            if scheme not in ("http", "https", "data", "blob", "about"):
                await record(
                    "blocked_request",
                    "warning",
                    None,
                    {"reason": "unsupported_scheme", "scheme": scheme},
                )
                await route.abort("blockedbyclient")
                return
            if scheme in ("http", "https"):
                if request.method.upper() not in ALLOWED_METHODS:
                    await record(
                        "blocked_request",
                        "info",
                        page.url,
                        {
                            "reason": "non_get_blocked",
                            "method": request.method,
                            "url": sanitize_url(url),
                        },
                    )
                    await route.abort("blockedbyclient")
                    return
                if state.requests >= limits.max_requests:
                    state.stop_reason = state.stop_reason or "request_limit"
                    await route.abort("blockedbyclient")
                    return
                state.requests += 1
                if state.bytes > limits.max_bytes:
                    state.stop_reason = state.stop_reason or "byte_limit"
                    await route.abort("blockedbyclient")
                    return
                if request.is_navigation_request() and request.frame == main_frame:
                    host = urlsplit(url).hostname
                    if host != allowed_host:
                        await record(
                            "blocked_request",
                            "warning",
                            page.url,
                            {"reason": "out_of_scope_navigation", "url": sanitize_url(url)},
                        )
                        await route.abort("blockedbyclient")
                        return
                    if is_excluded(url, limits.exclusions):
                        await record(
                            "blocked_request",
                            "info",
                            page.url,
                            {"reason": "excluded_path", "url": sanitize_url(url)},
                        )
                        await route.abort("blockedbyclient")
                        return
            if limits.inject_request_id and _inject_here(request, allowed_host, main_frame):
                state.request_seq += 1
                request_id = f"{limits.request_id_prefix}-{state.request_seq}"
                state.request_ids[request] = request_id
                await route.continue_(headers={**request.headers, "x-request-id": request_id})
                return
            await route.continue_()
        except PlaywrightError:
            pass  # the page closed while we were deciding

    await context.route("**/*", handle_route)

    # ---- listeners: every one records sanitized data only -----------------------------------
    def on_console(message: Any) -> None:
        # "Failed to load resource" lines repeat what http_error / failed_request already say.
        if message.type == "error" and not message.text.startswith("Failed to load resource"):
            location = message.location or {}
            spawn(
                record(
                    "console_error",
                    "error",
                    page.url,
                    {
                        "message": sanitize_text(message.text),
                        "source": sanitize_url(location.get("url", "")) or None,
                        "line": location.get("lineNumber"),
                    },
                )
            )

    def on_page_error(error: Any) -> None:
        spawn(
            record(
                "page_error",
                "error",
                page.url,
                {"message": sanitize_text(getattr(error, "message", str(error)))},
            )
        )

    def on_request_failed(request: Request) -> None:
        failure = request.failure or ""
        if "BLOCKED_BY_CLIENT" in failure or "ERR_ABORTED" in failure:
            return  # our own blocking (already recorded) or a normal cancelled load
        spawn(
            record(
                "failed_request",
                "warning",
                page.url,
                _with_id(
                    {
                        "url": sanitize_url(request.url),
                        "method": request.method,
                        "resource_type": request.resource_type,
                        "failure": sanitize_text(failure, 200),
                    },
                    state.request_ids.get(request),
                ),
            )
        )

    def on_response(response: Response) -> None:
        request = response.request
        blocked_code = response.headers.get("x-kmr-blocked")
        if blocked_code:
            # The egress proxy refused this target: record why, never as a normal HTTP error.
            spawn(
                record(
                    "blocked_request",
                    "warning",
                    page.url,
                    {
                        "reason": f"proxy_{sanitize_text(blocked_code, 40)}",
                        "url": sanitize_url(request.url),
                    },
                )
            )
            return
        if request.is_navigation_request() and request.frame == main_frame:
            state.document_status = response.status
        if response.status >= 400 and not (
            request.is_navigation_request() and request.frame == main_frame
        ):
            spawn(
                record(
                    "http_error",
                    "error" if response.status >= 500 else "warning",
                    page.url,
                    _with_id(
                        {
                            "url": sanitize_url(request.url),
                            "status": response.status,
                            "resource_type": request.resource_type,
                            "method": request.method,
                        },
                        state.request_ids.get(request),
                    ),
                )
            )

    def on_request_finished(request: Request) -> None:
        async def account() -> None:
            try:
                sizes = await request.sizes()
                state.bytes += int(sizes.get("responseBodySize", 0)) + int(
                    sizes.get("responseHeadersSize", 0)
                )
            except PlaywrightError:
                pass

        spawn(account())

    def on_popup(popup: Page) -> None:
        spawn(popup.close())
        spawn(record("note", "info", page.url, {"event": "popup_closed"}))

    page.on("console", on_console)
    page.on("pageerror", on_page_error)
    page.on("requestfailed", on_request_failed)
    page.on("response", on_response)
    page.on("requestfinished", on_request_finished)
    page.on("popup", on_popup)
    page.on("dialog", lambda dialog: spawn(dialog.dismiss()))
    page.on("download", lambda download: spawn(download.cancel()))

    async def monitor() -> None:
        ticks = 0
        while True:
            await asyncio.sleep(1)
            ticks += 1
            if await sink.cancel_requested():
                state.cancelled = True
                await context.close()
                return
            if ticks % 10 == 0:
                await sink.heartbeat()

    monitor_task = asyncio.create_task(monitor())

    async def scan_page(url: str) -> list[str]:
        try:
            response = await page.goto(
                url,
                wait_until="load",
                timeout=int(limits.page_timeout_seconds * profile.page_timeout_factor * 1000),
            )
        except PlaywrightError as exc:
            message = exc.message or str(exc)
            interrupted = "interrupted by another navigation" in message
            if interrupted and urlsplit(page.url).hostname == allowed_host:
                # The page loaded, then its own script tried to leave the site and that navigation
                # was refused. The page itself is fine and is scanned normally below.
                response = None
            elif state.cancelled:
                return []
            else:
                outcome.pages_failed += 1
                reason = message.splitlines()[0]
                await record(
                    "page", "error", url, {"status": None, "error": sanitize_text(reason, 200)}
                )
                return []
        with_suppress = asyncio.timeout(3)
        try:
            async with with_suppress:
                await page.wait_for_load_state("networkidle")
        except (TimeoutError, PlaywrightError):
            pass
        final_url = page.url
        if urlsplit(final_url).hostname != allowed_host:
            # The page loaded, but a redirect or its own script moved the top-level document off
            # the verified host. The move was refused (and recorded when it was a navigation), and
            # the browser replaced the page with an error page, so only the load itself is known.
            if urlsplit(final_url).scheme in ("http", "https"):
                await record(
                    "blocked_request",
                    "warning",
                    url,
                    {"reason": "out_of_scope_final_url", "url": sanitize_url(final_url)},
                )
            try:
                await page.goto("about:blank")
            except PlaywrightError:
                pass
            known_status = response.status if response else state.document_status
            if known_status is None:
                outcome.pages_failed += 1
                return []
            via_redirect = response is not None and response.request.redirected_from is not None
            if via_redirect or 300 <= known_status < 400:  # redirected off-site, no page loaded
                outcome.pages_failed += 1
                await record(
                    "page",
                    "error",
                    url,
                    {
                        "status": known_status,
                        "error": "The address redirects to another site, which is not scanned.",
                    },
                )
                return []
            outcome.pages_scanned += 1
            await record(
                "page",
                "warning",
                url,
                {
                    "status": response.status if response else state.document_status,
                    "left_site": True,
                    "note": "The page tried to navigate away from the verified site.",
                },
            )
            return []
        try:
            signals: dict[str, Any] = await page.evaluate(_PAGE_SCRIPT)
        except PlaywrightError:
            outcome.pages_failed += 1
            return []
        status = response.status if response else state.document_status
        screenshot: bytes | None = None
        try:
            png = await page.screenshot(type="png", full_page=False, timeout=10_000)
            screenshot = png if len(png) <= MAX_SCREENSHOT_BYTES else None
        except PlaywrightError:
            screenshot = None
        outcome.pages_scanned += 1
        severity = (
            "error" if (status or 0) >= 500 else "warning" if (status or 0) >= 400 else "info"
        )
        await record(
            "page",
            severity,
            final_url,
            {
                "status": status,
                "title": sanitize_text(signals.get("title", ""), 200),
                "lang": sanitize_text(signals.get("lang", ""), 20),
                "description": sanitize_text(signals.get("description", ""), 300),
                "h1_count": signals.get("h1Count"),
                "load_ms": signals.get("loadMs"),
                "viewport_width": signals.get("viewportWidth"),
                "profile": profile.key,
                "links_found": len(signals.get("hrefs", [])),
                **(
                    {"request_id": state.request_ids[response.request]}
                    if response is not None and response.request in state.request_ids
                    else {}
                ),
            },
            screenshot,
        )
        for check, count, severity_name in (
            ("images_missing_alt", signals.get("imagesMissingAlt", 0), "warning"),
            ("inputs_without_label", signals.get("inputsWithoutLabel", 0), "warning"),
            ("buttons_without_name", signals.get("buttonsWithoutName", 0), "warning"),
            ("links_without_name", signals.get("linksWithoutName", 0), "warning"),
        ):
            if count:
                await record(
                    "a11y_issue", severity_name, final_url, {"check": check, "count": count}
                )
        if not signals.get("lang"):
            await record("a11y_issue", "warning", final_url, {"check": "missing_lang", "count": 1})
        if not signals.get("title"):
            await record("a11y_issue", "warning", final_url, {"check": "missing_title", "count": 1})
        if signals.get("h1Count", 0) != 1:
            await record(
                "a11y_issue",
                "info",
                final_url,
                {
                    "check": "heading_structure",
                    "count": signals.get("h1Count", 0),
                    "detail": "expected exactly one h1",
                },
            )
        if signals.get("overflowX"):
            await record(
                "a11y_issue",
                "warning",
                final_url,
                {
                    "check": "horizontal_overflow",
                    "count": 1,
                    "detail": f"wider than the {signals.get('viewportWidth')}px screen",
                },
            )
        if not signals.get("hasViewport"):
            await record(
                "a11y_issue", "info", final_url, {"check": "missing_viewport_meta", "count": 1}
            )
        return [str(href) for href in signals.get("hrefs", [])]

    try:
        queue: deque[tuple[str, int]] = deque([(start_url, 0)])
        seen = {start_url}
        truncated = False
        while queue:
            if state.cancelled or state.stop_reason:
                break
            if outcome.pages_scanned + outcome.pages_failed >= limits.max_pages:
                truncated = True
                break
            url, depth = queue.popleft()
            hrefs = await scan_page(url)
            if depth < MAX_DEPTH:
                for href in hrefs:
                    candidate = crawl_candidate(href, allowed_host, limits.exclusions)
                    if candidate and candidate not in seen:
                        seen.add(candidate)
                        queue.append((candidate, depth + 1))
        if state.cancelled:
            outcome.status = "cancelled"
            outcome.stopped_reason = "cancelled"
        elif outcome.pages_scanned == 0 and outcome.pages_failed > 0:
            outcome.status = "failed"
            outcome.error_code = "site_unreachable"
        else:
            outcome.stopped_reason = state.stop_reason or ("page_limit" if truncated else None)
    finally:
        monitor_task.cancel()
        await asyncio.gather(monitor_task, return_exceptions=True)
        await asyncio.gather(*background, return_exceptions=True)
        if state.dropped and not state.cancelled:
            await sink.observation(
                "limit",
                "info",
                None,
                {"dropped": dict(state.dropped), "per_kind_cap": MAX_PER_KIND},
            )
        try:
            await context.close()
        except PlaywrightError:
            pass
    if state.stop_reason in ("request_limit", "byte_limit"):
        await sink.observation("limit", "warning", None, {"stopped": state.stop_reason})
    outcome.requests = state.requests
    outcome.bytes = state.bytes
    outcome.duration_ms = int((time.monotonic() - started) * 1000)
    return outcome


__all__ = [
    "BrowserContext",
    "ScanLimits",
    "ScanOutcome",
    "ScanSink",
    "crawl_candidate",
    "is_excluded",
    "run_scan",
]
