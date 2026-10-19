"""Form definitions, value checks, the review checklist, and action-scoped confirmations.

The assistant never submits anything. It tells the visitor what is missing or wrong, shows a
review of exactly what they entered, and can issue a short-lived confirmation that the visitor
(not the assistant) agreed to ONE named action on THIS exact data. The host site decides what to
do with it. Values are checked and discarded: nothing here stores a value.
"""

import base64
import hashlib
import hmac
import json
import re
import time
import unicodedata
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

SUPPORTED_CATEGORIES = ("exam_registration", "admission", "scholarship", "job_application")
EXCLUDED_NOTE = (
    "Supported: exam registration, admission, scholarship and job application forms. Not "
    "supported: payment pages, identity/government-login flows, CAPTCHA, OTP or password steps."
)
FIELD_TYPES = ("text", "email", "phone", "date", "number", "select", "declaration", "file")
FILE_MIME = ("image/jpeg", "image/png", "application/pdf")
CONFIRM_ACTIONS = ("declaration", "submission", "payment")
MAX_FIELDS = 60
CONFIRMATION_TTL_SECONDS = 600

# Data minimization: these are never collected through the assistant. The visitor keeps entering
# them on the official form themselves.
_FORBIDDEN = re.compile(
    r"pass(word|code)|\botp\b|one[\s_-]?time|\bcvv\b|\bcvc\b|card[\s_-]?number|captcha|aadha?a?r|"
    r"pan[\s_-]?(card|number)|bank[\s_-]?account|account[\s_-]?number|\bpin[\s_-]?code\b.*atm",
    re.IGNORECASE,
)
_KEY = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_EMAIL = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$")


class FormField(BaseModel):
    key: str
    label: str = Field(min_length=1, max_length=100)
    type: Literal["text", "email", "phone", "date", "number", "select", "declaration", "file"]
    required: bool = True
    help: str = Field(default="", max_length=300)
    min_length: int | None = Field(default=None, ge=0, le=1000)
    max_length: int | None = Field(default=None, ge=1, le=1000)
    min_value: float | None = None
    max_value: float | None = None
    min_date: date | None = None
    max_date: date | None = None
    options: list[str] = Field(default_factory=list, max_length=50)
    declaration_text: str = Field(default="", max_length=500)
    accept: list[str] = Field(default_factory=list)
    min_kb: int | None = Field(default=None, ge=0, le=20_000)
    max_kb: int | None = Field(default=None, ge=1, le=20_000)
    min_width: int | None = Field(default=None, ge=1, le=20_000)
    max_width: int | None = Field(default=None, ge=1, le=20_000)
    min_height: int | None = Field(default=None, ge=1, le=20_000)
    max_height: int | None = Field(default=None, ge=1, le=20_000)

    @field_validator("key")
    @classmethod
    def valid_key(cls, value: str) -> str:
        if not _KEY.match(value):
            raise ValueError("key must be lowercase letters, digits and underscores")
        return value

    @model_validator(mode="after")
    def consistent(self) -> "FormField":
        if _FORBIDDEN.search(f"{self.key} {self.label}"):
            raise ValueError(
                f"'{self.label}' is not collected through the assistant (passwords, OTP, card, "
                "CAPTCHA, Aadhaar/PAN and bank details stay on the official form)."
            )
        if self.type == "select" and not self.options:
            raise ValueError("a select field needs options")
        if self.type == "declaration":
            if not self.declaration_text.strip():
                raise ValueError("a declaration field needs its exact declaration_text")
            self.required = True
        if self.type == "file":
            if not self.accept:
                raise ValueError("a file field needs accept (allowed file types)")
            if any(m not in FILE_MIME for m in self.accept):
                raise ValueError(f"accept may contain only {', '.join(FILE_MIME)}")
        pairs: list[tuple[Any, Any]] = [
            (self.min_length, self.max_length),
            (self.min_value, self.max_value),
            (self.min_date, self.max_date),
            (self.min_kb, self.max_kb),
            (self.min_width, self.max_width),
            (self.min_height, self.max_height),
        ]
        for low, high in pairs:
            if low is not None and high is not None and low > high:
                raise ValueError("a minimum is larger than its maximum")
        return self


class FormDefinition(BaseModel):
    category: Literal["exam_registration", "admission", "scholarship", "job_application"]
    fields: list[FormField] = Field(min_length=1, max_length=MAX_FIELDS)
    payment_note: str = Field(default="", max_length=300)
    support_note: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def unique_keys(self) -> "FormDefinition":
        keys = [f.key for f in self.fields]
        if len(keys) != len(set(keys)):
            raise ValueError("field keys must be unique")
        return self


