"""Local redaction of text read from other apps, before anything leaves this computer.

A port of extension/src/redact.js (the browser extension), so the same patterns and the same
labeled test sets apply to both. It is pattern-based: it catches emails, phone and card numbers, ID
numbers, tokens, labelled personal details, many addresses and many names that follow a cue. It
cannot catch every name or address written in ordinary sentences, and the documentation says so.
The server checks again and rejects what still looks personal.
"""

import re
from collections.abc import Callable
from urllib.parse import quote, urlsplit

VERSION = "1"
MAX_TEXT = 300
Counts = dict[str, int]
A = re.ASCII
I = re.IGNORECASE | re.ASCII  # noqa: E741 - mirrors the JS `i` flag

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_URL_IN_TEXT = re.compile(r"https?://[^\s\"'<>)\]]+")


def bump(counts: Counts | None, kind: str) -> None:
    if counts is not None:
        counts[kind] = counts.get(kind, 0) + 1


def _rule(
    kind: str, pattern: str, template: str, flags: int = A, guard: str | None = None
) -> tuple[str, re.Pattern[str], str, re.Pattern[str] | None]:
    return kind, re.compile(pattern, flags), template, re.compile(guard, I) if guard else None


# (kind, pattern, template with \1 \2 groups, optional cheap guard). Same order as the JS.
PATTERNS = [
    _rule("token", r"\beyJ[\w-]{5,}\.[\w-]{5,}\.[\w-]*", "[TOKEN]"),
    _rule("token", r"\b(?:bearer|basic)\s+[\w.~+/=-]{8,}", "[TOKEN]", I),
    _rule(
        "token",
        r"\b([a-z_]*(?:token|secret|passw(?:or)?d|pwd|api[_-]?key|auth|session|otp|cvv|cvc)[a-z_]*)"
        r"\s*[=:]\s*[\"']?[^\s\"',;&]{3,}",
        r"\1=[TOKEN]",
        I,
    ),
    _rule("email", r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[EMAIL]"),
    _rule("card", r"\b(?:\d[ -]?){13,19}\b", "[CARD]"),
    _rule("id", r"\b\d{4}[ -]?\d{4}[ -]?\d{4}\b", "[ID]"),
    _rule("id", r"\b[A-Z]{5}\d{4}[A-Z]\b", "[ID]"),
    _rule("id", r"\b[A-Z]{4}0[A-Z0-9]{6}\b", "[ID]"),
    _rule(
        "personal",
        r"\b(full name|first name|last name|name|dob|date of birth|address|father|mother|mobile|"
        r"phone|email|aadhaar|aadhar)\s*[:=-]\s*[^\n,;|]{2,60}",
        r"\1: [PERSONAL]",
        I,
    ),
    _rule("name", r"\b(?:Mr|Mrs|Ms|Miss|Dr|Shri|Smt|Sri)\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?", "[NAME]"),
    _rule("code", r"\b(otp|code|pin|cvv|cvc|passcode)\b([^\n]{0,20}?)\b\d{4,8}\b", r"\1\2[CODE]", I),
    _rule(
        "email",
        r"\b[\w]+(?:\s*(?:\(dot\)|\[dot\]|\sdot\s)\s*[\w]+)*\s*(?:\(at\)|\[at\]|\sat\s)\s*[\w]+"
        r"(?:\s*(?:\(dot\)|\[dot\]|\sdot\s)\s*[\w]+)+",
        "[EMAIL]",
        I,
        r"(?:\(at\)|\[at\]|\sat\s)[\s\S]{0,60}(?:\(dot\)|\[dot\]|\sdot\s)",
    ),
    _rule("id", r"\b\d{3}-\d{2}-\d{4}\b", "[ID]"),
    _rule("id", r"\b[A-Z]{2}\d{1,2}[A-Z]{1,3}\d{4}\b", "[ID]"),
    _rule("id", r"\b[A-Z]\d{7}\b", "[ID]"),
    _rule("ip", r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "[IP]"),
    _rule(
        "dob",
        r"\b(born|birth(?:day)?|birth date|dob|d\.o\.b\.?)\b(?:\s+(?:on|is|date))*[\s:=-]*"
        r"(?:\d{1,4}[/.-]\d{1,2}[/.-]\d{2,4}|\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]{3,9}\s+\d{4}|"
        r"[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})",
        r"\1: [DOB]",
        I,
    ),
    _rule(
        "dob",
        r"\bdate of birth\b(?:\s+(?:is|on))?[\s:=-]*(?:\d{1,4}[/.-]\d{1,2}[/.-]\d{2,4}|"
        r"\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})",
        "date of birth: [DOB]",
        I,
    ),
    _rule(
        "address",
        r"\b(?:flat|house|plot|door|h\.?\s?no\.?)\s*(?:no\.?\s*)?#?\d+[A-Za-z]?\b[^.\n]{0,60}",
        "[ADDRESS]",
        I,
    ),
    _rule(
        "address",
        r"\b\d{1,4}[A-Za-z]?,?\s+[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,2}\s+"
        r"(?:Street|St|Road|Rd|Lane|Nagar|Marg|Avenue|Ave|Colony|Layout|Sector)\b(?:\s+[A-Z][a-z]+){0,2}",
        "[ADDRESS]",
    ),
    _rule(
        "pin",
        r"\b(pin ?code|pin|zip(?: code)?|postal code|post ?code)\b\D{0,12}\d{4,6}(?:-\d{4})?",
        r"\1 [PIN]",
        I,
    ),
    _rule("pin", r"(?<![\d.,])\d{6}(?![\d.,])", "[PIN]"),
    _rule(
        "user",
        r"\b(signed in as|logged in as|log(?:ged)? in as|username|user name|user id|userid|login id|"
        r"handle)\s*[:=-]?\s*@?[\w.]{3,40}",
        r"\1: [USER]",
        I,
    ),
    _rule("user", r"(?<![\w@])@[A-Za-z0-9_]{3,30}\b", "@[USER]"),
    _rule("token", r"\bAKIA[0-9A-Z]{16}\b", "[TOKEN]"),
    _rule("token", r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b", "[TOKEN]"),
    _rule("token", r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b", "[TOKEN]"),
    _rule("token", r"\bsk-[A-Za-z0-9_-]{20,}\b", "[TOKEN]"),
    _rule("token", r"\bAIza[0-9A-Za-z_-]{35}\b", "[TOKEN]"),
    _rule("code", r"\b(cvv|cvc|cid)\b\D{0,10}\d{3,4}\b", r"\1 [CODE]", I),
    _rule("email", r"[\w+-]+(?:\s*\.\s*[\w-]+)*\s+@\s+[\w-]+(?:\s*\.\s*[\w-]+)+", "[EMAIL]", A, r"\s@\s"),
    _rule("id", r"\b[A-Za-z0-9._-]{2,}@[a-z]{3,}\b(?![.\w])", "[ID]"),
    _rule("id", r"\b[A-Z]{3}\d{7}\b", "[ID]"),
    _rule("id", r"\b\d{2}[A-Z]{5}\d{4}[A-Z][0-9A-Z]Z[0-9A-Z]\b", "[ID]"),
    _rule(
        "ip",
        r"(?<![\w:])(?=[0-9A-Fa-f:]*(?:[A-Fa-f]|::))(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![\w:])",
        "[IP]",
    ),
    _rule("address", r"\b\d{1,4}\s*/\s*[A-Za-z0-9]+,\s*[^.\n]{3,60}", "[ADDRESS]"),
    _rule("address", r"\b\d{1,2}(?:st|nd|rd|th)\s+floor\b[^.\n]{0,50}", "[ADDRESS]", I),
    _rule(
        "address",
        r"\b(?:[Vv]illage|[Vv]ill\.?|[Pp]ost|[Dd]istrict|[Tt]ehsil|[Tt]aluka|[Mm]andal)\s+[A-Z][a-z]+",
        "[ADDRESS]",
    ),
    _rule(
        "address",
        r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\s+"
        r"(?:Apartments?|Heights|Towers?|Residency|Society|Enclave|Complex|Park|Villas?|Gardens?)\b",
        "[ADDRESS]",
    ),
    _rule("token", r"\b(?=[A-Za-z0-9_+/=-]*\d)[A-Za-z0-9_+/=-]{32,}\b", "[TOKEN]"),
]

_PHONE = re.compile(r"(?<![\w.])\+?\d[\d\s().-]{8,}\d(?![\w])", A)
_DATE_LIKE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_DIGIT_WORDS = "zero|oh|one|two|three|four|five|six|seven|eight|nine"
_SPELLED = re.compile(rf"\b(?:(?:{_DIGIT_WORDS})(?:[\s,-]+|\b)){{4,}}", I)

_NOT_NAMES = frozenset(
    (
        "support team us service services store page home cart order orders account settings help sale "
        "terms policy privacy conditions products product contact customer care desk admin administrator "
        "sorry happy great sure glad ready afraid fine good well welcome back all today tomorrow yesterday "
        "there everyone again back now soon here this that the our your my "
        "free shipping delivery payment checkout login signup search menu news blog about faq error warning "
        "success cancel save submit continue next previous close open view more less new old "
        "monday tuesday wednesday thursday friday saturday sunday january february march april may june july "
        "august september october november december"
    ).split(" ")
)
_STRONG_CUES = (
    "hello|hi|hey|dear|welcome back|thanks|thank you|regards|best regards|sincerely|cheers|my name is|"
    "name is|mera naam|mera naam hai|contact person|beneficiary|nominee|guardian|father's name|"
    "mother's name|spouse|invoice for|bill to|billed to|ship to|deliver to|attn|attention|student|"
    "applicant|candidate|patient|passenger|tenant|employee|account holder"
)
_WEAK_CUES = (
    "from|to|by|cc|ask|ask for|tell|told|call|called|meet|met|contact|customer|member|guest|owner|"
    "holder|i am|this is|name of|application of|booking for|ticket for|raised by|signed by|submitted by|"
    "assigned to|welcome|sent to|send to|for"
)


def _flexible(word: str) -> str:
    return "".join(
        f"[{c.upper()}{c.lower()}]" if re.match(r"[a-z]", c, re.IGNORECASE) else re.escape(c) for c in word
    )


def _cue_group(words: str) -> str:
    return "(?:" + "|".join(_flexible(w) for w in words.split("|")) + ")"


_NAME1 = r"[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{1,}){0,2}"
_NAME2 = r"[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{1,}){1,2}"
_SEP = r"(\s*[,:-]?\s+)"
_STRONG_RE = re.compile(rf"\b({_cue_group(_STRONG_CUES)}){_SEP}({_NAME1})(?![a-z])", A)
_WEAK_RE = re.compile(rf"\b({_cue_group(_WEAK_CUES)}){_SEP}({_NAME2})(?![a-z])", A)
_BEFORE_VERB = re.compile(
    r"\b([A-Z][a-z]{2,}\s+[A-Z][a-z]{2,})(\s+(?:called|said|asked|wrote|emailed|replied|requested|"
    r"submitted|applied|booked|paid|signed|logged|complained|confirmed|phoned|visited|scored|passed|"
    r"failed|enrolled|admitted|attended|joined|reported|reached)\b)",
    A,
)
_HINGLISH = re.compile(r"\b([A-Z][a-z]{2,})(\s+(?:ne|ka|ki|ko)\s+[a-z]+)", A)


def _mask_names(text: str, counts: Counts | None) -> str:
    def ok(name: str) -> bool:
        return not any(w.lower() in _NOT_NAMES for w in name.split())

    def swap(m: re.Match[str]) -> str:
        cue, sep, name = m.group(1), m.group(2), m.group(3)
        if not ok(name):
            return m.group(0)
        bump(counts, "name")
        return f"{cue}{sep}[NAME]"

    def lead(m: re.Match[str]) -> str:
        if not ok(m.group(1)):
            return m.group(0)
        bump(counts, "name")
        return f"[NAME]{m.group(2)}"

    text = _WEAK_RE.sub(swap, _STRONG_RE.sub(swap, text))
    text = _HINGLISH.sub(lead, text)
    return _BEFORE_VERB.sub(lead, text)


def redact_text(value: object, counts: Counts | None = None, limit: int = MAX_TEXT) -> str:
    # Cap the work before doing it: a huge text node must not make the app slow.
    text = _CONTROL.sub(" ", str("" if value is None else value)[: max(limit * 4, 2000)])
    text = _URL_IN_TEXT.sub(lambda m: redact_url(m.group(0), counts), text)
    for kind, pattern, template, guard in PATTERNS:
        if guard is not None and not guard.search(text):
            continue

        def fill(m: re.Match[str], kind: str = kind, template: str = template) -> str:
            bump(counts, kind)
            return m.expand(template)

        text = pattern.sub(fill, text)

    def phone(m: re.Match[str]) -> str:
        run = m.group(0)
        if sum(c.isdigit() for c in run) >= 10 and not _DATE_LIKE.match(run):
            bump(counts, "phone")
            return "[PHONE]"
        return run

    text = _PHONE.sub(phone, text)

    def spelled(_m: re.Match[str]) -> str:
        bump(counts, "spelled")
        return "[SPELLED-NUMBER] "

    text = _SPELLED.sub(spelled, text)
    text = _mask_names(text, counts)
    return re.sub(r"\s+", " ", text, flags=A).strip()[:limit]


_USER_SEGMENT = re.compile(r"^(u|user|users|profile|profiles|member|members|~[\w.-]*)$", I)


def redact_url(value: object, counts: Counts | None = None) -> str:
    """Drops credentials and fragment, removes every query value, masks long numeric path parts."""
    try:
        parts = urlsplit(str(value))
        _ = parts.port  # raises on an invalid port
    except ValueError:
        return "[URL]"
    if parts.scheme not in ("http", "https"):
        return f"{parts.scheme}:" if parts.scheme else "[URL]"
    if not parts.hostname:
        return "[URL]"
    if parts.username or parts.password:
        bump(counts, "token")
    raw = parts.path.split("/")
    out: list[str] = []
    for i, part in enumerate(raw):
        if i > 1 and part and _USER_SEGMENT.match(raw[i - 1]):
            bump(counts, "user")
            out.append(":user")
        elif part.startswith("@") and len(part) > 1:
            bump(counts, "user")
            out.append("@user")
        elif (
            re.fullmatch(r"\d{5,}", part, A)
            or re.fullmatch(r"[0-9a-f]{16,}", part, I)
            or re.fullmatch(r"[\w-]{24,}", part, A)
        ):
            bump(counts, "id")
            out.append(":id")
        elif "@" in part:
            bump(counts, "email")
            out.append(":email")
        else:
            out.append(part)
    keys = [k for k, _v in (p.partition("=")[::2] for p in parts.query.split("&") if p)]
    if keys:
        bump(counts, "url_value")
    query = "?" + "&".join(f"{quote(k, safe=chr(45) + '_.!~*()')}=[V]" for k in keys) if keys else ""
    host = parts.hostname + (f":{parts.port}" if parts.port else "")
    return f"{parts.scheme}://{host}{'/'.join(out)}{query}"[:500]


_ = Callable  # (kept for type-checkers that follow re-exports)
