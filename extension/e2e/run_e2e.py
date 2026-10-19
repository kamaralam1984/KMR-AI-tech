"""Real-browser test of the extension: Playwright's Chromium with the extension loaded, against the
running local API and a local fixture site.

Run:  make ext-e2e   (needs `make up`, `make db-init` and the API on :8000)
      add `--chrome` for Google Chrome itself and `--headed` to watch it:
      cd backend && PYTHONPATH=. .venv/bin/python ../extension/e2e/run_e2e.py --chrome --headed

What is real here: the browser, the extension code, the service worker, captureVisibleTab, the HTTP
calls to the API, the database and the stored screenshot file.

What is adapted for the harness (and nothing else), in a COPY of the extension:
- host_permissions (<all_urls>) are added to the manifest. In normal use the same access comes from
  the toolbar click (activeTab) and the options-page prompt, which cannot be clicked in an automated
  browser.
- the overlay's shadow root is opened so the test can read it. Production keeps it closed.
Test data uses e2e-owner-*@example.com, which `python -m app.db.clean_test_data` removes.
"""

import json
import os
import shutil
import subprocess
import sys
import time
import tempfile
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import psycopg
from playwright.sync_api import sync_playwright

from app.config import get_settings

ROOT = Path(__file__).resolve().parents[2]
EXT = ROOT / "extension"
API = "http://127.0.0.1:8000"
PASSWORD = "correct horse battery staple"  # noqa: S105 - throwaway local test credential

