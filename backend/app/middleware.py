import logging
import re
import time
import uuid

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.request_context import request_id_var

logger = logging.getLogger("app.access")

REQUEST_ID_HEADER = "X-Request-ID"
# Accept a caller-supplied ID only if it is short and made of safe characters.
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


_DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")


def _security_headers(headers: MutableHeaders, path: str, hsts: bool) -> None:
    """Defaults for every API response. The interactive docs (never in production) need scripts,
    so they get only the basic ones."""
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("Referrer-Policy", "no-referrer")
    headers.setdefault("X-Frame-Options", "DENY")
    if hsts:
        headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if path.startswith(_DOCS_PATHS):
        return
    # API answers are per-user data: never let a browser or proxy keep them.
    headers.setdefault("Cache-Control", "no-store")
    headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")


class RequestContextMiddleware:
    """Assigns a request ID, echoes it in the response, and logs one line per request.

    The request ID context variable is intentionally not reset afterwards: each request runs in
    its own task, and the outer error handler still needs the ID when an exception escapes.
    """

    def __init__(self, app: ASGIApp, *, hsts: bool = False) -> None:
        self.app = app
        self.hsts = hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = MutableHeaders(scope=scope).get(REQUEST_ID_HEADER, "")
        request_id = incoming if _SAFE_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        request_id_var.set(request_id)

        status_code = 500
        started = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = MutableHeaders(scope=message)
                headers[REQUEST_ID_HEADER] = request_id
                _security_headers(headers, scope["path"], self.hsts)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            logger.info(
                "request",
                extra={
                    "method": scope["method"],
                    "path": scope["path"],
                    "status": status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
