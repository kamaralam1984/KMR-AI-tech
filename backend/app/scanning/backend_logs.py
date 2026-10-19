"""Parses backend logs a customer pastes in. These are untrusted, customer-supplied evidence.

Only structure is kept (time, level, request id, status, path) plus a short sanitized message.
Anything that does not look like a request-bearing log line is counted as rejected and dropped.
"""

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlsplit

from app.scanning.sanitize import sanitize_text

MAX_CONTENT_CHARS = 1_000_000
MAX_LINES = 20_000
MAX_ENTRIES_PER_SCAN = 5_000

_REQUEST_ID_KEYS = (
    "request_id",
    "requestId",
    "req_id",
    "x_request_id",
    "x-request-id",
    "correlation_id",
    "correlationId",
    "trace_id",
    "traceId",
)
_STATUS_KEYS = ("status", "status_code", "statusCode", "http_status", "response_status")
_PATH_KEYS = ("path", "url", "uri", "route", "endpoint", "request_path")
_LEVEL_KEYS = ("level", "severity", "log_level", "levelname")
_MESSAGE_KEYS = ("message", "msg", "error", "err", "exception")
_TIME_KEYS = ("timestamp", "ts", "time", "@timestamp", "date", "datetime")

_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{4,100}$")
_TEXT_REQUEST_ID = re.compile(
    r"(?i)(?:x-request-id|request[_-]?id|req[_-]?id|trace[_-]?id|correlation[_-]?id)[\"']?\s*[=:]\s*[\"']?"
    r"([A-Za-z0-9._-]{4,100})"
)
_TEXT_REQUEST = re.compile(r"\b(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(/\S*)")
_TEXT_STATUS = re.compile(
    r"(?i)\bstatus(?:[_ -]?code)?[\"']?\s*[=:]\s*[\"']?(\d{3})\b|\"\s(\d{3})\s"
)
_TEXT_LEVEL = re.compile(r"\b(CRITICAL|FATAL|ERROR|WARN(?:ING)?|INFO|DEBUG)\b", re.IGNORECASE)
_TEXT_TIME = re.compile(
    r"\b(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)"
)


@dataclass(frozen=True)
class ParsedEntry:
    ts: datetime | None
    level: str | None
    request_id: str | None
    http_status: int | None
    path: str | None
    message: str | None


@dataclass
class ParseResult:
    entries: list[ParsedEntry]
    rejected: int


def _first(obj: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in obj and obj[key] not in (None, ""):
            return obj[key]
    return None


def _status(value: Any) -> int | None:
    try:
        status = int(value)
    except (TypeError, ValueError):
        return None
    return status if 100 <= status <= 599 else None


def _timestamp(value: Any) -> datetime | None:
    try:
        if isinstance(value, int | float):
            seconds = value / 1000 if value > 10_000_000_000 else value
            return datetime.fromtimestamp(seconds, UTC)
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00").replace(" ", "T", 1))
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _path(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    path = urlsplit(value).path or None
    return path[:300] if path else None


def _level(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    level = value.strip().lower()
    return {"warning": "warn", "fatal": "critical"}.get(level, level)[:16] or None


def _request_id(value: Any) -> str | None:
    text = str(value) if value is not None else ""
    return text if _VALID_REQUEST_ID.match(text) else None


def _from_json(obj: dict[str, Any]) -> ParsedEntry | None:
    request_id = _request_id(_first(obj, _REQUEST_ID_KEYS))
    path = _path(_first(obj, _PATH_KEYS))
    if request_id is None and path is None:
        return None
    message = _first(obj, _MESSAGE_KEYS)
    return ParsedEntry(
        ts=_timestamp(_first(obj, _TIME_KEYS)),
        level=_level(_first(obj, _LEVEL_KEYS)),
        request_id=request_id,
        http_status=_status(_first(obj, _STATUS_KEYS)),
        path=path,
        message=sanitize_text(str(message), 300) if message is not None else None,
    )


def _from_text(line: str) -> ParsedEntry | None:
    request = _TEXT_REQUEST.search(line)
    rid = _TEXT_REQUEST_ID.search(line)
    if rid is None and request is None:
        return None
    status = _TEXT_STATUS.search(line)
    level = _TEXT_LEVEL.search(line)
    when = _TEXT_TIME.search(line)
    return ParsedEntry(
        ts=_timestamp(when.group(1)) if when else None,
        level=_level(level.group(1)) if level else None,
        request_id=_request_id(rid.group(1)) if rid else None,
        http_status=_status(status.group(1) or status.group(2)) if status else None,
        path=_path(request.group(1)) if request else None,
        message=sanitize_text(line, 300),
    )


def parse_logs(content: str, fmt: Literal["jsonl", "text"]) -> ParseResult:
    entries: list[ParsedEntry] = []
    rejected = 0
    for number, line in enumerate(content.splitlines()):
        if number >= MAX_LINES:
            rejected += 1
            continue
        line = line.strip()
        if not line:
            continue
        entry: ParsedEntry | None
        if fmt == "jsonl":
            try:
                parsed = json.loads(line)
            except ValueError:
                rejected += 1
                continue
            entry = _from_json(parsed) if isinstance(parsed, dict) else None
        else:
            entry = _from_text(line)
        if entry is None:
            rejected += 1
        else:
            entries.append(entry)
    return ParseResult(entries, rejected)
