import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.logging_config import JsonFormatter, redact
from app.main import create_app


def test_secret_settings_are_hidden_in_repr() -> None:
    settings = Settings(database_url="postgresql://user:hunter2@host/db")
    assert "hunter2" not in repr(settings)
    assert "hunter2" not in str(settings)


def test_docs_disabled_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KMR_APP_ENV", "production")
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404


def test_docs_enabled_outside_production(client: TestClient) -> None:
    assert client.get("/docs").status_code == 200


def test_redact_masks_sensitive_keys() -> None:
    data = {"Authorization": "Bearer abc", "nested": {"session_id": "xyz", "ok": 1}}
    expected = {"Authorization": "[REDACTED]", "nested": {"session_id": "[REDACTED]", "ok": 1}}
    assert redact(data) == expected


def test_json_formatter_redacts_extras() -> None:
    record = logging.LogRecord("t", logging.INFO, "f", 1, "hello", (), None)
    record.cookie = "secret-cookie"
    record.path = "/health"
    payload = json.loads(JsonFormatter().format(record))
    assert payload["cookie"] == "[REDACTED]"
    assert payload["path"] == "/health"
    assert "secret-cookie" not in json.dumps(payload)
