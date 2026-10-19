"""Forward proxy that is the only way scanner traffic reaches the internet. Standard library only.

It runs in its own container. The browser container sits on an internal network whose only
neighbour is this proxy, so every request the browser makes, including requests triggered by
page JavaScript and redirects, passes through here.

For every request the proxy (1) validates the target, (2) resolves the name itself, (3) checks that
EVERY resolved address is public, and (4) connects to one of the addresses it just checked. The
browser never resolves names (see the host-resolver rule in the scanner), so a hostile DNS server
cannot make the check and the connection disagree (DNS rebinding).

Supported: HTTP CONNECT (all HTTPS) and absolute-URI plain HTTP, one request per connection.
There is deliberately no MITM, no caching and no authentication to other services.
"""

import asyncio
import json
import os
import sys
from dataclasses import dataclass, field

try:  # inside the repo
    from app.scanning.net_policy import (
        NetPolicy,
        Resolver,
        TargetRejected,
        resolve_public_addresses,
        system_resolver,
        validate_url,
    )
except ImportError:  # inside the container, where the file sits next to net_policy.py
    from net_policy import (  # type: ignore[no-redef,import-not-found]
        NetPolicy,
        Resolver,
        TargetRejected,
        resolve_public_addresses,
        system_resolver,
        validate_url,
    )

MAX_HEAD_BYTES = 16 * 1024
MAX_HEADER_LINES = 100
_HOP_BY_HOP = {
    "proxy-connection",
    "proxy-authorization",
    "connection",
    "keep-alive",
    "te",
    "trailer",
    "upgrade",
}


@dataclass
class ProxyConfig:
    policy: NetPolicy = field(default_factory=NetPolicy)
    resolver: Resolver = system_resolver
    max_connections: int = 64
    connect_timeout: float = 10.0
    head_timeout: float = 10.0
    idle_timeout: float = 30.0
    max_lifetime: float = 180.0
    max_bytes_per_connection: int = 100 * 1024 * 1024
    log: bool = True


def _log(config: ProxyConfig, **fields: object) -> None:
    if config.log:
        print(json.dumps({"service": "egress-proxy", **fields}), flush=True)


_STATUS_TEXT = {
    400: "Bad Request",
    403: "Forbidden",
    431: "Request Header Fields Too Large",
    502: "Bad Gateway",
    503: "Service Unavailable",
}