PII = ["Rahul Kumar Sharma", "jane.doe@example.com", "9876543210", "4111 1111 1111 1111"]
GIF = "R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw=="
PAGE = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Support</title>
<style>body{{margin:0;font:16px system-ui;background:#fff}}main{{padding:24px;max-width:700px}}
input,textarea{{display:block;margin:8px 0;width:320px;height:32px;box-sizing:border-box}}</style></head>
<body><main><h1>Support</h1>
<div role="alert">Could not save for jane.doe@example.com, call 9876543210</div>
<form id="f" action="/submit"><label for="n">Full name</label>
<input id="n" name="name" value="Rahul Kumar Sharma"><input name="contact" value="jane.doe@example.com">
<textarea name="note">card 4111 1111 1111 1111</textarea><button type="submit"></button></form>
<img id="hero" src="data:image/gif;base64,{GIF}" width="200" height="100">
<p id="pii">Contact: 9876543210</p></main>
<script>window.__events=[];
for (const t of ["click","input","change","submit","keydown","keyup","focusin","mousedown","pointerdown"])
  document.addEventListener(t,e=>window.__events.push(t+":"+e.target.tagName),true);</script>
</body></html>"""
PASSWORD_PAGE = '<!doctype html><html lang="en"><title>Account</title><h1>Account</h1><input type="password"></html>'

results: list[tuple[str, bool, str]] = []


def step(text: str) -> None:
    print("  ..", text, flush=True)


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        body = PASSWORD_PAGE if path == "/pw" else PAGE
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args: object) -> None:
        pass


def browser_env() -> dict[str, str]:
    """A clean environment for the browser. Variables set by a snap-packaged editor (for example
    GSETTINGS_SCHEMA_DIR) make desktop Chrome abort at start-up with a GLib settings error."""
    drop = (
        "GSETTINGS_SCHEMA_DIR",
        "GIO_MODULE_DIR",
        "GTK_PATH",
        "GTK_EXE_PREFIX",
        "GTK_IM_MODULE_FILE",
    )
    env = {k: v for k, v in os.environ.items() if k not in drop and not k.startswith("SNAP")}
    env["GSETTINGS_BACKEND"] = "memory"
    return env


def find_chrome() -> str | None:
    """Google Chrome itself. Chrome 137+ ignores --load-extension unless this feature flag is set."""
    return shutil.which("google-chrome") or shutil.which("google-chrome-stable")


def launch_chrome(p, chrome: str, ext_dir: Path, work: Path, headed: bool):  # type: ignore[no-untyped-def]
    """Google Chrome 137+ ignores --load-extension. The supported route is the DevTools command
    Extensions.loadUnpacked (what "Load unpacked" does), which needs --enable-unsafe-extension-debugging.
    Returns (context, stop)."""
    port = 9333
    args = [
        chrome,
        f"--user-data-dir={work / 'profile'}",
        f"--remote-debugging-port={port}",
        "--enable-unsafe-extension-debugging",
        "--no-first-run",
        "--no-default-browser-check",
        "--password-store=basic",
        "--window-size=1280,900",
        *([] if headed else ["--headless=new"]),
        "about:blank",
    ]
    proc = subprocess.Popen(  # noqa: S603 - fixed arguments
        args, env=browser_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    for _ in range(80):
        try:
            httpx.get(f"http://127.0.0.1:{port}/json/version", timeout=1).raise_for_status()
            break
        except Exception:  # noqa: BLE001
            if proc.poll() is not None:
                raise SystemExit(
                    "Chrome exited at start-up (see the note about headed Chrome)."
                ) from None
            time.sleep(0.25)
    browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
    session = browser.new_browser_cdp_session()
    loaded = session.send("Extensions.loadUnpacked", {"path": str(ext_dir)})
    step(f"Chrome loaded the extension: {loaded.get('id')}")
    context = browser.contexts[0]

    def stop() -> None:
        try:
            browser.close()
        finally:
            proc.terminate()

    return context, stop, str(loaded.get("id"))


def find_chromium() -> str | None:
    """Playwright's own Chromium. Branded Chrome (137+) ignores --load-extension."""
    found = sorted(
        (Path.home() / ".cache" / "ms-playwright").glob("chromium-*/chrome-linux*/chrome")
    )
    return str(found[-1]) if found else None


def with_timeout(js_promise_fn: str) -> str:
    """Wraps an async arrow function so a call that never answers fails after 20 s, not forever."""
    return (
        "async (arg) => { const run = (" + js_promise_fn + "); "
        "return Promise.race([run(arg), new Promise((_, no) => setTimeout(() => no(new Error('timed out after 20s')), 20000))]); }"
    )


def api_session() -> tuple[httpx.Client, str, str, str]:
    client = httpx.Client(base_url=API, timeout=20)
    email = f"e2e-owner-{uuid.uuid4().hex[:10]}@example.com"
    r = client.post(
        "/v1/auth/register", json={"email": email, "password": PASSWORD, "display_name": "E2E"}
    )
    r.raise_for_status()
    csrf = client.get("/v1/auth/csrf").json()["csrf_token"]
    client.headers["X-CSRF-Token"] = csrf
    org = client.post("/v1/organizations", json={"name": "E2E ext"}).json()["id"]
    project = client.post(f"/v1/organizations/{org}/projects", json={"name": "Fixture"}).json()[
        "id"
    ]
    token = client.post(
        f"/v1/organizations/{org}/extension-tokens", json={"name": "e2e", "days": 1}
    ).json()["token"]
    return client, org, project, token


def main() -> int:
    settings = get_settings()
    if settings.app_env != "local" or settings.migration_database_url is None:
        print("Refusing to run: needs KMR_APP_ENV=local and KMR_MIGRATION_DATABASE_URL.")
        return 2
    dsn = settings.migration_database_url.get_secret_value().replace(
        "postgresql+psycopg://", "postgresql://", 1
    )
    try:
        httpx.get(f"{API}/ready", timeout=5).raise_for_status()
    except Exception as exc:  # noqa: BLE001
        print(f"Unavailable: the API is not reachable on {API} ({exc}).")
        return 2

    use_chrome = "--chrome" in sys.argv
    headed = "--headed" in sys.argv
    chromium = find_chrome() if use_chrome else find_chromium()
    if chromium is None:
        print(
            "Unavailable: Google Chrome is not installed."
            if use_chrome
            else "Unavailable: no Playwright Chromium installed (run `playwright install chromium`)."
        )
        return 2
    print(f"Browser: {chromium}" + (" (headed)" if headed else " (headless)"), flush=True)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    site = f"http://127.0.0.1:{server.server_address[1]}"

    client, org, project, token = api_session()
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "UPDATE projects SET site_url = %s, site_host = '127.0.0.1', verified_at = now(), "
            "verified_host = '127.0.0.1', verified_method = 'file' WHERE id = %s",
            (site + "/", project),
        )

    work = Path(tempfile.mkdtemp(prefix="kmr-ext-e2e-"))
    ext_dir = work / "extension"
    shutil.copytree(EXT, ext_dir, ignore=shutil.ignore_patterns("test", "e2e", "node_modules"))
    manifest = json.loads((ext_dir / "manifest.json").read_text())
    # activeTab (granted by a toolbar click in real use) is what lets captureVisibleTab run.
    manifest["host_permissions"] = ["<all_urls>"]
    (ext_dir / "manifest.json").write_text(json.dumps(manifest))
    overlay = ext_dir / "src" / "overlay.js"
    text = overlay.read_text()
    assert 'mode = "closed"' in text
    overlay.write_text(text.replace('mode = "closed"', 'mode = "open"'))

    with sync_playwright() as p:
        if use_chrome:
            context, stop_browser, loaded_id = launch_chrome(p, chromium, ext_dir, work, headed)
        else:
            context = p.chromium.launch_persistent_context(
                str(work / "profile"),
                executable_path=chromium,
                headless=False,
                ignore_default_args=["--disable-extensions"],
                slow_mo=350 if headed else 0,
                env=browser_env(),
                viewport={"width": 1280, "height": 720},
                args=[
                    *([] if headed else ["--headless=new"]),
                    f"--disable-extensions-except={ext_dir}",
                    f"--load-extension={ext_dir}",
                ],
            )
            stop_browser = context.close
            loaded_id = ""
        try:

            def ours(w) -> bool:  # type: ignore[no-untyped-def]
                return w.url.startswith("chrome-extension://") and (
                    not loaded_id or loaded_id in w.url
                )

            worker = next((w for w in context.service_workers if ours(w)), None)
            if worker is None:
                deadline = time.time() + 20
                while worker is None and time.time() < deadline:
                    context.pages[0].wait_for_timeout(300) if context.pages else time.sleep(0.3)
                    worker = next((w for w in context.service_workers if ours(w)), None)
            if worker is None:
                raise SystemExit("The extension's service worker never started.")
            ext_id = worker.url.split("/")[2]
            worker.evaluate(
                with_timeout("(cfg) => chrome.storage.local.set(cfg)"),
                {
                    "apiBase": API,
                    "webBase": "http://localhost:3000",
                    "token": token,
                    "projectId": project,
                    "userBlocklist": [],
                    "confirmBeforeSend": False,
                    "includeScreenshot": True,
                },
            )
            step("storage configured")
            control = context.new_page()
            control.goto(f"chrome-extension://{ext_id}/options.html")

            def activate() -> dict:  # type: ignore[type-arg]
                return control.evaluate(  # type: ignore[no-any-return]
                    with_timeout("() => chrome.runtime.sendMessage({type: 'kmr:activate'})")
                )

            def tab_message(url_prefix: str, message: dict) -> dict:  # type: ignore[type-arg]
                return control.evaluate(  # type: ignore[no-any-return]
                    with_timeout("""async ([prefix, message]) => {
                        const tabs = await chrome.tabs.query({});
                        const tab = tabs.find((t) => t.url && t.url.startsWith(prefix));
                        return chrome.tabs.sendMessage(tab.id, message);
                    }"""),
                    [url_prefix, message],
                )

            # ---------------------------------------------------------------- the good page
            step("opening the fixture page")
            page = context.new_page()
            page.set_viewport_size({"width": 1280, "height": 720})
            page.goto(site + "/")
            page.bring_to_front()
            step("activating")
            on = activate()
            check(
                "agent turns on for an ordinary page",
                on.get("ok") and not on.get("blocked"),
                str(on),
            )
            before = page.evaluate(
                "[...document.querySelectorAll('input,textarea')].map(e=>e.value)"
            )
            step("analyzing")
            done = tab_message(site, {"type": "kmr:analyze", "projectId": project})
            check("analysis completes", done.get("blocked") is False, str(done))

            host = page.locator("kmr-ai-agent")
            check("overlay is on the page", host.count() == 1)
            panel_text = page.locator("kmr-ai-agent .panel").inner_text()
            check(
                "panel lists findings and says nothing was changed",
                "Nothing was changed" in panel_text,
                panel_text[:120],
            )
            check("findings say built-in rules, not AI", "Built-in rules (not AI)" in panel_text)

            hero = page.locator("#hero").bounding_box()
            boxes = page.locator("kmr-ai-agent .box")
            matched = False
            for i in range(boxes.count()):
                b = boxes.nth(i).bounding_box()
                if (
                    b
                    and abs(b["x"] - hero["x"]) < 2
                    and abs(b["y"] - hero["y"]) < 2
                    and abs(b["width"] - hero["width"]) < 2
                    and abs(b["height"] - hero["height"]) < 2
                ):
                    matched = True
            check(
                "a highlight sits exactly on the image that has no alt text",
                matched,
                f"hero={hero}",
            )

            show = page.locator("kmr-ai-agent button", has_text="Show on page")
            check("each finding has a Show button", show.count() >= 1)
            # Find the finding whose selector is the image, then show it.
            cards = page.locator("kmr-ai-agent .finding")
            target = None
            for i in range(cards.count()):
                if "alt" in cards.nth(i).inner_text().lower():
                    target = cards.nth(i)
            check("the missing-alt finding is shown", target is not None)
            if target is not None:
                target.locator("button", has_text="Show on page").click()
                page.wait_for_timeout(1000)  # the cursor glides for 0.6 s
                cursor = page.locator("kmr-ai-agent .cursor").bounding_box()
                cx, cy = hero["x"] + hero["width"] / 2, hero["y"] + hero["height"] / 2
                check(
                    "the agent cursor moved to the image",
                    cursor is not None and abs(cursor["x"] - cx) < 3 and abs(cursor["y"] - cy) < 3,
                    f"cursor={cursor} want=({cx},{cy})",
                )
            page_events = [e for e in page.evaluate("window.__events") if "KMR-AI-AGENT" not in e]
            check(
                "the page saw no click, input, key or submit from the agent",
                page_events == [],
                str(page_events),
            )
            after = page.evaluate(
                "[...document.querySelectorAll('input,textarea')].map(e=>e.value)"
            )
            check("no field value changed", before == after)

            # ------------------------------------------------ "Preview fix": allowed per element
            with psycopg.connect(dsn) as conn:
                scans_before = conn.execute(
                    "SELECT count(*) FROM scans WHERE project_id = %s", (project,)
                ).fetchone()[0]
            hero_el = page.locator("#hero")
            check(
                "before any permission the image has no alt attribute",
                hero_el.get_attribute("alt") is None,
            )
            card = page.locator("kmr-ai-agent .finding", has_text="Images without alternative text")
            card.locator("button", has_text="Preview fix").click()
            ask = page.locator("kmr-ai-agent .ask")
            check(
                "it asks first and says it changes only this tab",
                "in this tab only" in ask.inner_text(),
                ask.inner_text()[:120],
            )
            check("asking changes nothing on the page", hero_el.get_attribute("alt") is None)
            page.locator("kmr-ai-agent .ask input").fill("Support team photo")
            # A click fabricated by the page's own script must not count as the user's permission.
            page.evaluate(
                """() => { const root = document.querySelector('kmr-ai-agent').shadowRoot;
                    [...root.querySelectorAll('button')].find((b) => b.textContent === 'Allow').click(); }"""
            )
            page.wait_for_timeout(1500)
            check(
                "a script-made click does NOT grant permission",
                hero_el.get_attribute("alt") is None,
            )
            page.locator("kmr-ai-agent .ask button", has_text="Allow").click()  # a real click
            page.wait_for_function(
                "document.querySelector('#hero').getAttribute('alt') !== null", timeout=8000
            )
            check(
                "after a real click the typed text is applied",
                hero_el.get_attribute("alt") == "Support team photo",
            )
            cur = page.locator("kmr-ai-agent .cursor").bounding_box()
            hb = page.locator("#hero").bounding_box()
            check(
                "the pointer was on the image when it applied",
                abs(cur["x"] - (hb["x"] + hb["width"] / 2)) < 4
                and abs(cur["y"] - (hb["y"] + hb["height"] / 2)) < 4,
                f"cursor={cur} hero={hb}",
            )
            applied = page.locator("kmr-ai-agent .panel").inner_text()
            check(
                "it says Done (preview) and that the real website is unchanged",
                "Done (preview)" in applied and "real website is unchanged" in applied,
            )
            check("the highlight turned green", page.locator("kmr-ai-agent .box.done").count() >= 1)
            check(
                "no field value changed during the preview",
                before
                == page.evaluate(
                    "[...document.querySelectorAll('input,textarea')].map(e=>e.value)"
                ),
            )
            page_events = [e for e in page.evaluate("window.__events") if "KMR-AI-AGENT" not in e]
            check(
                "the page still saw no click, input or submit from the agent",
                page_events == [],
                str(page_events),
            )
            page.locator("kmr-ai-agent .panel button", has_text="Undo").click()
            check("Undo puts the page back exactly", hero_el.get_attribute("alt") is None)
            with psycopg.connect(dsn) as conn:
                scans_after = conn.execute(
                    "SELECT count(*) FROM scans WHERE project_id = %s", (project,)
                ).fetchone()[0]
            check(
                "a preview sends nothing to the server",
                scans_before == scans_after,
                f"{scans_before}->{scans_after}",
            )

            # Two form fields: one permission each, then turning the agent off restores everything.
            card2 = page.locator("kmr-ai-agent .finding", has_text="Form fields without a label")
            card2.locator("button", has_text="Preview fix").click()
            page.locator("kmr-ai-agent .ask input").fill("Contact email")
            page.locator("kmr-ai-agent .ask button", has_text="Allow").click()
            page.wait_for_selector(
                "kmr-ai-agent .ask button:has-text('Next element')", timeout=8000
            )
            labelled = page.evaluate(
                "[...document.querySelectorAll('input[aria-label],textarea[aria-label]')].length"
            )
            check("only the first field was changed so far", labelled == 1, str(labelled))
            page.locator("kmr-ai-agent .ask button", has_text="Next element").click()
            check(
                "the next element asks again",
                "(2 of" in page.locator("kmr-ai-agent .ask").inner_text(),
            )
            page.locator("kmr-ai-agent .ask button", has_text="Cancel").click()
            control.evaluate(
                with_timeout("""async (prefix) => {
                    const tabs = await chrome.tabs.query({});
                    const tab = tabs.find((t) => t.url && t.url.startsWith(prefix));
                    return chrome.tabs.sendMessage(tab.id, {type: 'kmr:off'}); }"""),
                site,
            )
            restored = page.evaluate("[...document.querySelectorAll('[aria-label]')].length")
            check("turning the agent off restored the page", restored == 0, str(restored))
            check("and removed the overlay", page.locator("kmr-ai-agent").count() == 0)
            # Back on, so the rest of the run (server data, screenshot) is unaffected.
            on_again = activate()
            check("it can be turned on again", on_again.get("ok") is True)
            tab_message(site, {"type": "kmr:analyze", "projectId": project})

            # ------------------------------------------------------------- what the server got
            with psycopg.connect(dsn) as conn:
                scan = conn.execute(
                    "SELECT id FROM scans WHERE project_id = %s AND limits->>'source' = 'extension' ORDER BY created_at DESC LIMIT 1",
                    (project,),
                ).fetchone()
                check("the server stored one extension scan", scan is not None)
                scan_id = scan[0]
                rows = conn.execute(
                    "SELECT kind, data::text FROM observations WHERE scan_id = %s", (scan_id,)
                ).fetchall()
                blob = " ".join(r[1] for r in rows)
                leaked = [s for s in PII if s in blob]
                check(
                    "none of the fake personal data reached the database", not leaked, str(leaked)
                )
                check(
                    "redaction placeholders are what was stored",
                    "[EMAIL]" in blob and "[PHONE]" in blob,
                )
                evidence = conn.execute(
                    "SELECT storage_key, size_bytes FROM evidence WHERE scan_id = %s", (scan_id,)
                ).fetchone()
            check("a screenshot was stored", evidence is not None)
            if evidence is None:
                probe = worker.evaluate(
                    with_timeout("""async (prefix) => {
                        try {
                            const tabs = await chrome.tabs.query({});
                            const tab = tabs.find((t) => t.url && t.url.startsWith(prefix));
                            const shot = await chrome.tabs.captureVisibleTab(tab.windowId, {format: 'png'});
                            const bmp = await createImageBitmap(await (await fetch(shot)).blob());
                            return 'capture ok ' + bmp.width + 'x' + bmp.height;
                        } catch (e) { return 'capture failed: ' + e.message; }
                    }"""),
                    site,
                )
                step(f"probe: {probe}")

            # ------------------------------------------------------- screenshot masking, pixels
            if evidence is not None:
                png = (ROOT / ".data" / "evidence" / evidence[0]).read_bytes()
                shot = context.new_page()
                shot.set_content("<canvas id=c></canvas>")
                data = list(png)
                res = shot.evaluate(
                    """async ({bytes, points}) => {
                        const blob = new Blob([new Uint8Array(bytes)], {type: 'image/png'});
                        const bmp = await createImageBitmap(blob);
                        const c = document.getElementById('c'); c.width = bmp.width; c.height = bmp.height;
                        const ctx = c.getContext('2d'); ctx.drawImage(bmp, 0, 0);
                        const px = ([x, y]) => Array.from(ctx.getImageData(Math.round(x), Math.round(y), 1, 1).data.slice(0, 3));
                        return {w: bmp.width, h: bmp.height, pts: points.map(px)};
                    }""",
                    {"bytes": data, "points": []},
                )
                scale = res["w"] / 1280

                def center(sel: str) -> list[float]:
                    b = page.locator(sel).bounding_box()
                    return [(b["x"] + b["width"] / 2) * scale, (b["y"] + b["height"] / 2) * scale]

                points = [
                    center("#n"),
                    center("input[name=contact]"),
                    center("textarea"),
                    center("#pii"),
                    [1270 * scale, 700 * scale],
                    center("h1"),
                ]
                res = shot.evaluate(
                    """async ({bytes, points}) => {
                        const bmp = await createImageBitmap(new Blob([new Uint8Array(bytes)], {type: 'image/png'}));
                        const c = document.getElementById('c'); c.width = bmp.width; c.height = bmp.height;
                        const ctx = c.getContext('2d'); ctx.drawImage(bmp, 0, 0);
                        return points.map(([x, y]) => Array.from(ctx.getImageData(Math.round(x), Math.round(y), 1, 1).data.slice(0, 3)));
                    }""",
                    {"bytes": data, "points": points},
                )
                black = lambda px: sum(px) < 30  # noqa: E731
                check("screenshot: the name field is blacked out", black(res[0]), str(res[0]))
                check("screenshot: the email field is blacked out", black(res[1]), str(res[1]))
                check(
                    "screenshot: the text area with a card number is blacked out",
                    black(res[2]),
                    str(res[2]),
                )
                check(
                    "screenshot: the paragraph with a phone number is blacked out",
                    black(res[3]),
                    str(res[3]),
                )
                check(
                    "screenshot: an empty part of the page is NOT blacked out",
                    not black(res[4]),
                    str(res[4]),
                )
                check(
                    "screenshot: the heading area is not blacked out",
                    not black(res[5]),
                    str(res[5]),
                )

            # ------------------------------------------------------------ pages it must refuse
            with psycopg.connect(dsn) as conn:
                count_before = conn.execute(
                    "SELECT count(*) FROM scans WHERE project_id = %s", (project,)
                ).fetchone()[0]
            login = context.new_page()
            login.goto(site + "/login")
            login.bring_to_front()
            refused = activate()
            check(
                "a /login address is refused before anything is injected",
                refused.get("blocked") is True,
                str(refused),
            )
            login_panel = login.locator("kmr-ai-agent .panel").inner_text()
            check(
                "the /login page shows 'Not reading this page'",
                "Not reading this page" in login_panel,
                login_panel[:100],
            )
            check(
                "the page-world hook was not installed on the /login page",
                login.evaluate("typeof window.__kmrAgentHook") == "undefined",
            )
            pw = context.new_page()
            pw.goto(site + "/pw")
            pw.bring_to_front()
            refused2 = activate()
            check(
                "a page with a password field is refused",
                refused2.get("blocked") is True,
                str(refused2),
            )
            result = tab_message(site + "/pw", {"type": "kmr:analyze", "projectId": project})
            check("analysis on it sends nothing", result.get("blocked") is True)
            panel = pw.locator("kmr-ai-agent .panel").inner_text()
            check(
                "it tells the user it is not reading the page",
                "Not reading this page" in panel,
                panel[:100],
            )
            with psycopg.connect(dsn) as conn:
                count_after = conn.execute(
                    "SELECT count(*) FROM scans WHERE project_id = %s", (project,)
                ).fetchone()[0]
            check(
                "no scan was created from refused pages",
                count_after == count_before,
                f"{count_before}->{count_after}",
            )
        finally:
            stop_browser()
            server.shutdown()
            shutil.rmtree(work, ignore_errors=True)

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
