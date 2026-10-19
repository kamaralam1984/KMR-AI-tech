"""Redaction of visitor text, tokenization, and the prompt-injection filter.

Redaction is pattern based. It removes what can be recognized by shape (emails, phone-like and
long digit runs, card-like numbers, PAN, tokens, credentials in URLs). It cannot recognize names
or addresses, so callers must not describe it as complete.
"""

import re

from app.scanning.sanitize import sanitize_text

PERSONAL = "[PERSONAL]"
_DIGIT_RUN = re.compile(r"\+?\d(?:[\s-]?\d){7,}")
_PAN = re.compile(r"\b[A-Za-z]{5}\d{4}[A-Za-z]\b")

# Devanagari letters AND vowel signs: Python's \w drops the combining signs and would split words.
_TOKEN = re.compile(r"[\wऀ-ॣ०-ॿ]+", re.UNICODE)

STOPWORDS = frozenset(
    """
    a an the is are was were be been am do does did of to in on at for from by with and or not no
    what which who whom whose when where why how this that these those it its i me my we our you
    your can could should would will shall may might please tell about there here any some
    kya hai hain ho tha thi the ka ke ki ko se mein me par aur ya nahi nahin kaise kab kahan kaun
    kitna kitni kitne mujhe mera meri hum aap batao bataiye karo kare kar sakte sakta
    का के की को से में पर और या नहीं क्या है हैं था थी कैसे कब कहाँ कौन कितना मुझे मेरा मेरी हम आप
    """.split()
)

_INJECTION = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(ignore|disregard|forget|override)\b[^.\n]{0,40}\b(previous|prior|above|earlier|all|any|"
        r"your|the)\b[^.\n]{0,30}\b(instruction|prompt|rule|guideline|polic)",
        r"\b(reveal|show|print|repeat|output|leak)\b[^.\n]{0,30}\b(system|hidden|secret|your)\b"
        r"[^.\n]{0,20}\b(prompt|instruction|key|password)",
        r"\byou are now\b",
        r"\b(act|behave) as (if you|an?|the)\b",
        r"\bpretend (to be|you)\b",
        r"\bnew instructions?\b",
        r"\bsystem prompt\b",
        r"\b(developer|jailbreak|dan) mode\b",
        r"</?\s*(system|assistant|instructions?)\s*>",
        r"\bdo anything now\b",
        r"पिछले\s*(सभी\s*)?निर्देश",
        r"निर्देश[^\n.]{0,20}अनदेखा",
        r"\bpichle\s+(sab\s+)?(nirdesh|instructions?)\b",
    )
]


def redact_personal(text: str, limit: int = 2000) -> str:
    """Masks credentials, emails, long digit runs (phones, Aadhaar-like, cards) and PAN numbers."""
    cleaned = sanitize_text(text, limit=limit * 2)
    cleaned = _PAN.sub(PERSONAL, cleaned)
    cleaned = _DIGIT_RUN.sub(PERSONAL, cleaned)
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1] + "…"


def looks_like_injection(text: str) -> bool:
    """True when text tries to instruct whoever reads it. Such passages are never used."""
    return any(p.search(text) for p in _INJECTION)


def normalize_token(token: str) -> str:
    token = token.lower()
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("es") and token[-3] in "sxz":
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def tokenize(text: str) -> list[str]:
    return [normalize_token(t) for t in _TOKEN.findall(text)]


def content_terms(text: str) -> list[str]:
    """Distinct meaningful words of a question, in order."""
    seen: dict[str, None] = {}
    for token in tokenize(text):
        if token not in STOPWORDS and normalize_token(token) not in STOPWORDS and len(token) > 1:
            seen.setdefault(token, None)
    return list(seen)
