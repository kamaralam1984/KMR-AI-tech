from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel


class Payload(BaseModel):
    count: int


def test_not_found_uses_standard_error_format(client: TestClient) -> None:
    response = client.get("/does-not-exist")
    assert response.status_code == 404
    body = response.json()["error"]
    assert body["code"] == "not_found"
    assert body["request_id"] == response.headers["X-Request-ID"]


def test_validation_error_does_not_echo_submitted_values(app: FastAPI) -> None:
    @app.post("/_test/payload")
    async def payload(data: Payload) -> dict[str, int]:
        return {"count": data.count}

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/_test/payload", json={"count": "super-secret-value"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert "super-secret-value" not in response.text
    assert response.json()["error"]["details"][0]["location"] == ["body", "count"]


def test_unhandled_exception_returns_generic_500_with_request_id(app: FastAPI) -> None:
    @app.get("/_test/boom")
    async def boom() -> None:
        raise RuntimeError("database password is hunter2")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/_test/boom", headers={"X-Request-ID": "abcdef123456"})
    assert response.status_code == 500
    assert "hunter2" not in response.text
    body = response.json()["error"]
    assert body["code"] == "internal_error"
    assert body["request_id"] == "abcdef123456"
    assert response.headers["X-Request-ID"] == "abcdef123456"