# ----------------------------------------------------------------------------- value checks

_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y")


def parse_date(raw: str) -> date | None:
    """Day-first for slashes and dashes (the usual Indian form convention); ISO also accepted."""
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw.strip(), fmt).date()  # noqa: DTZ007
        except ValueError:
            continue
    return None


def _clean(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value)).strip() if value is not None else ""


def normalize_phone(raw: str) -> str | None:
    digits = re.sub(r"[\s().-]", "", raw)
    digits = (
        re.sub(r"^(\+91|0091|91|0)", "", digits) if len(re.sub(r"\D", "", raw)) > 10 else digits
    )
    return digits if re.fullmatch(r"[6-9]\d{9}", digits) else None


def check_file(spec: FormField, meta: Any) -> tuple[str, str]:
    """Checks a file's METADATA (name, type, size, pixels). The file itself never reaches here."""
    if not isinstance(meta, dict):
        return "missing", f"Choose a file for '{spec.label}'."
    mime = _clean(meta.get("mime"))
    size = meta.get("size_bytes")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        return "invalid", "The file size could not be read. Choose the file again."
    if mime not in spec.accept:
        allowed = ", ".join(m.split("/")[1].upper() for m in spec.accept)
        return "invalid", f"This file type is not accepted. Use: {allowed}."
    kb = size / 1024
    if spec.max_kb is not None and kb > spec.max_kb:
        return "invalid", f"The file is {kb:.0f} KB; the maximum is {spec.max_kb} KB. Reduce it."
    if spec.min_kb is not None and kb < spec.min_kb:
        return "invalid", f"The file is {kb:.0f} KB; the minimum is {spec.min_kb} KB."
    if mime.startswith("image/"):
        w, h = meta.get("width"), meta.get("height")
        bounded = (spec.min_width, spec.max_width, spec.min_height, spec.max_height)
        if any(v is not None for v in bounded):
            if not isinstance(w, int) or not isinstance(h, int):
                return "invalid", "The image size in pixels could not be read."
            for value, low, high, name in (
                (w, spec.min_width, spec.max_width, "width"),
                (h, spec.min_height, spec.max_height, "height"),
            ):
                if low is not None and value < low:
                    return "invalid", f"The image {name} is {value}px; it must be at least {low}px."
                if high is not None and value > high:
                    return "invalid", f"The image {name} is {value}px; it must be at most {high}px."
    return "ok", "The file meets the stated requirements."


def check_field(spec: FormField, raw: Any) -> tuple[str, str, Any]:
    """(status, message, normalized value). Status: ok, missing, invalid or needs_confirmation."""
    if spec.type == "file":
        status, message = check_file(spec, raw)
        if status == "missing" and not spec.required:
            return "ok", "Optional and left empty.", None
        norm = (
            {
                "name": _clean(raw.get("name"))[:120],
                "mime": raw.get("mime"),
                "size_bytes": raw.get("size_bytes"),
            }
            if status == "ok" and isinstance(raw, dict)
            else None
        )
        return status, message, norm
    if spec.type == "declaration":
        # Never accepted as a plain value: the visitor confirms it in the confirm step.
        return (
            "needs_confirmation",
            "You must read and confirm this declaration yourself. The assistant cannot tick it.",
            None,
        )
    value = _clean(raw)
    if not value:
        if spec.required:
            return (
                "missing",
                f"'{spec.label}' is required. Please provide it; I will not guess it.",
                None,
            )
        return "ok", "Optional and left empty.", None
    if spec.type == "select":
        if value not in spec.options:
            return "invalid", f"Choose one of: {', '.join(spec.options[:10])}.", None
        return "ok", "Valid option.", value
    if spec.type == "email":
        if not _EMAIL.match(value) or len(value) > 254:
            return "invalid", "This does not look like an email address (name@example.com).", None
        return "ok", "Looks like a valid email address.", value.lower()
    if spec.type == "phone":
        phone = normalize_phone(value)
        if phone is None:
            return (
                "invalid",
                "Enter a 10-digit mobile number starting with 6-9 (+91 is optional).",
                None,
            )
        return "ok", "Valid mobile number format.", phone
    if spec.type == "number":
        try:
            number = float(value.replace(",", ""))
        except ValueError:
            return "invalid", "Enter a number using digits only.", None
        if spec.min_value is not None and number < spec.min_value:
            return "invalid", f"Must be at least {spec.min_value:g}.", None
        if spec.max_value is not None and number > spec.max_value:
            return "invalid", f"Must be at most {spec.max_value:g}.", None
        return "ok", "Valid number.", f"{number:g}"
    if spec.type == "date":
        parsed = parse_date(value)
        if parsed is None:
            return "invalid", "Use the date format DD/MM/YYYY (day first).", None
        if spec.min_date and parsed < spec.min_date:
            return "invalid", f"The date must be on or after {spec.min_date.isoformat()}.", None
        if spec.max_date and parsed > spec.max_date:
            return "invalid", f"The date must be on or before {spec.max_date.isoformat()}.", None
        return "ok", "Valid date (read as day/month/year).", parsed.isoformat()
    if spec.min_length is not None and len(value) < spec.min_length:
        return "invalid", f"Enter at least {spec.min_length} characters.", None
    if spec.max_length is not None and len(value) > spec.max_length:
        return "invalid", f"Enter at most {spec.max_length} characters.", None
    return "ok", "Looks fine.", value


