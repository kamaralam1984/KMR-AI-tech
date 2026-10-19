"""Finds credentials in repository text.

Used twice: files that contain secrets are never sent to a model, and a proposed change that
introduces a secret is rejected. Matching is deliberately conservative (few false negatives for
well-known formats); it reports what kind of secret it saw, never the secret.
"""

import re

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    (
        "github_token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b"),
    ),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("stripe_key", re.compile(r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    (
        "credential_assignment",
        re.compile(
            r"(?i)\b[\w.-]*(?:secret|passw(?:or)?d|api[_-]?key|access[_-]?token|auth[_-]?token)"
            r"[\w.-]*[\"']?\s*[:=]\s*[\"']([A-Za-z0-9_\-+/=.]{16,})[\"']"
        ),
    ),
)

_PLACEHOLDER = re.compile(r"(?i)(change-?me|example|placeholder|your[_-]|xxxx|<[^>]+>|\*{4,})")


def find_secrets(text: str) -> list[str]:
    """Kinds of secret found in `text` (each kind once, in a stable order)."""
    found: list[str] = []
    for kind, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            if kind == "credential_assignment" and _PLACEHOLDER.search(match.group(1)):
                continue
            found.append(kind)
            break
    return found
