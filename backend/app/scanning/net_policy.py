"""Which network targets a scan (or ownership check) may reach. Standard library only.

This module is shared by the API (URL validation at save time) and the egress proxy (the actual
enforcement point, inside a container that has no third-party packages). It must stay free of
imports from the rest of the app.

Two layers, deliberately: `validate_url` rejects obviously bad targets early with a clear reason,
and `resolve_public_addresses` is the real control. The proxy resolves the name itself, checks
every address, and connects to one of the addresses it checked, so a name that answers with a
public address for the check and a private one for the connection (DNS rebinding) cannot win.
"""

import ipaddress
import re
import socket
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit

MAX_URL_LENGTH = 2048
DEFAULT_PORTS = {"http": 80, "https": 443}

# Explicit denylist, so safety does not rest on `ipaddress.is_global` alone (which has differed
# between Python versions) and so embedded-IPv4 transition ranges are refused outright.
_BLOCKED_NETWORKS = tuple(
    ipaddress.ip_network(net)
    for net in (
        "0.0.0.0/8",  # "this network"
        "10.0.0.0/8",  # private
        "100.64.0.0/10",  # carrier-grade NAT (also Alibaba Cloud metadata)
        "127.0.0.0/8",  # loopback
        "169.254.0.0/16",  # link-local, includes the cloud metadata address 169.254.169.254
        "172.16.0.0/12",  # private
        "192.0.0.0/24",  # IETF protocol assignments (Oracle Cloud legacy metadata)
        "192.0.2.0/24",  # documentation
        "192.168.0.0/16",  # private
        "198.18.0.0/15",  # benchmarking
        "198.51.100.0/24",  # documentation
        "203.0.113.0/24",  # documentation
        "224.0.0.0/4",  # multicast
        "240.0.0.0/4",  # reserved, includes broadcast
        "::/128",  # unspecified
        "::1/128",  # loopback
        "::ffff:0:0/96",  # IPv4-mapped (unwrapped and re-checked separately)
        "64:ff9b::/96",  # NAT64: embeds an IPv4 address
        "64:ff9b:1::/48",  # local-use NAT64
        "100::/64",  # discard-only
        "2001::/32",  # Teredo: embeds an IPv4 address
        "2001:db8::/32",  # documentation
        "2002::/16",  # 6to4: embeds an IPv4 address
        "fc00::/7",  # unique local (includes the AWS IPv6 metadata address)
        "fe80::/10",  # link-local
        "ff00::/8",  # multicast
    )
)

_BLOCKED_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".localdomain", ".home.arpa")
_BLOCKED_HOSTS = frozenset({"localhost", "metadata", "metadata.google.internal"})
# Anything made only of digits, hex digits, "x" and dots could be an alternative spelling of an
# IPv4 address (2130706433, 0x7f.1, 017700000001) that the OS resolver would accept.
_NUMERIC_LOOKING = re.compile(r"^[0-9a-fx.]+$", re.IGNORECASE)
_BAD_URL_CHARS = re.compile(r"[\s\x00-\x1f\x7f\\]")


class TargetRejected(Exception):  # noqa: N818 - domain term
    """A target is not allowed. `code` is stable and safe to show; `detail` is for logs."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class NetPolicy:
    allowed_ports: tuple[int, ...] = (80, 443)
    # A narrow test-only exception: private networks (CIDR strings) that are reachable anyway,
    # for example ("127.0.0.0/8",) so tests can scan a fixture site on this machine. Everything
    # else private stays blocked, including the cloud metadata range. Callers must refuse a
    # non-empty value outside tests.
    allowed_networks: tuple[str, ...] = ()


@dataclass(frozen=True)
class Target:
    scheme: str
    host: str  # lowercase ASCII (IDNA) without brackets
    port: int
    url: str  # normalized, without fragment

    @property
    def origin(self) -> str:
        default = DEFAULT_PORTS[self.scheme]
        suffix = "" if self.port == default else f":{self.port}"
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{self.scheme}://{host}{suffix}"


def is_public_address(address: str | ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True only for addresses on the public internet."""
    try:
        ip = ipaddress.ip_address(address) if isinstance(address, str) else address
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped  # ::ffff:127.0.0.1 is just 127.0.0.1
    if any(ip in net for net in _BLOCKED_NETWORKS if net.version == ip.version):
        return False
    return ip.is_global and not ip.is_multicast


