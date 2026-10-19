"""Database helpers shared by the owner and visitor routes."""

import hashlib
import hmac
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.assistant.privacy import looks_like_injection
from app.assistant.retrieval import Candidate, chunk_text
from app.db.models import KbChunk, KbPage
from app.db.tenant import tenant_session

KEY_PREFIX = "kmr_ik_"
MAX_PAGES_PER_INSTALL = 200
ORIGIN = re.compile(
    r"^(https://[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+(:\d{1,5})?"
    r"|http://(localhost|127\.0\.0\.1)(:\d{1,5})?)$"
)


def new_key() -> tuple[str, bytes, str]:
    """(plaintext shown once, sha256 digest stored, short prefix for display)."""
    raw = KEY_PREFIX + secrets.token_urlsafe(32)
    return raw, hash_key(raw), raw[: len(KEY_PREFIX) + 4]


def hash_key(raw: str) -> bytes:
    return hashlib.sha256(raw.encode()).digest()


def valid_origin(origin: str) -> bool:
    """Exact origins only: https hosts, or http on localhost for development. No wildcards."""
    return bool(ORIGIN.match(origin)) and len(origin) <= 200


@dataclass(frozen=True)
class Visitor:
    install_id: uuid.UUID
    org_id: uuid.UUID
    project_id: uuid.UUID | None
    name: str
    modules: tuple[str, ...]
    consent_text: str
    retention_days: int
    allowed_origins: tuple[str, ...]
    consent_required: bool = True


async def resolve_key(engine: AsyncEngine, raw_key: str) -> tuple[Visitor, bool] | None:
    """Looks a key up through the narrow SECURITY DEFINER function. Returns (visitor, enabled)."""
    async with tenant_session(engine) as db:
        row = (
            await db.execute(text("SELECT * FROM assistant_resolve(:h)"), {"h": hash_key(raw_key)})
        ).first()
    if row is None:
        return None
    visitor = Visitor(
        install_id=row.install_id,
        org_id=row.org_id,
        project_id=row.project_id,
        name=row.name,
        modules=tuple(row.modules),
        consent_text=row.consent_text,
        retention_days=row.retention_days,
        allowed_origins=tuple(row.allowed_origins),
        consent_required=bool(row.consent_required),
    )
    return visitor, bool(row.enabled)


def sign(secret: str, domain: str, *parts: str) -> str:
    key = hmac.new(secret.encode(), f"kmr-assistant-{domain}-v1".encode(), hashlib.sha256).digest()
    return hmac.new(key, "\x1f".join(parts).encode(), hashlib.sha256).hexdigest()


