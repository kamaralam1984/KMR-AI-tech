import socket

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings


def test_health_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_with_nothing_configured(client: TestClient) -> None:
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"database": "not_configured", "redis": "not_configured"},
    }


def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_ready_reports_unreachable_dependency(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    monkeypatch.setenv("KMR_REDIS_URL", f"redis://127.0.0.1:{_closed_port()}/0")
    get_settings.cache_clear()
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert response.json()["checks"]["redis"] == "unreachable"


def test_ready_reports_reachable_dependency(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        monkeypatch.setenv("KMR_DATABASE_URL", f"postgresql://user:pw@127.0.0.1:{port}/db")
        get_settings.cache_clear()
        response = client.get("/ready")
    assert response.status_code == 200
    assert response.json()["checks"]["database"] == "reachable"


def test_ready_response_never_leaks_connection_details(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    monkeypatch.setenv(
        "KMR_DATABASE_URL", f"postgresql://user:hunter2@127.0.0.1:{_closed_port()}/db"
    )
    get_settings.cache_clear()
    text = client.get("/ready").text
    assert "hunter2" not in text
    assert "127.0.0.1" not in text
