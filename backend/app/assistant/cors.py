"""CORS for the visitor API.

Visitors reach the API from other people's websites, so the allowed origins are per install and
cannot be known at preflight time (a preflight carries no install key). The preflight therefore
answers for any origin, which exposes nothing: the real request is refused unless its Origin is on
that install's list, and only then does the response carry `Access-Control-Allow-Origin`. No
credentials or cookies are ever involved.
"""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

PUBLIC_PREFIX = "/public/"
ALLOW_HEADERS = "Content-Type, X-KMR-Install-Key, X-KMR-Consent, X-Request-ID"


class PublicCorsMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(PUBLIC_PREFIX):
            await self.app(scope, receive, send)
            return

        origin = MutableHeaders(scope=scope).get("origin")
        if scope["method"] == "OPTIONS" and origin:
            headers = [
                (b"access-control-allow-origin", origin.encode("latin-1")),
                (b"access-control-allow-methods", b"GET, POST, OPTIONS"),
                (b"access-control-allow-headers", ALLOW_HEADERS.encode()),
                (b"access-control-max-age", b"600"),
                (b"vary", b"Origin"),
                (b"content-length", b"0"),
            ]
            await send({"type": "http.response.start", "status": 204, "headers": headers})
            await send({"type": "http.response.body", "body": b""})
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                allowed = scope.get("state", {}).get("cors_origin")
                headers = MutableHeaders(scope=message)
                headers["Vary"] = "Origin"
                if allowed:
                    headers["Access-Control-Allow-Origin"] = allowed
                    headers["Access-Control-Expose-Headers"] = "X-Request-ID"
            await send(message)

        await self.app(scope, receive, send_wrapper)
