"""What the server refuses to accept from the extension, whatever the extension claims.

The extension redacts locally before anything is sent, and refuses to read sensitive pages. This
module is the second lock: if a payload still looks like it carries personal data or secrets, or
comes from a page that looks sensitive, the whole payload is rejected and nothing is stored. Only
field paths and reasons are reported back, never the offending values.

The sensitive-site lists must match extension/src/blocklist.js (a test compares them).
"""

import re
from typing import Any
from urllib.parse import parse_qsl, urlsplit

BLOCKED_HOSTS = (
    "paypal.com",
    "stripe.com",
    "razorpay.com",
    "paytm.com",
    "phonepe.com",
    "pay.google.com",
    "mail.google.com",
    "outlook.live.com",
    "outlook.office.com",
    "outlook.office365.com",
    "mail.yahoo.com",
    "mail.proton.me",
    "accounts.google.com",
    "login.microsoftonline.com",
    "login.live.com",
    "appleid.apple.com",
    "hdfcbank.com",
    "onlinesbi.sbi",
    "sbi.co.in",
    "icicibank.com",
    "axisbank.com",
    "kotak.com",
)
BLOCKED_HOST_PATTERNS = (r"bank", r"\.gov(\.[a-z]{2})?$", r"\.nic\.in$", r"\.mil$")
SENSITIVE_PATH = (
    r"(^|/)(log-?in|sign-?in|signin|sso|auth|oauth|otp|verify|2fa|mfa|password|passwd|reset|"
    r"forgot|pay|payment|payments|checkout|billing|card|wallet|netbanking|banking)"
    r"(/|$|[-_.])"
)
_PATH_RE = re.compile(SENSITIVE_PATH, re.IGNORECASE)
_HOST_RES = [re.compile(p, re.IGNORECASE) for p in BLOCKED_HOST_PATTERNS]


def sensitive_reason(url: str) -> str | None:
    """Why this page must not be read (or None). Looks only at the address."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "unreadable address"
    if parts.scheme not in ("http", "https"):
        return "not a web page"
    host = (parts.hostname or "").lower()
    if any(host == h or host.endswith("." + h) for h in BLOCKED_HOSTS):
        return "site is on the default block list (banking, mail, payment or sign-in provider)"
    if any(r.search(host) for r in _HOST_RES):
        return "site looks like a bank or government service"
    if _PATH_RE.search(parts.path):
        return "the address looks like a sign-in, verification, payment or checkout page"
    return None


# ------------------------------------------------------------------------- personal data scan

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*")
_BEARER = re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")
_SECRET_KV = re.compile(
    r"(?i)\b(?:[a-z_]*(?:token|secret|passw(?:or)?d|pwd|api[_-]?key|otp|cvv|cvc)[a-z_]*)"
    r"\s*[=:]\s*[\"']?(?!\[)[^\s\"',;&]{4,}"
)
_LONG_SECRET = re.compile(r"\b(?=[A-Za-z0-9_+/=-]*\d)[A-Za-z0-9_+/=-]{32,}\b")
_PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
_DIGIT_RUN = re.compile(r"(?<![\w.])\+?\d[\d\s().-]{8,}\d(?![\w])")
_LABELLED = re.compile(
    r"(?i)\b(?:full name|first name|last name|name|dob|date of birth|address|father|mother|"
    r"mobile|phone|email|aadhaar|aadhar)\s*[:=]\s*(?!\[)[^\s\[]"
)
_DATE_LIKE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_URL_KEYS = {"url", "page_url"}
_SKIP_KEYS = {"selector", "selectors"}


def _digits(text: str) -> int:
    return sum(c.isdigit() for c in text)


def _text_violations(value: str) -> list[str]:
    found = []
    if _EMAIL.search(value):
        found.append("email address")
    if _JWT.search(value) or _BEARER.search(value):
        found.append("token")
    if _SECRET_KV.search(value) or _LONG_SECRET.search(value):
        found.append("secret-looking value")
    if _PAN.search(value):
        found.append("ID number")
    if _LABELLED.search(value):
        found.append("labelled personal detail")
    for match in _DIGIT_RUN.finditer(value):
        run = match.group(0)
        if _digits(run) >= 10 and not _DATE_LIKE.match(run):
            found.append("long number (phone, card or ID)")
            break
    return found


def _url_violations(value: str) -> list[str]:
    found = []
    try:
        parts = urlsplit(value)
    except ValueError:
        return ["unreadable address"]
    if parts.username or parts.password:
        found.append("credentials in address")
    if _EMAIL.search(value) or _JWT.search(value):
        found.append("personal data in address")
    if any(v not in ("", "[V]") for _, v in parse_qsl(parts.query, keep_blank_values=True)):
        found.append("query value not removed")
    if parts.fragment:
        found.append("fragment not removed")
    return found


def find_violations(value: Any, path: str = "$") -> list[tuple[str, str]]:
    """(field path, reason) for every string that looks like unredacted personal data."""
    out: list[tuple[str, str]] = []
    if isinstance(value, str):
        out += [(path, r) for r in _text_violations(value)]
    elif isinstance(value, dict):
        for key, item in value.items():
            if key in _SKIP_KEYS:
                continue
            child = f"{path}.{key}"
            if key in _URL_KEYS and isinstance(item, str):
                out += [(child, r) for r in _url_violations(item)]
            else:
                out += find_violations(item, child)
    elif isinstance(value, list):
        for i, item in enumerate(value):
            out += find_violations(item, f"{path}[{i}]")
    return out
