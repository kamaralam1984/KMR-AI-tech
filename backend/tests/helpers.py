import uuid
from typing import Any

from fastapi.testclient import TestClient
from httpx import Response

PASSWORD = "correct horse battery staple"  # noqa: S105 - throwaway test credential
COOKIE = "kmr_session"


def random_email() -> str:
    return f"user-{uuid.uuid4().hex[:10]}@example.com"


class Api:
    """One logical user (cookie jar) talking to the shared test client."""

    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.jar: dict[str, str] = {}
        self.email: str | None = None
        self.user_id: str | None = None
        self._csrf: str | None = None

    def request(
        self,
        method: str,
        path: str,
        json: Any = None,
        *,
        csrf: bool = True,
        headers: dict[str, str] | None = None,
    ) -> Response:
        self.client.cookies.clear()
        for name, value in self.jar.items():
            self.client.cookies.set(name, value)
        sent = dict(headers or {})
        if csrf and method not in ("GET", "HEAD") and COOKIE in self.jar:
            sent.setdefault("X-CSRF-Token", self.csrf_token())
        response: Response = self.client.request(method, path, json=json, headers=sent)
        self.jar = {c.name: c.value for c in self.client.cookies.jar}
        return response

    def get(self, path: str, **kw: Any) -> Response:
        return self.request("GET", path, **kw)

    def post(self, path: str, json: Any = None, **kw: Any) -> Response:
        return self.request("POST", path, json, **kw)

    def patch(self, path: str, json: Any = None, **kw: Any) -> Response:
        return self.request("PATCH", path, json, **kw)

    def delete(self, path: str, **kw: Any) -> Response:
        return self.request("DELETE", path, **kw)

    def csrf_token(self) -> str:
        # Cached per session; sessions change on login/logout, which resets the cache.
        if self._csrf is None:
            response = self.request("GET", "/v1/auth/csrf")
            assert response.status_code == 200, response.text
            self._csrf = response.json()["csrf_token"]
        return self._csrf

    def register(
        self, email: str | None = None, password: str = PASSWORD, name: str = "Test User"
    ) -> Response:
        self.email = email or random_email()
        self._csrf = None
        response = self.request(
            "POST",
            "/v1/auth/register",
            {"email": self.email, "password": password, "display_name": name},
        )
        if response.status_code == 201:
            self.user_id = response.json()["id"]
        return response

    def login(self, email: str, password: str = PASSWORD) -> Response:
        self._csrf = None
        return self.request("POST", "/v1/auth/login", {"email": email, "password": password})

    def create_org(self, name: str = "Acme") -> str:
        response = self.post("/v1/organizations", {"name": name})
        assert response.status_code == 201, response.text
        return str(response.json()["id"])

    def create_project(self, org_id: str, name: str = "Site") -> str:
        response = self.post(f"/v1/organizations/{org_id}/projects", {"name": name})
        assert response.status_code == 201, response.text
        return str(response.json()["id"])

    def add_member(self, org_id: str, email: str, role: str = "member") -> Response:
        return self.post(f"/v1/organizations/{org_id}/members", {"email": email, "role": role})


def registered(new_api: Any, name: str = "Test User") -> Api:
    api: Api = new_api()
    response = api.register(name=name)
    assert response.status_code == 201, response.text
    return api


def project_base(org_id: str, project_id: str) -> str:
    return f"/v1/organizations/{org_id}/projects/{project_id}"


def verified_project(api: Api, verifier, host: str = "shop.example.com") -> tuple[str, str]:  # type: ignore[no-untyped-def]
    """An organization and project whose site is set and ownership-verified via the fake DNS."""
    org = api.create_org()
    project = api.create_project(org)
    base = project_base(org, project)
    set_site = api.request("PUT", f"{base}/site", {"site_url": f"https://{host}/"})
    assert set_site.status_code == 200, set_site.text
    site = set_site.json()["site"]["verification"]
    verifier.txt[site["dns_record_name"]] = [site["dns_record_value"]]
    verified = api.post(f"{base}/verify", {"method": "dns"})
    assert verified.status_code == 200 and verified.json()["verified"] is True, verified.text
    return org, project
