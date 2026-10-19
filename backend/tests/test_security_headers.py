from fastapi.testclient import TestClient

from app.main import create_app


def test_api_answers_carry_security_headers_and_are_never_cacheable(api_app) -> None:  # type: ignore[no-untyped-def]
    client: TestClient = api_app.state.test_client
    for path in ("/health", "/v1/auth/csrf", "/v1/does-not-exist"):
        response = client.get(path)
        assert response.headers["x-content-type-options"] == "nosniff", path
        assert response.headers["x-frame-options"] == "DENY", path
        assert response.headers["referrer-policy"] == "no-referrer", path
        assert response.headers["cache-control"] == "no-store", path
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"], path
        assert "strict-transport-security" not in response.headers  # local: plain http


def test_hsts_is_sent_only_where_https_is_expected(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.config import get_settings

    monkeypatch.setenv("KMR_APP_ENV", "production")
    get_settings.cache_clear()
    try:
        client = TestClient(create_app())  # no lifespan: /health needs no database
        response = client.get("/health")
        assert "max-age=31536000" in response.headers["strict-transport-security"]
        assert response.headers["x-content-type-options"] == "nosniff"
        assert client.get("/docs").status_code == 404  # docs are off in production
    finally:
        get_settings.cache_clear()
