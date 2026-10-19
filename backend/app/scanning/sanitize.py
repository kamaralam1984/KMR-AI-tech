"""Everything captured from a scanned page passes through here before it is stored or shown.

Pages are untrusted and often contain credentials in URLs, console output and error messages.
The rule is to keep what helps debugging (host, path, status, error type, a trimmed message) and
to mask anything that looks like a secret or personal data. Cookies, headers and response bodies
are never captured at all, so they never reach this module.
"""

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

MAX_URL = 500
MAX_TEXT = 500
REDACTED = "[REDACTED]"

_SENSITIVE_NAME = re.compile(
    r"(token|secret|passw|pwd|auth|session|sid|key|code|jwt|signature|sig|otp|credential|cookie|"
    r"bearer|email|phone|ssn)",
    re.IGNORECASE,
)
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*")
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")
# The credential after the scheme word is masked too ("Authorization: Bearer <token>"), and a
# whole Cookie line goes, because it can hold several secrets separated by semicolons.
_AUTH_HEADER = re.compile(
    r"(?i)\b(proxy-authorization|authorization|x-api-key)\s*[:=]\s*(?:(?:bearer|basic|token)\s+)?[^\s,;]+"
)
_COOKIE_LINE = re.compile(r"(?i)\b(set-cookie|cookie)\s*[:=][^\n]*")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_LONG_SECRET = re.compile(r"\b(?=[A-Za-z0-9_+/=-]*\d)[A-Za-z0-9_+/=-]{32,}\b")
_CARD_LIKE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_KEY_VALUE = re.compile(
    r"(?i)\b([A-Za-z_]*(?:token|secret|passw(?:or)?d|pwd|api[_-]?key|auth|session)[A-Za-z_]*)"
    r"\s*[=:]\s*[\"']?([^\s\"',;&]{4,})"
)
_URL_IN_TEXT = re.compile(r"https?://[^\s\"'<>)\]]+")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _looks_secret(value: str) -> bool:
    if len(value) >= 24 and re.fullmatch(r"[A-Za-z0-9_\-+/=.]+", value) and re.search(r"\d", value):
        return True
    return bool(_JWT.search(value))


def sanitize_url(url: str) -> str:
    """Drops credentials and fragments and masks query values that look like secrets."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return REDACTED
    if parts.scheme not in ("http", "https"):
        # data:, blob:, about: and friends can embed content; keep only the scheme.
        return f"{parts.scheme}:" if parts.scheme else REDACTED
    host = parts.hostname or ""
    netloc = f"[{host}]" if ":" in host else host
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    query = urlencode(
        [
            (name, REDACTED if _SENSITIVE_NAME.search(name) or _looks_secret(value) else value)
            for name, value in parse_qsl(parts.query, keep_blank_values=True)
        ]
    )
    cleaned = urlunsplit((parts.scheme, netloc, parts.path, query, ""))
    return cleaned[:MAX_URL]


def sanitize_text(text: str, limit: int = MAX_TEXT) -> str:
    """Masks credentials, tokens, emails and card-like numbers in free text, then trims it."""
    text = _CONTROL.sub(" ", text)
    text = _URL_IN_TEXT.sub(lambda m: sanitize_url(m.group(0)), text)
    text = _COOKIE_LINE.sub(lambda m: f"{m.group(1)}: {REDACTED}", text)
    text = _AUTH_HEADER.sub(lambda m: f"{m.group(1)}: {REDACTED}", text)
    text = _BEARER.sub(lambda m: f"{m.group(1)} {REDACTED}", text)
    text = _JWT.sub(REDACTED, text)
    text = _KEY_VALUE.sub(lambda m: f"{m.group(1)}={REDACTED}", text)
    text = _EMAIL.sub("[EMAIL]", text)
    text = _CARD_LIKE.sub("[NUMBER]", text)
    text = _LONG_SECRET.sub(REDACTED, text)
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"
