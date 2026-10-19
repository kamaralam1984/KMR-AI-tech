"""The API-check runner against a real local server, through the real egress proxy."""

import pytest

from app.scanning.api_checks import CheckSpec, ProxyTransport, run_check
from app.scanning.egress_proxy import ProxyConfig, start_server
from app.scanning.net_policy import NetPolicy
from tests.fixture_site import FixtureSite

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def env():  # type: ignore[no-untyped-def]
    site = FixtureSite()
    site.start()
    loopback = NetPolicy(allowed_ports=(site.port,), allowed_networks=("127.0.0.0/8",))
    proxy = await start_server(ProxyConfig(policy=loopback, log=False))
    url = f"http://127.0.0.1:{proxy.sockets[0].getsockname()[1]}"
    transport = ProxyTransport(url)

    async def check(path: str, **over):  # type: ignore[no-untyped-def]
        spec = CheckSpec(
            path=path,
            method=over.pop("method", "GET"),
            expected_status=over.pop("expected_status", 200),
            max_latency_ms=over.pop("max_latency_ms", 3000),
            response_schema=over.pop("schema", None),
        )
        return await run_check(
            spec,
            origin=site.base,
            allowed_host="127.0.0.1",
            transport=transport,
            policy=loopback,
            timeout=5,
            request_id="kmr-chk-test",
            **over,
        )

    yield site, check
    proxy.close()
    site.stop()


async def test_a_good_response_passes_and_the_request_id_arrives(env) -> None:  # type: ignore[no-untyped-def]
    site, check = env
    schema = {
        "type": "object",
        "required": ["id", "name"],
        "properties": {"id": {"type": "integer"}},
    }
    result = await check("/api/ok", schema=schema)
    assert result.status == "passed" and result.http_status == 200 and result.reasons == []
    assert site.request_headers[-1]["x-request-id"] == "kmr-chk-test"


async def test_a_server_error_is_reported(env) -> None:  # type: ignore[no-untyped-def]
    _site, check = env
    result = await check("/api/fail")
    assert result.status == "failed" and result.http_status == 500
    assert result.reasons[0]["code"] == "status_mismatch"


async def test_a_wrong_shape_is_reported_without_the_value(env) -> None:  # type: ignore[no-untyped-def]
    _site, check = env
    result = await check(
        "/api/bad-shape", schema={"type": "object", "properties": {"id": {"type": "integer"}}}
    )
    assert result.status == "failed"
    assert "SECRET-VALUE-123" not in repr(result)


async def test_html_where_json_is_expected(env) -> None:  # type: ignore[no-untyped-def]
    _site, check = env
    result = await check("/api/not-json", schema={"type": "object"})
    assert "invalid_json" in [r["code"] for r in result.reasons]


async def test_slow_responses_are_flagged(env) -> None:  # type: ignore[no-untyped-def]
    _site, check = env
    result = await check("/api/slow", max_latency_ms=200)
    assert "too_slow" in [r["code"] for r in result.reasons]


async def test_redirects_are_not_followed(env) -> None:  # type: ignore[no-untyped-def]
    site, check = env
    result = await check("/api/redirect")
    assert result.http_status == 302 and result.status == "failed"
    assert not any("elsewhere" in path for path in site.seen())


async def test_oversized_bodies_are_cut_off_and_flagged(env) -> None:  # type: ignore[no-untyped-def]
    _site, check = env
    result = await check("/api/big")
    assert "response_too_large" in [r["code"] for r in result.reasons]
    assert (result.response_bytes or 0) <= 256 * 1024


async def test_credentials_are_sent_and_a_wrong_one_is_called_what_it_is(env) -> None:  # type: ignore[no-untyped-def]
    site, check = env
    good = await check("/api/auth", credentials=("Authorization", "Bearer good-token"))
    assert good.status == "passed"
    bad = await check("/api/auth", credentials=("Authorization", "Bearer wrong-token"))
    assert "auth_rejected" in [r["code"] for r in bad.reasons]
    assert "wrong-token" not in repr(bad)
    anonymous = await check("/api/auth")
    assert "status_mismatch" in [
        r["code"] for r in anonymous.reasons
    ]  # no credential, so not "rejected"


async def test_the_check_cannot_leave_the_verified_host(env) -> None:  # type: ignore[no-untyped-def]
    site, _check = env
    loopback = NetPolicy(allowed_ports=(site.port,), allowed_networks=("127.0.0.0/8",))
    result = await run_check(
        CheckSpec("/api/ok", "GET", 200, 3000, None),
        origin=site.base,
        allowed_host="shop.example.com",
        transport=ProxyTransport("http://127.0.0.1:1"),
        policy=loopback,
        timeout=2,
        request_id="x",
    )
    assert result.status == "error" and result.reasons[0] == {
        "code": "blocked",
        "detail": "other_host",
    }
    assert site.requests == []


async def test_the_proxy_policy_still_applies_to_checks(env) -> None:  # type: ignore[no-untyped-def]
    """A proxy with the production policy refuses a loopback target even if the app allowed it."""
    site, _check = env
    strict = await start_server(
        ProxyConfig(policy=NetPolicy(allowed_ports=(site.port,)), log=False)
    )
    try:
        transport = ProxyTransport(f"http://127.0.0.1:{strict.sockets[0].getsockname()[1]}")
        loopback = NetPolicy(allowed_ports=(site.port,), allowed_networks=("127.0.0.0/8",))
        result = await run_check(
            CheckSpec("/api/ok", "GET", 200, 3000, None),
            origin=site.base,
            allowed_host="127.0.0.1",
            transport=transport,
            policy=loopback,
            timeout=5,
            request_id="x",
        )
    finally:
        strict.close()
    assert result.status == "error"
    assert result.reasons[0]["code"] == "blocked"
    assert site.requests == []


async def test_an_unreachable_proxy_is_a_clear_error(env) -> None:  # type: ignore[no-untyped-def]
    site, _check = env
    loopback = NetPolicy(allowed_ports=(site.port,), allowed_networks=("127.0.0.0/8",))
    result = await run_check(
        CheckSpec("/api/ok", "GET", 200, 3000, None),
        origin=site.base,
        allowed_host="127.0.0.1",
        transport=ProxyTransport("http://127.0.0.1:1"),
        policy=loopback,
        timeout=2,
        request_id="x",
    )
    assert result.status == "error" and result.reasons[0]["code"] == "proxy_unavailable"
