import pytest

from app.scanning.net_policy import (
    NetPolicy,
    TargetRejected,
    is_public_address,
    resolve_public_addresses,
    validate_url,
)

BLOCKED_URLS = [
    ("file:///etc/passwd", "unsupported_scheme"),
    ("ftp://example.com/", "unsupported_scheme"),
    ("javascript:alert(1)", "unsupported_scheme"),
    ("gopher://example.com/", "unsupported_scheme"),
    ("http://user:pw@example.com/", "credentials_in_url"),
    ("http://example.com@evil.com/", "credentials_in_url"),
    ("http://127.0.0.1/", "blocked_address"),
    ("http://127.1/", "blocked_host"),
    ("http://0.0.0.0/", "blocked_address"),
    ("http://[::1]/", "blocked_address"),
    ("http://localhost/", "blocked_host"),
    ("http://LOCALHOST./", "blocked_host"),
    ("http://app.localhost/", "blocked_host"),
    ("http://2130706433/", "blocked_host"),
    ("http://0x7f.0.0.1/", "blocked_host"),
    ("http://017700000001/", "blocked_host"),
    ("http://169.254.169.254/latest/meta-data/", "blocked_address"),
    ("http://metadata.google.internal/", "blocked_host"),
    ("http://10.1.2.3/", "blocked_address"),
    ("http://172.16.0.1/", "blocked_address"),
    ("http://172.31.255.255/", "blocked_address"),
    ("http://192.168.0.10/", "blocked_address"),
    ("http://100.64.0.1/", "blocked_address"),
    ("http://100.100.100.200/", "blocked_address"),
    ("http://[::ffff:127.0.0.1]/", "blocked_address"),
    ("http://[::ffff:7f00:1]/", "blocked_address"),
    ("http://[fd00::1]/", "blocked_address"),
    ("http://[fd00:ec2::254]/", "blocked_address"),
    ("http://[fe80::1]/", "blocked_address"),
    ("http://[64:ff9b::7f00:1]/", "blocked_address"),
    ("http://[2002:7f00:1::]/", "blocked_address"),
    ("http://intranet/", "blocked_host"),
    ("http://example.com:22/", "blocked_port"),
    ("http://example.com:6379/", "blocked_port"),
    ("https://example.com:8443/", "blocked_port"),
    ("http://example.com\\@evil.com/", "invalid_url"),
    ("http://exa mple.com/", "invalid_url"),
    ("http://example.com/\r\nHost: evil", "invalid_url"),
    ("", "invalid_url"),
    ("http://" + "a" * 3000 + ".com/", "invalid_url"),
]


@pytest.mark.parametrize(("url", "code"), BLOCKED_URLS)
def test_dangerous_urls_are_rejected(url: str, code: str) -> None:
    with pytest.raises(TargetRejected) as caught:
        validate_url(url)
    assert caught.value.code == code, f"{url!r} was rejected as {caught.value.code}"


@pytest.mark.parametrize(
    ("url", "normalized"),
    [
        ("https://example.com", "https://example.com/"),
        ("http://example.com:80/a?b=1#frag", "http://example.com/a?b=1"),
        ("HTTPS://Sub.Example.CO.UK/Path", "https://sub.example.co.uk/Path"),
        ("https://bücher.example/", "https://xn--bcher-kva.example/"),
        ("https://example.com./", "https://example.com/"),
    ],
)
def test_ordinary_urls_are_accepted_and_normalized(url: str, normalized: str) -> None:
    assert validate_url(url).url == normalized


def test_normalization_drops_fragment_and_default_port() -> None:
    assert validate_url("https://Example.com:443/a#x").url == "https://example.com/a"
    assert validate_url("http://example.com:80").url == "http://example.com/"
    assert validate_url("https://example.com/").origin == "https://example.com"


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.169.254",
        "192.168.1.1",
        "172.20.0.5",
        "100.64.0.1",
        "0.0.0.0",  # noqa: S104 - an address under test, not a bind
        "255.255.255.255",
        "224.0.0.1",
        "::1",
        "::",
        "fe80::1",
        "fc00::1",
        "::ffff:10.0.0.1",
        "::ffff:169.254.169.254",
        "64:ff9b::a00:1",
        "2002:a00:1::",
        "not-an-ip",
        "",
    ],
)
def test_non_public_addresses_are_refused(address: str) -> None:
    assert is_public_address(address) is False


@pytest.mark.parametrize("address", ["93.184.216.34", "8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"])
def test_public_addresses_are_allowed(address: str) -> None:
    assert is_public_address(address) is True


def _resolver(*addresses: str):  # type: ignore[no-untyped-def]
    return lambda host, port: list(addresses)


def test_resolution_returns_the_checked_addresses() -> None:
    result = resolve_public_addresses("example.com", 443, resolver=_resolver("93.184.216.34"))
    assert result == ["93.184.216.34"]


def test_a_name_that_resolves_to_a_private_address_is_blocked() -> None:
    with pytest.raises(TargetRejected) as caught:
        resolve_public_addresses("evil.example", 443, resolver=_resolver("10.0.0.5"))
    assert caught.value.code == "blocked_address"


def test_mixed_public_and_private_answers_are_blocked() -> None:
    """DNS rebinding variant: one good record must not launder a bad one."""
    with pytest.raises(TargetRejected) as caught:
        resolve_public_addresses(
            "evil.example", 443, resolver=_resolver("93.184.216.34", "127.0.0.1")
        )
    assert caught.value.code == "blocked_address"


def test_ipv4_mapped_ipv6_answer_is_blocked() -> None:
    with pytest.raises(TargetRejected):
        resolve_public_addresses("evil.example", 443, resolver=_resolver("::ffff:127.0.0.1"))


def test_empty_and_failing_resolution_are_dns_failures() -> None:
    with pytest.raises(TargetRejected) as empty:
        resolve_public_addresses("x.example", 443, resolver=_resolver())
    assert empty.value.code == "dns_failed"

    def failing(host: str, port: int) -> list[str]:
        raise OSError("nxdomain")

    with pytest.raises(TargetRejected) as failed:
        resolve_public_addresses("x.example", 443, resolver=failing)
    assert failed.value.code == "dns_failed"


def test_the_test_exception_is_narrow() -> None:
    loopback_only = NetPolicy(allowed_networks=("127.0.0.0/8",), allowed_ports=(80, 443, 8080))
    assert validate_url("http://127.0.0.1:8080/", loopback_only).host == "127.0.0.1"
    assert resolve_public_addresses("127.0.0.1", 8080, loopback_only) == ["127.0.0.1"]
    with pytest.raises(TargetRejected):
        validate_url("http://127.0.0.1:8080/")  # the default policy still refuses
    # Allowing loopback must not open the metadata range or other private networks.
    for blocked in ("http://169.254.169.254/", "http://10.0.0.1/", "http://192.168.1.1/"):
        with pytest.raises(TargetRejected):
            validate_url(blocked, loopback_only)
    with pytest.raises(TargetRejected):
        resolve_public_addresses("x.example", 80, loopback_only, lambda h, p: ["169.254.169.254"])
