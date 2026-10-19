"""Customer-supplied traffic: parsing an analytics export, and using it to weight impact.

The owner exports page views per path from their own analytics and uploads the CSV. KMR AI does not
measure, verify or enrich these numbers, and says so wherever they appear. Only page views per path
are read. Revenue, conversion and user-level columns are ignored on purpose: nothing in KMR AI can
turn this data into a money figure.
"""

import csv
import io
import re
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

MAX_ROWS = 5000
MAX_BYTES = 400_000
PATH_HEADERS = ("path", "page", "page path", "page path and screen class", "url", "page url")
VIEW_HEADERS = ("views", "pageviews", "page views", "screen page views", "sessions")
FACTOR_CAP = 3.0


class TrafficFormatError(ValueError):
    """The upload cannot be understood. The message is safe to show to the user."""


@dataclass(frozen=True)
class ParsedTraffic:
    rows: dict[str, int]
    rejected: int


def normalize_path(raw: str) -> str | None:
    value = raw.strip()
    if not value:
        return None
    if "://" in value:
        try:
            value = urlsplit(value).path or "/"
        except ValueError:
            return None
    value = value.split("?", 1)[0].split("#", 1)[0]
    value = unquote(value)
    if not value.startswith("/"):
        value = "/" + value
    value = re.sub(r"/{2,}", "/", value)
    if len(value) > 1:
        value = value.rstrip("/")
    return value if len(value) <= 300 and "\x00" not in value else None


def _int(raw: str) -> int | None:
    cleaned = raw.strip().replace(",", "").replace(" ", "")
    if not re.fullmatch(r"\d{1,12}", cleaned):
        return None
    return int(cleaned)


def parse_csv(text: str) -> ParsedTraffic:
    if len(text.encode()) > MAX_BYTES:
        raise TrafficFormatError("The file is too large (400 KB at most).")
    sample = text.lstrip("﻿")
    try:
        dialect = csv.Sniffer().sniff(sample[:2000], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(sample), dialect)
    header: list[str] | None = None
    path_i = view_i = -1
    rows: dict[str, int] = {}
    rejected = 0
    for line in reader:
        if not any(cell.strip() for cell in line):
            continue
        if header is None:
            header = [c.strip().lower() for c in line]
            path_i = next((header.index(h) for h in PATH_HEADERS if h in header), -1)
            view_i = next((header.index(h) for h in VIEW_HEADERS if h in header), -1)
            if path_i < 0 or view_i < 0:
                raise TrafficFormatError(
                    "The first row must name a path column (path, page, url) and a views column "
                    "(views, pageviews, sessions)."
                )
            continue
        if len(rows) + rejected >= MAX_ROWS:
            raise TrafficFormatError(f"Too many rows (at most {MAX_ROWS}).")
        if len(line) <= max(path_i, view_i):
            rejected += 1
            continue
        path, views = normalize_path(line[path_i]), _int(line[view_i])
        if path is None or views is None:
            rejected += 1
            continue
        rows[path] = rows.get(path, 0) + views
    if header is None or not rows:
        raise TrafficFormatError("No usable rows were found.")
    return ParsedTraffic(rows=rows, rejected=rejected)


@dataclass(frozen=True)
class TrafficIndex:
    views: dict[str, int]
    total: int
    period_start: str
    period_end: str
    source_label: str

    def share(self, path: str) -> tuple[int, float]:
        views = self.views.get(normalize_path(path) or "", 0)
        return views, (views / self.total) if self.total else 0.0

    @staticmethod
    def factor(share: float) -> float:
        """1.0 for a page with no traffic; up to 3.0 for a page with 20% or more of all views."""
        return min(FACTOR_CAP, 1.0 + share * 10)
