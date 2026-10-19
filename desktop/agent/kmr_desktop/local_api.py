"""The agent's local HTTP API, used only by the desktop app's own window.

It listens on 127.0.0.1 only, and every request must carry a per-run secret that only the app that
started the agent knows. A web page in a browser cannot call it: it has no secret, and requests
whose Host or Origin are not local are refused. The secret is what stops another program on the
computer from approving a step on the person's behalf.
"""

import hmac
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from kmr_desktop import config as config_module
from kmr_desktop.client import DeskClient, DeskError
from kmr_desktop.session import Agent, AgentError

logger = logging.getLogger(__name__)
MAX_BODY = 200_000


class LocalApi:
    def __init__(self, agent: Agent, secret: str, cfg: config_module.Config, config_file=None) -> None:  # type: ignore[no-untyped-def]
        if len(secret) < 24:
            raise ValueError("the local secret must be at least 24 characters")
        self.agent = agent
        self.secret = secret
        self.cfg = cfg
        self.config_file = config_file
        self.server: ThreadingHTTPServer | None = None

    # -------------------------------------------------------------------------- routes

    def status(self) -> dict[str, Any]:
        out: dict[str, Any] = {"platform": self.agent.platform.name, "connected": False, **self.cfg.public()}
        if self.agent.client is not None:
            try:
                me = self.agent.client.me()
                out.update(
                    connected=True,
                    org=me["org_name"],
                    user=me["user_name"],
                    provider_label=me["provider_label"],
                    is_ai=me["is_ai"],
                    used=me["daily_chat_used"],
                    limit=me["daily_chat_limit"],
                )
            except DeskError as exc:
                out["error"] = exc.message
        return out

    def handle(self, method: str, path: str, body: dict[str, Any]) -> tuple[int, Any]:
        a = self.agent
        try:
            if method == "GET" and path == "/status":
                return 200, self.status()
            if method == "GET" and path == "/windows":
                return 200, a.windows()
            if method == "POST" and path == "/prepare":
                p = a.prepare(
                    str(body.get("message", "")),
                    str(body.get("language", "auto")),
                    [str(w) for w in body.get("window_ids", [])][:5],
                )
                return 200, {
                    "prepared_id": p.prepared_id,
                    "preview": p.preview,
                    "sha256": p.sha256,
                    "windows": p.windows,
                    "nodes": p.nodes,
                    "redacted_counts": p.redacted_counts,
                    "blocked": p.blocked,
                }
            if method == "POST" and path == "/send":
                return 200, a.send(str(body.get("prepared_id", "")), str(body.get("sha256", "")))
            if method == "POST" and path.startswith("/steps/"):
                _, _, step_id, action = path.split("/")
                if action == "approve":
                    return 200, a.approve(step_id, body.get("text"))
                if action == "reject":
                    return 200, a.reject(step_id)
                if action == "highlight":
                    return 200, a.highlight(step_id)
            if method == "POST" and path == "/config":
                return 200, self.update_config(body)
        except AgentError as exc:
            return exc.status, {"error": exc.message}
        return 404, {"error": "Not found."}

    def update_config(self, body: dict[str, Any]) -> dict[str, Any]:
        cfg = self.cfg
        if "server_url" in body:
            url = str(body["server_url"]).strip().rstrip("/")
            if url and not url.startswith(("http://localhost", "http://127.0.0.1", "https://")):
                raise AgentError(
                    "The server address must start with https:// (or http://localhost for testing)."
                )
            cfg.server_url = url
        if body.get("disconnect"):
            cfg.token = ""
        elif body.get("token"):
            cfg.token = str(body["token"]).strip()
        if body.get("language") in ("auto", "en", "hinglish", "hi"):
            cfg.language = body["language"]
        if isinstance(body.get("extra_apps"), list):
            cfg.extra_apps = [str(x)[:60] for x in body["extra_apps"] if str(x).strip()][:50]
        config_module.save(cfg, self.config_file)
        self.agent.extra_apps = tuple(cfg.extra_apps)
        if self.agent.client is not None:
            self.agent.client.close()
        self.agent.client = DeskClient(cfg.server_url, cfg.token) if cfg.connected else None
        return {**cfg.public(), "connected": cfg.connected}

    # ----------------------------------------------------------------------- the server

    def serve(self, port: int = 0) -> ThreadingHTTPServer:
        api = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a: object) -> None:  # never log requests: they carry the person's words
                pass

            def _reply(self, status: int, body: Any) -> None:
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

            def _allowed(self) -> bool:
                port_ = self.server.server_port  # type: ignore[attr-defined]
                host = self.headers.get("Host", "")
                if host not in (f"127.0.0.1:{port_}", f"localhost:{port_}"):
                    return False  # DNS rebinding: a page on another name cannot reach this
                origin = self.headers.get("Origin")
                if origin not in (None, "null", "file://"):
                    return False  # a web page in a browser
                supplied = self.headers.get("X-KMR-Local", "")
                return hmac.compare_digest(supplied.encode(), api.secret.encode())

            def _run(self, method: str) -> None:
                if not self._allowed():
                    return self._reply(403, {"error": "Not allowed."})
                body: dict[str, Any] = {}
                if method == "POST":
                    length = int(self.headers.get("Content-Length") or 0)
                    if length > MAX_BODY:
                        return self._reply(413, {"error": "Too large."})
                    try:
                        body = json.loads(self.rfile.read(length) or b"{}")
                    except ValueError:
                        return self._reply(400, {"error": "Invalid JSON."})
                    if not isinstance(body, dict):
                        return self._reply(400, {"error": "Invalid body."})
                status, payload = api.handle(method, self.path.split("?")[0], body)
                self._reply(status, payload)

            def do_GET(self) -> None:  # noqa: N802
                self._run("GET")

            def do_POST(self) -> None:  # noqa: N802
                self._run("POST")

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        return self.server
