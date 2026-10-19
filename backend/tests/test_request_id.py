from fastapi.testclient import TestClient


def test_generates_request_id(client: TestClient) -> None:
    response = client.get("/health")
    assert len(response.headers["X-Request-ID"]) >= 8


def test_reuses_safe_incoming_request_id(client: TestClient) -> None:
    response = client.get("/health", headers={"X-Request-ID": "trace-abc-12345"})
    assert response.headers["X-Request-ID"] == "trace-abc-12345"


def test_replaces_unsafe_incoming_request_id(client: TestClient) -> None:
    response = client.get("/health", headers={"X-Request-ID": "bad id with spaces!"})
    assert response.headers["X-Request-ID"] != "bad id with spaces!"
    assert " " not in response.headers["X-Request-ID"]