class _TextExtractor(HTMLParser):
    """Visible text of an HTML page; scripts, styles and hidden/template content are dropped."""

    _SKIP = {"script", "style", "noscript", "template", "svg", "iframe", "head"}
    _VOID = {"br", "img", "hr", "input", "meta", "link", "area", "base", "col", "embed", "source"}
    _BLOCK = {
        "p",
        "div",
        "li",
        "br",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "tr",
        "section",
        "article",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False
        self._stack: list[tuple[str, bool]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
        hidden = any(k == "hidden" or (k == "aria-hidden" and v == "true") for k, v in attrs)
        skip = tag in self._SKIP or hidden
        if tag not in self._VOID:
            self._stack.append((tag, skip))
            self._skip += int(skip)
        if tag in self._BLOCK:
            self.parts.append("\n" if tag == "br" else "\n\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i][0] == tag:
                self._skip -= sum(int(skipped) for _, skipped in self._stack[i:])
                del self._stack[i:]
                break

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> tuple[str, str]:
    parser = _TextExtractor()
    parser.feed(html[:500_000])
    body = re.sub(r"[ \t]+", " ", "".join(parser.parts))
    body = re.sub(r"\n\s*\n\s*", "\n\n", body).strip()
    return parser.title.strip()[:200], body


async def store_page(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    install_id: uuid.UUID,
    user_id: uuid.UUID,
    kind: str,
    title: str,
    url: str | None,
    content: str,
    source: str,
) -> KbPage:
    """Creates or replaces a page and its searchable chunks. Chunks that try to instruct the
    assistant are kept but flagged, and never used to answer."""
    existing = None
    if url is not None:
        existing = await db.scalar(
            select(KbPage).where(
                KbPage.install_id == install_id, KbPage.kind == kind, KbPage.url == url
            )
        )
    chunks = chunk_text(content)
    digest = hashlib.sha256(content.encode()).hexdigest()
    if existing is not None:
        page = existing
        page.title, page.content, page.content_sha256 = title, content, digest
        page.source, page.updated_at = source, datetime.now(UTC)
        await db.execute(delete(KbChunk).where(KbChunk.page_id == page.id))
    else:
        count = await db.scalar(
            text("SELECT count(*) FROM kb_pages WHERE install_id = :i"), {"i": install_id}
        )
        if (count or 0) >= MAX_PAGES_PER_INSTALL:
            raise PageLimit
        page = KbPage(
            org_id=org_id,
            install_id=install_id,
            kind=kind,
            url=url,
            title=title,
            content=content,
            content_sha256=digest,
            source=source,
            created_by=user_id,
        )
        db.add(page)
        await db.flush()
    page.chunk_count = len(chunks)
    page.blocked_chunks = sum(1 for c in chunks if c.blocked or looks_like_injection(c.text))
    for i, chunk in enumerate(chunks):
        db.add(
            KbChunk(
                org_id=org_id,
                install_id=install_id,
                page_id=page.id,
                kind=kind,
                idx=i,
                text=chunk.text,
                tokens=sorted(set(chunk.tokens)),
                blocked=chunk.blocked,
            )
        )
    await db.flush()
    return page


class PageLimit(Exception):  # noqa: N818 - domain term
    pass


async def candidates(
    db: AsyncSession, *, install_id: uuid.UUID, kind: str, terms: list[str]
) -> list[Candidate]:
    """Chunks sharing at least one term with the question (GIN-indexed array overlap)."""
    if not terms:
        return []
    rows = (
        await db.execute(
            text(
                "SELECT c.id, c.page_id, c.text, c.tokens, p.title, p.url FROM kb_chunks c "
                "JOIN kb_pages p ON p.id = c.page_id AND p.org_id = c.org_id "
                "WHERE c.install_id = :i AND c.kind = :k AND NOT c.blocked AND c.tokens && :t "
                "LIMIT 400"
            ),
            {"i": install_id, "k": kind, "t": terms},
        )
    ).all()
    return [
        Candidate(str(r.id), str(r.page_id), r.text, list(r.tokens), r.title, r.url) for r in rows
    ]


def expiry(days: int) -> datetime:
    return datetime.now(UTC) + timedelta(days=days)


# ------------------------------------------------------------------ learning consent tokens

CONSENT_PREFIX = "kmr_lc_"


def consent_token(secret: str, consent_id: uuid.UUID, install_id: uuid.UUID) -> str:
    """Shown once to the installer. Signed, so an id alone cannot be guessed into a token."""
    mac = sign(secret, "learning-consent", str(consent_id), str(install_id))[:32]
    return f"{CONSENT_PREFIX}{consent_id}.{mac}"


def parse_consent(secret: str, token: str, install_id: uuid.UUID) -> uuid.UUID | None:
    """The consent id inside a well-formed, correctly signed token for this install, else None."""
    if not token.startswith(CONSENT_PREFIX) or len(token) > 120:
        return None
    body, _, mac = token[len(CONSENT_PREFIX) :].partition(".")
    try:
        consent_id = uuid.UUID(body)
    except ValueError:
        return None
    expected = sign(secret, "learning-consent", str(consent_id), str(install_id))[:32]
    return consent_id if hmac.compare_digest(mac, expected) else None
