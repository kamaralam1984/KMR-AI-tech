"""Proof that the person scanning a site controls it: a DNS TXT record or a file on the site.

Both checks are behind a small interface so tests can use a clearly labeled fake and never need
real DNS. The real implementation does DNS lookups only for the fixed record name derived from the
host, and fetches the file through the egress proxy, so a hostile site cannot use the check to
reach internal addresses.
"""

from typing import Protocol

import dns.asyncresolver
import dns.exception
import dns.resolver
import httpx

PROOF_PREFIX = "kmr-verification="
DNS_LABEL = "_kmr-verify"
FILE_PATH = "/.well-known/kmr-verification.txt"
MAX_PROOF_BYTES = 1024


class VerificationUnavailable(Exception):  # noqa: N818 - domain term
    """The check could not run (proxy down). It is not a failed proof and must not count as one."""


def proof_value(token: str) -> str:
    return f"{PROOF_PREFIX}{token}"


def dns_record_name(host: str) -> str:
    return f"{DNS_LABEL}.{host}"


def file_url(scheme: str, host: str) -> str:
    return f"{scheme}://{host}{FILE_PATH}"


class OwnershipVerifier(Protocol):
    async def dns_txt(self, name: str) -> list[str]:
        """All TXT strings published at `name` (empty if none)."""
        ...

    async def fetch_file(self, url: str) -> str | None:
        """Body of `url` (at most 1 KiB), or None if it is missing, redirected or not a 200."""
        ...


class NetworkOwnershipVerifier:
    def __init__(self, proxy_url: str, timeout: float = 8.0) -> None:
        self._proxy_url = proxy_url
        self._timeout = timeout

    async def dns_txt(self, name: str) -> list[str]:
        resolver = dns.asyncresolver.Resolver()
        resolver.lifetime = self._timeout
        try:
            answer = await resolver.resolve(name, "TXT")
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers):
            return []
        except dns.exception.Timeout:
            return []
        return [b"".join(record.strings).decode("utf-8", "replace") for record in answer]

    async def fetch_file(self, url: str) -> str | None:
        try:
            async with (
                httpx.AsyncClient(
                    proxy=self._proxy_url, timeout=self._timeout, follow_redirects=False
                ) as client,
                client.stream("GET", url) as response,
            ):
                if response.status_code == 403 and response.headers.get("x-kmr-blocked"):
                    return None  # the proxy refused the target (private address and so on)
                if response.status_code != 200:
                    return None
                body = b""
                async for chunk in response.aiter_bytes():
                    body += chunk
                    if len(body) > MAX_PROOF_BYTES:
                        return None
                return body.decode("utf-8", "replace")
        except (httpx.ProxyError, httpx.ConnectError) as exc:
            raise VerificationUnavailable from exc
        except httpx.HTTPError:
            return None
