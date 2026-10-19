"""A fake desktop and a fake server, shared by the tests."""

import json
import uuid
from typing import Any

import httpx

from kmr_desktop.client import DeskClient
from kmr_desktop.model import RawNode, WindowInfo
from kmr_desktop.platforms.fake import FakePlatform, FakeWindow

FAKE_EMAIL = "jane.doe@example.com"
FAKE_PHONE = "9876543210"


def node(
    nid: str, role: str, name: str = "", text: str = "", actionable: bool = True, password: bool = False
) -> RawNode:
    return RawNode(nid, role, name, text, [], actionable, password)  # type: ignore[arg-type]


def scene() -> FakePlatform:
    shop = FakeWindow(
        WindowInfo("w1", "Chromium", "Shop - Support", True),
        [
            node("w1.n0", "button", "Reload"),
            node("w1.n1", "button", ""),
            node("w1.n2", "label", "", f"Could not save for {FAKE_EMAIL}, call {FAKE_PHONE}", False),
            node("w1.n3", "text_field", "Search"),
            node("w1.n4", "text_field", "Password", password=True),
            node("w1.n5", "button", "Submit order"),
            node("w1.n6", "image", "", "", False),  # dropped: nothing to say about it
        ],
    )
    vault = FakeWindow(
        WindowInfo("w2", "Bitwarden", "My vault", False),
        [node("w2.n0", "label", "", "hunter2 for the bank", False)],
    )
    mail = FakeWindow(
        WindowInfo("w3", "Files", "Inbox - Compose message", False),
        [node("w3.n0", "label", "", "private words", False)],
    )
    return FakePlatform([shop, vault, mail])


class FakeServer:
    """Stands in for /desk/v1: records every request and returns the steps it is told to."""

    def __init__(self, steps: list[dict[str, Any]] | None = None) -> None:
        self.requests: list[tuple[str, str, Any]] = []
        self.steps = steps or []
        self.reports: list[tuple[str, str]] = []
        self.fail_chat: tuple[int, str] | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.requests.append((request.method, request.url.path, body))
        assert request.headers["authorization"].startswith("Bearer ")
        if request.url.path == "/desk/v1/me":
            return httpx.Response(
                200,
                json={
                    "org_name": "Acme",
                    "user_name": "Asha",
                    "provider_label": "Built-in checks (not AI)",
                    "is_ai": False,
                    "daily_chat_limit": 200,
                    "daily_chat_used": 3,
                },
            )
        if request.url.path == "/desk/v1/chat":
            if self.fail_chat:
                return httpx.Response(self.fail_chat[0], json={"error": {"message": self.fail_chat[1]}})
            return httpx.Response(
                200,
                json={
                    "reply": "Try the reload button.",
                    "language": "en",
                    "steps": self.steps,
                    "provider_label": "Built-in checks (not AI)",
                    "is_ai": False,
                    "notice": "n",
                },
            )
        if request.url.path.endswith("/report"):
            self.reports.append((request.url.path.split("/")[-2], body["outcome"]))
            return httpx.Response(204)
        return httpx.Response(404, json={})

    def client(self) -> DeskClient:
        return DeskClient(
            "http://server.test", "kmr_dt_testtoken", transport=httpx.MockTransport(self.handler)
        )


def step(
    kind: str, window_id: str, node_id: str, role: str, name: str, suggested: str | None = None
) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "kind": kind,
        "window_id": window_id,
        "node_id": node_id,
        "role": role,
        "name": name,
        "why": "because",
        "suggested_text": suggested,
    }
