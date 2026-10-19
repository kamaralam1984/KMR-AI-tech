"""The egress proxy, exercised over real sockets against local fixture servers."""

import asyncio
from collections.abc import AsyncIterator, Callable

import pytest

from app.scanning.egress_proxy import ProxyConfig, start_server
from app.scanning.net_policy import NetPolicy

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class Fixture:
    """A tiny origin server that records what it received and answers with a fixed body."""

    def __init__(self) -> None:
        self.received: list[bytes] = []
        self.server: asyncio.Server | None = None
        self.port = 0

    async def start(self) -> None:
        async def handle(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
            data = await r.read(65536)
            self.received.append(data)
            body = b"hello from fixture"
            w.write(
                b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s"
                % (len(body), body)
            )
            await w.drain()
            w.close()

        self.server = await asyncio.start_server(handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()


@pytest.fixture
async def origin() -> AsyncIterator[Fixture]:
    fixture = Fixture()
    await fixture.start()
    yield fixture
    await fixture.stop()


async def run_proxy(config: ProxyConfig) -> tuple[asyncio.Server, int]:
    config.log = False
    server = await start_server(config)
    return server, server.sockets[0].getsockname()[1]


async def talk(port: int, raw: bytes, *, read_timeout: float = 3.0) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(raw)
    await writer.drain()
    try:
        return await asyncio.wait_for(reader.read(65536), read_timeout)
    finally:
        writer.close()


def first_line(response: bytes) -> str:
    return response.split(b"\r\n", 1)[0].decode()


@pytest.fixture
def make_proxy() -> Callable[..., object]:
    return run_proxy


async def test_default_policy_refuses_loopback_targets(origin: Fixture) -> None:
    # The port is allowed, so the refusal below comes from the address check alone.
    config = ProxyConfig(policy=NetPolicy(allowed_ports=(origin.port,)))
    server, port = await run_proxy(config)
    try:
        response = await talk(port, f"CONNECT 127.0.0.1:{origin.port} HTTP/1.1\r\n\r\n".encode())
        assert first_line(response).startswith("HTTP/1.1 403")
        assert b"blocked_address" in response
        assert origin.received == []  # nothing was ever sent to the target
    finally:
        server.close()


@pytest.mark.parametrize(
    ("authority", "code"),
    [
        ("169.254.169.254:80", b"blocked_address"),
        ("localhost:80", b"blocked_host"),
        ("[::1]:80", b"blocked_address"),
        ("2130706433:80", b"blocked_host"),
        ("10.0.0.1:443", b"blocked_address"),
        ("example.com:22", b"blocked_port"),
        ("example.com:6379", b"blocked_port"),
    ],
)
async def test_connect_to_dangerous_targets_is_blocked(authority: str, code: bytes) -> None:
    server, port = await run_proxy(ProxyConfig())
    try:
        response = await talk(port, f"CONNECT {authority} HTTP/1.1\r\n\r\n".encode())
        assert first_line(response).startswith("HTTP/1.1 403")
        assert code in response
    finally:
        server.close()


async def test_dns_answer_with_a_private_address_is_blocked() -> None:
    config = ProxyConfig(resolver=lambda host, port: ["10.1.2.3"])
    server, port = await run_proxy(config)
    try:
        response = await talk(port, b"CONNECT evil.example:443 HTTP/1.1\r\n\r\n")
        assert first_line(response).startswith("HTTP/1.1 403")
        assert b"blocked_address" in response
    finally:
        server.close()


async def test_mixed_dns_answer_is_blocked() -> None:
    config = ProxyConfig(resolver=lambda host, port: ["93.184.216.34", "127.0.0.1"])
    server, port = await run_proxy(config)
    try:
        response = await talk(port, b"CONNECT evil.example:443 HTTP/1.1\r\n\r\n")
        assert b"blocked_address" in response
    finally:
        server.close()


async def test_name_is_resolved_once_and_the_checked_address_is_used(origin: Fixture) -> None:
    """Pinning: the connection goes to the address that was verified, with no second lookup."""
    lookups: list[str] = []

    def resolver(host: str, port: int) -> list[str]:
        lookups.append(host)
        return ["127.0.0.1"]

    config = ProxyConfig(
        policy=NetPolicy(allowed_networks=("127.0.0.0/8",), allowed_ports=(origin.port,)),
        resolver=resolver,
    )
    server, port = await run_proxy(config)
    try:
        raw = (
            f"GET http://rebind.example:{origin.port}/page?x=1 HTTP/1.1\r\n"
            f"Host: rebind.example:{origin.port}\r\n"
            "Proxy-Connection: keep-alive\r\nProxy-Authorization: Basic c2VjcmV0\r\n"
            "Accept: */*\r\n\r\n"
        ).encode()
        response = await talk(port, raw)
        assert first_line(response) == "HTTP/1.1 200 OK"
        assert b"hello from fixture" in response
        assert lookups == ["rebind.example"]
        forwarded = origin.received[0].decode().lower()
        assert forwarded.startswith("get /page?x=1 http/1.1")  # origin-form, not absolute
        assert "proxy-connection" not in forwarded
        assert "proxy-authorization" not in forwarded
        assert "connection: close" in forwarded
        assert f"host: rebind.example:{origin.port}" in forwarded
    finally:
        server.close()


async def test_connect_tunnel_passes_bytes_when_the_policy_allows_the_target(
    origin: Fixture,
) -> None:
    config = ProxyConfig(
        policy=NetPolicy(allowed_networks=("127.0.0.0/8",), allowed_ports=(origin.port,))
    )
    server, port = await run_proxy(config)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(f"CONNECT 127.0.0.1:{origin.port} HTTP/1.1\r\n\r\n".encode())
        await writer.drain()
        established = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 3)
        assert established.startswith(b"HTTP/1.1 200")
        writer.write(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        await writer.drain()
        data = await asyncio.wait_for(reader.read(65536), 3)
        assert b"hello from fixture" in data
        writer.close()
    finally:
        server.close()


@pytest.mark.parametrize(
    "raw",
    [
        b"GET / HTTP/1.1\r\nHost: x\r\n\r\n",  # not a proxy request
        b"GET https://example.com/ HTTP/1.1\r\n\r\n",  # https must use CONNECT
        b"GARBAGE\r\n\r\n",
        b"GET http://example.com/ HTTP/1.1\r\nbad header line\r\n\r\n",
        b"GET http://example.com/ HTTP/2\r\n\r\n",
    ],
)
async def test_malformed_or_non_proxy_requests_get_400(raw: bytes) -> None:
    server, port = await run_proxy(ProxyConfig())
    try:
        assert first_line(await talk(port, raw)).startswith("HTTP/1.1 400")
    finally:
        server.close()


async def test_oversized_request_head_is_rejected() -> None:
    server, port = await run_proxy(ProxyConfig())
    try:
        raw = b"GET http://example.com/ HTTP/1.1\r\nX: " + b"a" * 40000 + b"\r\n\r\n"
        assert first_line(await talk(port, raw)).startswith("HTTP/1.1 431")
    finally:
        server.close()


async def test_connection_cap_returns_503() -> None:
    config = ProxyConfig(max_connections=1, head_timeout=2.0)
    server, port = await run_proxy(config)
    try:
        # The first client holds the only slot by never finishing its request head.
        _, hog = await asyncio.open_connection("127.0.0.1", port)
        hog.write(b"CONNECT ")
        await hog.drain()
        await asyncio.sleep(0.2)
        response = await talk(port, b"CONNECT example.com:443 HTTP/1.1\r\n\r\n")
        assert first_line(response).startswith("HTTP/1.1 503")
        hog.close()
    finally:
        server.close()


async def test_idle_tunnels_are_closed(origin: Fixture) -> None:
    config = ProxyConfig(
        policy=NetPolicy(allowed_networks=("127.0.0.0/8",), allowed_ports=(origin.port,)),
        idle_timeout=0.3,
    )
    server, port = await run_proxy(config)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(f"CONNECT 127.0.0.1:{origin.port} HTTP/1.1\r\n\r\n".encode())
        await writer.drain()
        await reader.readuntil(b"\r\n\r\n")
        # No traffic: the proxy must hang up on its own rather than hold the slot forever.
        assert await asyncio.wait_for(reader.read(1), 3) == b""
        writer.close()
    finally:
        server.close()


async def test_byte_cap_closes_the_connection() -> None:
    async def flood(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        await r.read(1)
        try:
            for _ in range(200):
                w.write(b"x" * 65536)
                await w.drain()
        except (ConnectionError, OSError):
            pass
        w.close()

    flooder = await asyncio.start_server(flood, "127.0.0.1", 0)
    flood_port = flooder.sockets[0].getsockname()[1]
    config = ProxyConfig(
        policy=NetPolicy(allowed_networks=("127.0.0.0/8",), allowed_ports=(flood_port,)),
        max_bytes_per_connection=1_000_000,
    )
    server, port = await run_proxy(config)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(f"CONNECT 127.0.0.1:{flood_port} HTTP/1.1\r\n\r\n".encode())
        await writer.drain()
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"go")
        await writer.drain()
        received = 0
        while chunk := await asyncio.wait_for(reader.read(65536), 5):
            received += len(chunk)
        assert received < 2_000_000  # cut off near the cap, nowhere near the 13 MB offered
        writer.close()
    finally:
        server.close()
        flooder.close()
