import re
import uuid

from fastapi import FastAPI

from tests.helpers import Api

PUBLIC = {("POST", "/v1/auth/register"), ("POST", "/v1/auth/login")}
METHODS = {"get", "post", "put", "patch", "delete"}


def _protected_routes(app: FastAPI) -> list[tuple[str, str]]:
    # The OpenAPI document lists every route that is actually mounted, including included routers.
    found = []
    for path, operations in app.openapi()["paths"].items():
        if not path.startswith("/v1"):
            continue
        for method in operations.keys() & METHODS:
            if (method.upper(), path) not in PUBLIC:
                found.append((method.upper(), path))
    return found


def test_every_v1_route_rejects_anonymous_requests(api_app: FastAPI, new_api) -> None:  # type: ignore[no-untyped-def]
    """Walks the real route table, so a newly added route cannot silently skip authentication."""
    routes = _protected_routes(api_app)
    assert len(routes) >= 15, "route discovery found suspiciously few routes"

    anonymous: Api = new_api()
    failures = []
    for method, template in routes:
        path = re.sub(r"\{[a-z_]+\}", lambda _: str(uuid.uuid4()), template)
        response = anonymous.request(method, path, json={}, csrf=False)
        if response.status_code != 401:
            failures.append(f"{method} {template} -> {response.status_code}")
    assert not failures, failures


def test_garbage_session_cookie_is_rejected(new_api) -> None:  # type: ignore[no-untyped-def]
    api: Api = new_api()
    api.jar["kmr_session"] = "not-a-real-token"
    assert api.get("/v1/auth/me").status_code == 401
