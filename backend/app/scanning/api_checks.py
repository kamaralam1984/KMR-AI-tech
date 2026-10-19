"""Read-only API contract checks, run against the project's verified host through the egress proxy.

A check says what a healthy response looks like (status, speed, optional JSON shape). Running it
sends ONE GET/HEAD, never follows redirects, reads at most 256 KiB, and records only the verdict
and reasons. Response bodies are not stored, and nothing about a response's content is repeated
back (schema violations name the path and the rule, never the value).
"""

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from app.scanning.net_policy import NetPolicy, TargetRejected, validate_url

MAX_BODY_BYTES = 256 * 1024
MAX_SCHEMA_BYTES = 10 * 1024
MAX_VIOLATIONS = 5
_HEADER_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,63}$")
_BAD_PATH = re.compile(r"[\s\x00-\x1f\x7f\\#]")
# Keywords refused in customer schemas: remote references would fetch URLs, and regular
# expressions supplied by a customer can be made to run for a very long time.
_FORBIDDEN_KEYWORDS = {"pattern", "patternProperties", "$dynamicRef", "$recursiveRef"}
_NETWORK_FAILURES = {"network_error", "timeout", "blocked", "proxy_unavailable"}


class InvalidCheck(ValueError):  # noqa: N818 - domain term
    """A check definition the user must fix. The message is safe to show."""


def validate_path(path: str) -> str:
    if not path.startswith("/") or path.startswith("//") or len(path) > 1024:
        raise InvalidCheck("The path must start with a single / and be at most 1024 characters.")
    if _BAD_PATH.search(path):
        raise InvalidCheck(
            "The path must not contain spaces, backslashes, # or control characters."
        )
    return path


def validate_header_name(name: str) -> str:
    if not _HEADER_NAME.match(name):
        raise InvalidCheck("The header name may contain only letters, digits and hyphens.")
    if name.lower() in {
        "host",
        "content-length",
        "connection",
        "transfer-encoding",
        "x-request-id",
    }:
        raise InvalidCheck("That header name is reserved.")
    return name


def validate_header_value(value: str) -> str:
    if not value or len(value) > 512 or re.search(r"[\x00-\x1f\x7f]", value):
        raise InvalidCheck("The credential must be 1 to 512 characters without line breaks.")
    return value