def is_allowed_address(
    address: str | ipaddress.IPv4Address | ipaddress.IPv6Address, policy: NetPolicy
) -> bool:
    """Public addresses, plus any network the policy explicitly allows (tests only)."""
    if is_public_address(address):
        return True
    try:
        ip = ipaddress.ip_address(address) if isinstance(address, str) else address
    except ValueError:
        return False
    return any(
        ip in network
        for network in (ipaddress.ip_network(cidr) for cidr in policy.allowed_networks)
        if network.version == ip.version
    )


def _host_ascii(host: str) -> str:
    try:
        return host.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise TargetRejected("invalid_host", "host is not valid IDNA") from exc


def _parse_ip_literal(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def validate_url(url: str, policy: NetPolicy | None = None) -> Target:
    """Static checks on a URL. Does no DNS; resolution is `resolve_public_addresses`."""
    policy = policy or NetPolicy()
    if not url or len(url) > MAX_URL_LENGTH:
        raise TargetRejected("invalid_url", "empty or too long")
    if _BAD_URL_CHARS.search(url):
        raise TargetRejected("invalid_url", "whitespace, control characters or backslash")
    try:
        parts: SplitResult = urlsplit(url)
        port = parts.port
        hostname = parts.hostname
    except ValueError as exc:
        raise TargetRejected("invalid_url", "unparseable") from exc

    scheme = parts.scheme.lower()
    if scheme not in DEFAULT_PORTS:
        raise TargetRejected("unsupported_scheme", scheme)
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise TargetRejected("credentials_in_url")
    if not hostname:
        raise TargetRejected("invalid_host", "missing host")

    host = hostname.rstrip(".")
    literal = _parse_ip_literal(host)
    if literal is not None:
        host = literal.compressed
    else:
        host = _host_ascii(host)
        if host in _BLOCKED_HOSTS or host.endswith(_BLOCKED_HOST_SUFFIXES):
            raise TargetRejected("blocked_host", host)
        if _NUMERIC_LOOKING.match(host):
            raise TargetRejected("blocked_host", "numeric host")
        if "." not in host:
            raise TargetRejected("blocked_host", "single-label host")

    port = port if port is not None else DEFAULT_PORTS[scheme]
    if port not in policy.allowed_ports:
        raise TargetRejected("blocked_port", str(port))
    if literal is not None and not is_allowed_address(literal, policy):
        raise TargetRejected("blocked_address", host)

    path = parts.path or "/"
    query = f"?{parts.query}" if parts.query else ""
    bracketed = f"[{host}]" if ":" in host else host
    default = DEFAULT_PORTS[scheme]
    netloc = bracketed if port == default else f"{bracketed}:{port}"
    return Target(scheme=scheme, host=host, port=port, url=f"{scheme}://{netloc}{path}{query}")


Resolver = Callable[[str, int], list[str]]


def system_resolver(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def resolve_public_addresses(
    host: str,
    port: int,
    policy: NetPolicy | None = None,
    resolver: Resolver = system_resolver,
) -> list[str]:
    """Resolves `host` and returns its addresses only if EVERY one is allowed.

    Requiring all of them (not just one) stops a name that returns a public and a private record
    from being used to reach the private one. Callers must connect to a returned address, never
    re-resolve the name.
    """
    policy = policy or NetPolicy()
    literal = _parse_ip_literal(host)
    if literal is not None:
        addresses = [literal.compressed]
    else:
        try:
            addresses = resolver(host, port)
        except OSError as exc:
            raise TargetRejected("dns_failed", host) from exc
    if not addresses:
        raise TargetRejected("dns_failed", host)
    for address in addresses:
        if not is_allowed_address(address, policy):
            raise TargetRejected("blocked_address", f"{host} -> {address}")
    return addresses
