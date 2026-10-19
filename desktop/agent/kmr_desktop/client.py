"""Talks to the KMR AI server (/desk/v1) with the device token."""

from typing import Any

import httpx


class DeskError(Exception):
    """A failure whose message is safe to show the person."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class DeskClient:
    def __init__(self, base_url: str, token: str, transport: httpx.BaseTransport | None = None) -> None:
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=90,
            transport=transport,
            follow_redirects=False,
        )

    def _call(self, method: str, path: str, json: Any = None) -> Any:
        try:
            response = self._http.request(method, path, json=json)
        except httpx.HTTPError:
            raise DeskError(
                0, "Cannot reach the KMR AI server. Check the address and your connection."
            ) from None
        if response.status_code == 204:
            return None
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code >= 400:
            message = (body.get("error") or {}).get("message") if isinstance(body, dict) else None
            raise DeskError(response.status_code, message or f"The server answered {response.status_code}.")
        return body

    def me(self) -> dict[str, Any]:
        return self._call("GET", "/desk/v1/me")  # type: ignore[no-any-return]

    def chat(self, payload: dict) -> dict[str, Any]:  # type: ignore[type-arg]
        return self._call("POST", "/desk/v1/chat", payload)  # type: ignore[no-any-return]

    def report(self, step_id: str, outcome: str) -> None:
        self._call("POST", f"/desk/v1/steps/{step_id}/report", {"outcome": outcome})

    def close(self) -> None:
        self._http.close()