def _walk(node: Any) -> list[tuple[str, Any]]:
    found: list[tuple[str, Any]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            found.append((key, value))
            found.extend(_walk(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_walk(item))
    return found


def validate_schema(schema: dict[str, Any]) -> dict[str, Any]:
    if len(json.dumps(schema)) > MAX_SCHEMA_BYTES:
        raise InvalidCheck("The schema is too large (10 KB at most).")
    for key, value in _walk(schema):
        if key in _FORBIDDEN_KEYWORDS:
            raise InvalidCheck(f"The schema keyword '{key}' is not supported.")
        if key == "$ref" and not (isinstance(value, str) and value.startswith("#")):
            raise InvalidCheck("Only local references (starting with #) are supported in $ref.")
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise InvalidCheck("That is not a valid JSON Schema.") from exc
    return schema


@dataclass
class TransportResult:
    status: int | None = None
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    latency_ms: int = 0
    failure: str | None = None  # network_error | timeout | proxy_unavailable
    body_truncated: bool = False


class ApiTransport(Protocol):
    async def request(
        self, method: str, url: str, headers: dict[str, str], timeout: float
    ) -> TransportResult: ...


class ProxyTransport:
    """Sends the request through the egress proxy, which enforces the address policy."""

    def __init__(self, proxy_url: str) -> None:
        self._proxy_url = proxy_url

    async def request(
        self, method: str, url: str, headers: dict[str, str], timeout: float
    ) -> TransportResult:
        started = time.perf_counter()
        result = TransportResult()
        try:
            async with (
                httpx.AsyncClient(
                    proxy=self._proxy_url, timeout=timeout, follow_redirects=False
                ) as client,
                client.stream(method, url, headers=headers) as response,
            ):
                result.status = response.status_code
                result.headers = {k.lower(): v for k, v in response.headers.items()}
                async for chunk in response.aiter_bytes():
                    result.body += chunk
                    if len(result.body) > MAX_BODY_BYTES:
                        result.body = result.body[:MAX_BODY_BYTES]
                        result.body_truncated = True
                        break
        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.PoolTimeout, httpx.WriteTimeout):
            result.failure = "timeout"
        except (httpx.ProxyError, httpx.ConnectError):
            result.failure = "proxy_unavailable"
        except httpx.HTTPError:
            result.failure = "network_error"
        result.latency_ms = int((time.perf_counter() - started) * 1000)
        return result


@dataclass(frozen=True)
class CheckSpec:
    path: str
    method: str
    expected_status: int
    max_latency_ms: int
    response_schema: dict[str, Any] | None


@dataclass
class CheckResult:
    status: str  # passed | failed | error
    http_status: int | None
    latency_ms: int | None
    response_bytes: int | None
    reasons: list[dict[str, Any]]


def _schema_violations(schema: dict[str, Any], instance: Any) -> list[dict[str, str]]:
    validator = Draft202012Validator(schema)
    errors = sorted(
        validator.iter_errors(instance), key=lambda e: [str(p) for p in e.absolute_path]
    )
    out: list[dict[str, str]] = []
    for error in errors[:MAX_VIOLATIONS]:
        where = "$" + "".join(
            f"[{p}]" if isinstance(p, int) else f".{p}" for p in error.absolute_path
        )
        out.append({"path": where, "rule": str(error.validator)})  # never the offending value
    return out


async def run_check(
    spec: CheckSpec,
    *,
    origin: str,
    allowed_host: str,
    transport: ApiTransport,
    policy: NetPolicy,
    timeout: float,
    request_id: str,
    credentials: tuple[str, str] | None = None,
) -> CheckResult:
    """Runs one check. `origin` is the verified site origin; the path is appended to it."""
    url = f"{origin}{validate_path(spec.path)}"
    try:
        target = validate_url(url, policy)
    except TargetRejected as rejected:
        return CheckResult(
            "error", None, None, None, [{"code": "blocked", "detail": rejected.code}]
        )
    if target.host != allowed_host:  # the check can never leave the verified host
        return CheckResult("error", None, None, None, [{"code": "blocked", "detail": "other_host"}])

    headers = {
        "User-Agent": "KMRAI-ApiCheck/1.0",
        "Accept": "application/json, */*;q=0.5",
        "X-Request-ID": request_id,
    }
    if credentials:
        headers[credentials[0]] = credentials[1]
    response = await transport.request(spec.method, target.url, headers, timeout)

    if response.failure:
        return CheckResult("error", None, response.latency_ms, None, [{"code": response.failure}])
    blocked = response.headers.get("x-kmr-blocked")
    if blocked:
        return CheckResult(
            "error", None, response.latency_ms, None, [{"code": "blocked", "detail": blocked[:40]}]
        )

    reasons: list[dict[str, Any]] = []
    if response.status != spec.expected_status:
        if credentials and response.status in (401, 403):
            reasons.append({"code": "auth_rejected", "actual": response.status})
        else:
            reasons.append(
                {
                    "code": "status_mismatch",
                    "expected": spec.expected_status,
                    "actual": response.status,
                }
            )
    if response.latency_ms > spec.max_latency_ms:
        reasons.append(
            {"code": "too_slow", "max_ms": spec.max_latency_ms, "actual_ms": response.latency_ms}
        )
    if response.body_truncated:
        reasons.append({"code": "response_too_large", "max_bytes": MAX_BODY_BYTES})
    elif spec.response_schema is not None and spec.method == "GET":
        try:
            instance = json.loads(response.body)
        except (ValueError, UnicodeDecodeError):
            reasons.append({"code": "invalid_json"})
        else:
            violations = _schema_violations(spec.response_schema, instance)
            if violations:
                reasons.append({"code": "schema_mismatch", "violations": violations})
    status = (
        "error"
        if any(r["code"] in _NETWORK_FAILURES for r in reasons)
        else ("failed" if reasons else "passed")
    )
    return CheckResult(status, response.status, response.latency_ms, len(response.body), reasons)