async def _reply(writer: asyncio.StreamWriter, status: int, code: str) -> None:
    body = f"{code}\n".encode()
    head = (
        f"HTTP/1.1 {status} {_STATUS_TEXT.get(status, 'Error')}\r\n"
        f"X-KMR-Blocked: {code}\r\n"
        # Lets a scanned page's browser expose this refusal instead of hiding it behind CORS,
        # so the scanner can record why. The body only names the reason; nothing is leaked.
        "Access-Control-Allow-Origin: *\r\n"
        "Access-Control-Expose-Headers: X-KMR-Blocked\r\n"
        f"Content-Type: text/plain\r\nContent-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode()
    try:
        writer.write(head + body)
        await writer.drain()
    except (ConnectionError, OSError):
        pass


async def _close(writer: asyncio.StreamWriter) -> None:
    try:
        writer.close()
        await writer.wait_closed()
    except (ConnectionError, OSError):
        pass


@dataclass
class _Counter:
    total: int = 0


async def _pipe(
    source: asyncio.StreamReader,
    sink: asyncio.StreamWriter,
    counter: _Counter,
    config: ProxyConfig,
) -> None:
    try:
        while True:
            data = await asyncio.wait_for(source.read(65536), config.idle_timeout)
            if not data:
                return
            counter.total += len(data)
            if counter.total > config.max_bytes_per_connection:
                return
            sink.write(data)
            await sink.drain()
    except (TimeoutError, ConnectionError, OSError):
        return


async def _relay(
    client_r: asyncio.StreamReader,
    client_w: asyncio.StreamWriter,
    upstream_r: asyncio.StreamReader,
    upstream_w: asyncio.StreamWriter,
    config: ProxyConfig,
) -> None:
    counter = _Counter()
    tasks = [
        asyncio.create_task(_pipe(client_r, upstream_w, counter, config)),
        asyncio.create_task(_pipe(upstream_r, client_w, counter, config)),
    ]
    try:
        await asyncio.wait(tasks, timeout=config.max_lifetime, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _connect_pinned(
    host: str, port: int, config: ProxyConfig
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Resolve once, verify every address, then connect to a verified address only."""
    loop = asyncio.get_running_loop()
    addresses = await loop.run_in_executor(
        None, resolve_public_addresses, host, port, config.policy, config.resolver
    )
    last_error: Exception | None = None
    for address in addresses:
        try:
            return await asyncio.wait_for(
                asyncio.open_connection(address, port), config.connect_timeout
            )
        except (TimeoutError, OSError) as exc:
            last_error = exc
    raise ConnectionError(f"could not connect: {last_error}")


def _split_host_port(authority: str) -> tuple[str, int]:
    if authority.startswith("["):
        host, _, rest = authority[1:].partition("]")
        port_text = rest.lstrip(":")
    else:
        host, _, port_text = authority.rpartition(":")
        if not host:
            host, port_text = authority, ""
    return host, int(port_text) if port_text else 443


def _parse_headers(lines: list[str]) -> list[tuple[str, str]]:
    headers: list[tuple[str, str]] = []
    for line in lines:
        name, sep, value = line.partition(":")
        if not sep or not name or name != name.strip() or line[0] in " \t":
            raise ValueError("malformed header")
        headers.append((name, value.strip()))
    return headers


async def handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    config: ProxyConfig,
    gate: asyncio.Semaphore,
) -> None:
    upstream_w: asyncio.StreamWriter | None = None
    try:
        if gate.locked():
            await _reply(writer, 503, "proxy_busy")
            return
        async with gate:
            try:
                head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), config.head_timeout)
            except asyncio.LimitOverrunError:
                await _reply(writer, 431, "head_too_large")
                return
            except (asyncio.IncompleteReadError, TimeoutError):
                return
            if len(head) > MAX_HEAD_BYTES:
                await _reply(writer, 431, "head_too_large")
                return
            try:
                lines = head[:-4].decode("latin-1").split("\r\n")
                method, target, version = lines[0].split(" ")
                headers = _parse_headers(lines[1:])
                if len(headers) > MAX_HEADER_LINES or not version.startswith("HTTP/1."):
                    raise ValueError("bad head")
            except ValueError:
                await _reply(writer, 400, "bad_request")
                return

            try:
                if method.upper() == "CONNECT":
                    host, port = _split_host_port(target)
                    validate_url(
                        f"https://{target if ':' in target else target + ':443'}/", config.policy
                    )
                    scheme = "https"
                else:
                    scheme = target.split(":", 1)[0].lower()
                    if scheme != "http":
                        await _reply(writer, 400, "absolute_http_uri_required")
                        return
                    parsed = validate_url(target, config.policy)
                    host, port = parsed.host, parsed.port
                upstream_r, upstream_w = await _connect_pinned(host, port, config)
            except TargetRejected as rejected:
                _log(
                    config,
                    decision="blocked",
                    method=method,
                    code=rejected.code,
                    detail=rejected.detail,
                )
                await _reply(writer, 403 if rejected.code != "dns_failed" else 502, rejected.code)
                return
            except ValueError:
                await _reply(writer, 400, "bad_request")
                return
            except ConnectionError:
                _log(config, decision="unreachable", method=method, host=host, port=port)
                await _reply(writer, 502, "upstream_unreachable")
                return

            _log(config, decision="allowed", method=method, host=host, port=port)
            if method.upper() == "CONNECT":
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
            else:
                path = target.split("//", 1)[1]
                path = "/" + path.split("/", 1)[1] if "/" in path else "/"
                forwarded = [
                    f"{name}: {value}" for name, value in headers if name.lower() not in _HOP_BY_HOP
                ]
                if not any(name.lower() == "host" for name, _ in headers):
                    forwarded.append(f"Host: {host}")
                request = "\r\n".join(
                    [f"{method} {path} HTTP/1.1", *forwarded, "Connection: close"]
                )
                upstream_w.write(request.encode("latin-1") + b"\r\n\r\n")
                await upstream_w.drain()
            await _relay(reader, writer, upstream_r, upstream_w, config)
    except (ConnectionError, OSError, asyncio.CancelledError):
        pass
    finally:
        if upstream_w is not None:
            await _close(upstream_w)
        await _close(writer)


async def start_server(
    config: ProxyConfig, host: str = "127.0.0.1", port: int = 0
) -> asyncio.Server:
    gate = asyncio.Semaphore(config.max_connections)

    async def client_connected(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        await handle_client(r, w, config, gate)

    return await asyncio.start_server(client_connected, host, port, limit=MAX_HEAD_BYTES)


def config_from_environment() -> tuple[ProxyConfig, str, int]:
    """Container entrypoint settings. The private-target switch exists for tests only."""
    allow_private = os.environ.get("KMR_SCAN_ALLOW_PRIVATE") == "1"
    if allow_private and os.environ.get("KMR_APP_ENV") != "test":
        sys.exit("refusing to start: KMR_SCAN_ALLOW_PRIVATE is only honored when KMR_APP_ENV=test")
    listen = os.environ.get("KMR_PROXY_LISTEN", "0.0.0.0:3128")  # noqa: S104 - container-internal
    host, _, port = listen.rpartition(":")
    ports = tuple(int(p) for p in os.environ.get("KMR_SCAN_PORTS", "80,443").split(","))
    networks = ("127.0.0.0/8",) if allow_private else ()  # loopback only, never metadata
    policy = NetPolicy(allowed_ports=ports, allowed_networks=networks)
    return ProxyConfig(policy=policy), host, int(port)


async def _main() -> None:
    config, host, port = config_from_environment()
    server = await start_server(config, host, port)
    networks = config.policy.allowed_networks
    _log(config, event="listening", host=host, port=port, test_networks=networks)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(_main())