def values_hash(normalized: dict[str, Any]) -> str:
    canonical = json.dumps(normalized, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def review(defn: FormDefinition, values: dict[str, Any]) -> dict[str, Any]:
    """Field-by-field results, a checklist, and a hash of the exact normalized values."""
    rows: list[dict[str, Any]] = []
    normalized: dict[str, Any] = {}
    unknown = sorted(k for k in values if k not in {f.key for f in defn.fields})
    for spec in defn.fields:
        status, message, norm = check_field(spec, values.get(spec.key))
        if norm is not None:
            normalized[spec.key] = norm
        rows.append(
            {
                "key": spec.key,
                "label": spec.label,
                "type": spec.type,
                "required": spec.required,
                "status": status,
                "message": message,
                "value": norm if spec.type != "declaration" else None,
                "declaration_text": spec.declaration_text if spec.type == "declaration" else None,
                "ask": f"What is your {spec.label.lower()}?" if status == "missing" else None,
            }
        )
    blocking = [r for r in rows if r["status"] in ("missing", "invalid")]
    pending = [r for r in rows if r["status"] == "needs_confirmation"]
    return {
        "fields": rows,
        "unknown_fields": unknown,
        "blocking": len(blocking),
        "pending_declarations": [r["key"] for r in pending],
        "ready_for_review": not blocking,
        "values_hash": values_hash(normalized),
        "checklist": [
            "Read every value above against your documents.",
            "Fix anything marked missing or invalid.",
            "Confirm each declaration yourself; the assistant cannot do it for you.",
            "Submission, declarations and any payment each need their own separate confirmation.",
        ],
        "submitted": False,
    }


# ----------------------------------------------------------------------------- confirmations


def _key(secret: str) -> bytes:
    return hmac.new(secret.encode(), b"kmr-assistant-confirmation-v1", hashlib.sha256).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make_confirmation(
    secret: str,
    *,
    install_id: str,
    form_id: str,
    action: str,
    data_hash: str,
    field_key: str | None = None,
    now: float | None = None,
) -> str:
    payload = {
        "i": install_id,
        "f": form_id,
        "a": action,
        "h": data_hash,
        "k": field_key,
        "exp": int((now if now is not None else time.time()) + CONFIRMATION_TTL_SECONDS),
    }
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"{_b64(body)}.{_b64(hmac.new(_key(secret), body, hashlib.sha256).digest())}"


def verify_confirmation(
    secret: str,
    token: str,
    *,
    install_id: str,
    form_id: str,
    action: str,
    data_hash: str,
    field_key: str | None = None,
    now: float | None = None,
) -> tuple[bool, str]:
    """True only if the token was issued for exactly this install, form, action and data."""
    try:
        body_b64, sig_b64 = token.split(".", 1)
        body, sig = _unb64(body_b64), _unb64(sig_b64)
        payload = json.loads(body)
    except (ValueError, TypeError):
        return False, "malformed"
    if not hmac.compare_digest(sig, hmac.new(_key(secret), body, hashlib.sha256).digest()):
        return False, "bad_signature"
    if payload.get("exp", 0) < (now if now is not None else time.time()):
        return False, "expired"
    expected = {"i": install_id, "f": form_id, "a": action, "h": data_hash, "k": field_key}
    if any(payload.get(k) != v for k, v in expected.items()):
        return False, "scope_mismatch"
    return True, "ok"
