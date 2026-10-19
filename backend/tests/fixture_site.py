"""A small website with deliberate problems, served on 127.0.0.1 for scanner tests."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

PIXEL = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,"
    b"\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
)


class FixtureSite:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []
        self.request_headers: list[dict[str, str]] = []
        self.last_headers: dict[str, str] = {}
        self.server: ThreadingHTTPServer | None = None
        self.port = 0
        self.thread: threading.Thread | None = None

    # ---- lifecycle ----------------------------------------------------------------------
    def start(self) -> None:
        site = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _serve(self, method: str) -> None:
                path = urlsplit(self.path).path
                site.requests.append((method, self.path))
                site.last_headers = {k.lower(): v for k, v in self.headers.items()}
                site.request_headers.append(site.last_headers)
                status, headers, body = site.route(method, self.path)
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                del path

            def do_GET(self) -> None:
                self._serve("GET")

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                self._serve("POST")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = int(self.server.server_address[1])
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def seen(self, method: str | None = None) -> list[str]:
        return [path for m, path in self.requests if method in (None, m)]

    # ---- content ------------------------------------------------------------------------
    def route(self, method: str, raw: str) -> tuple[int, dict[str, str], bytes]:
        path = urlsplit(raw).path
        html = {"Content-Type": "text/html; charset=utf-8"}

        def page(
            body: str, head: str = "", lang: str = ' lang="en"'
        ) -> tuple[int, dict[str, str], bytes]:
            doc = (
                f"<!doctype html><html{lang}><head><title>{path}</title>"
                '<meta name="viewport" content="width=device-width">'
                f"{head}</head><body><h1>{path}</h1>{body}</body></html>"
            )
            return 200, html, doc.encode()

        out = f"http://127.0.0.2:{self.port}"
        if path == "/":
            return page(
                '<a href="/about">About</a> <a href="/broken">Broken</a> '
                '<a href="/logout">Log out</a> <a href="/delete-account">Delete</a> '
                '<a href="/admin/secret">Admin</a> <a href="/popup">Popup</a> '
                '<a href="/secrets?token=abcDEF123456789012345678901234&page=2">Secrets</a> '
                f'<a href="{out}/elsewhere">Elsewhere</a>'
            )
        if path == "/about":
            return page(
                '<img src="/logo.gif"><img src="/missing.gif"><input name="q"><button></button>'
                "<script>"
                'console.error("boom token=abc123456789 mail jane.doe@example.com");'
                'setTimeout(() => { throw new Error("uncaught on about"); }, 20);'
                'fetch("http://169.254.169.254/latest/meta-data/").catch(() => {});'
                'fetch("/api/save", {method: "POST", body: "x"}).catch(() => {});'
                "</script>"
            )
        if path == "/wide":
            return page('<div style="width: 900px; background: #ccc">fixed-width block</div>')
        api = {"Content-Type": "application/json"}
        if path == "/api/ok":
            return 200, api, b'{"id": 1, "name": "widget", "price": 9.5}'
        if path == "/api/bad-shape":
            return 200, api, b'{"id": "SECRET-VALUE-123", "name": 7}'
        if path == "/api/not-json":
            return 200, {"Content-Type": "text/html"}, b"<html>nope</html>"
        if path == "/api/fail":
            return 500, api, b'{"error": "boom"}'
        if path == "/api/slow":
            threading.Event().wait(1.2)
            return 200, api, b"{}"
        if path == "/api/redirect":
            return 302, {"Location": "http://127.0.0.2:1/elsewhere"}, b""
        if path == "/api/big":
            return 200, api, b'{"pad": "' + b"x" * 300_000 + b'"}'
        if path == "/api/auth":
            if self.last_headers.get("authorization") == "Bearer good-token":
                return 200, api, b'{"ok": true}'
            return 401, api, b'{"error": "unauthorized"}'
        if path in ("/logo.gif", "/pixel"):
            return 200, {"Content-Type": "image/gif"}, PIXEL
        if path == "/broken":
            return 404, html, b"<!doctype html><html lang=en><title>nf</title><h1>Not found</h1>"
        if path in ("/logout", "/delete-account", "/admin/secret"):
            return page("<p>should not be crawled</p>")
        if path == "/slow":
            threading.Event().wait(6)
            return page("<p>slow</p>")
        if path == "/redirect-out":
            return 302, {"Location": f"{out}/landed"}, b""
        if path == "/js-out":
            return page(
                f"<script>setTimeout(() => location.href = '{out}/js-landed', 100)</script>"
            )
        if path == "/auto-post":
            return page(
                '<form id="f" method="post" action="/submit">'
                '<input name="a" value="1" aria-label="a"></form>'
                '<script>document.getElementById("f").submit()</script>'
            )
        if path == "/submit":
            return page("<p>submitted</p>")
        if path == "/popup":
            return page('<script>window.open("/about")</script>')
        if path == "/download":
            return (
                200,
                {
                    "Content-Type": "application/octet-stream",
                    "Content-Disposition": "attachment; filename=x.bin",
                },
                b"binary",
            )
        if path == "/secrets":
            return page(
                "<script>console.error('login failed for jane.doe@example.com with "
                "Authorization: Bearer abcdef1234567890abcdef')</script>"
            )
        if path == "/big":
            return page("<!--" + "x" * 3_000_000 + "-->")
        if path == "/many":
            return page("".join(f'<img src="/pixel?i={i}" alt="">' for i in range(60)))
        if path == "/cross":
            other = raw.split("b=")[-1]
            return page(f'<img src="http://127.0.0.1:{other}/pixel" alt="">')
        if path == "/start-many":
            return page(
                '<a href="/p1">1</a><a href="/p2">2</a><a href="/p3">3</a><a href="/p4">4</a>'
            )
        if path in ("/p1", "/p2", "/p3", "/p4"):
            return page("<p>page</p>")
        return 404, html, b"<!doctype html><html lang=en><title>nf</title><h1>nf</h1>"
