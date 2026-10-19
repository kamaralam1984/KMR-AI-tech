"""Extension tokens: creation, lookup and the identity a token acts as."""

import hashlib
import secrets
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.tenant import tenant_session

TOKEN_PREFIX = "kmr_xt_"  # noqa: S105 - a public prefix, not a secret
DESKTOP_PREFIX = "kmr_dt_"  # noqa: S105 - a public prefix, not a secret
PREFIXES = {"extension": TOKEN_PREFIX, "desktop": DESKTOP_PREFIX}


def new_token(kind: str = "extension") -> tuple[str, bytes, str]:
    """(plaintext shown once, sha256 digest stored, short prefix for display)."""
    prefix = PREFIXES[kind]
    raw = prefix + secrets.token_urlsafe(32)
    return raw, hash_token(raw), raw[: len(prefix) + 4]


def hash_token(raw: str) -> bytes:
    return hashlib.sha256(raw.encode()).digest()


@dataclass(frozen=True)
class ExtensionIdentity:
    token_id: uuid.UUID
    org_id: uuid.UUID
    user_id: uuid.UUID
    kind: str = "extension"


async def resolve_token(engine: AsyncEngine, raw: str) -> ExtensionIdentity | None:
    async with tenant_session(engine) as db:
        row = (
            await db.execute(text("SELECT * FROM extension_resolve(:h)"), {"h": hash_token(raw)})
        ).first()
    if row is None:
        return None
    return ExtensionIdentity(row.token_id, row.org_id, row.user_id, row.kind)
