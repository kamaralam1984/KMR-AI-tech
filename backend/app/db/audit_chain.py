"""A tamper-evident chain over each organization's audit events.

Every chained event stores the sequence number, the previous event's hash and its own hash, which
covers all of its fields and the previous hash. Editing a field, deleting an event, reordering
events or inserting one in the middle breaks verification at that point. The application role can
only append (no UPDATE or DELETE on audit_events), and an advisory lock per organization keeps two
concurrent writers from forking the chain.

Limits, stated honestly: someone with the database owner role can rewrite the whole chain from any
point on, and truncating the newest events is only detectable against a hash stored elsewhere.
`tip` is returned by verification so an external system can record it.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditEvent

GENESIS = "0" * 64


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def compute_hash(
    *,
    prev_hash: str,
    seq: int,
    org_id: uuid.UUID,
    event_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    action: str,
    outcome: str,
    target_type: str | None,
    target_id: str | None,
    details: dict[str, Any],
    created_at: datetime,
) -> str:
    body = canonical(
        {
            "prev": prev_hash,
            "seq": seq,
            "org": str(org_id),
            "id": str(event_id),
            "actor": str(actor_user_id) if actor_user_id else None,
            "action": action,
            "outcome": outcome,
            "target_type": target_type,
            "target_id": target_id,
            "details": details,
            "at": created_at.astimezone(UTC).isoformat(),
        }
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


async def chain_event(session: AsyncSession, event: AuditEvent) -> None:
    """Sets the chain fields on `event` (already carrying its other fields) before it is added."""
    if event.org_id is None:
        raise ValueError("only organization events are chained")
    await session.flush()
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"audit:{event.org_id}"},
    )
    tip = (
        await session.execute(
            select(AuditEvent.chain_seq, AuditEvent.entry_hash)
            .where(AuditEvent.org_id == event.org_id, AuditEvent.chain_seq.is_not(None))
            .order_by(AuditEvent.chain_seq.desc())
            .limit(1)
        )
    ).first()
    seq = (tip.chain_seq if tip else 0) + 1
    prev = tip.entry_hash if tip else GENESIS
    event.chain_seq, event.prev_hash = seq, prev
    event.entry_hash = compute_hash(
        prev_hash=prev,
        seq=seq,
        org_id=event.org_id,
        event_id=event.id,
        actor_user_id=event.actor_user_id,
        action=event.action,
        outcome=event.outcome,
        target_type=event.target_type,
        target_id=event.target_id,
        details=event.details,
        created_at=event.created_at,
    )


@dataclass
class ChainReport:
    ok: bool
    checked: int
    unchained: int
    tip: str | None
    broken_at: int | None = None
    reason: str | None = None


async def verify_chain(session: AsyncSession, org_id: uuid.UUID) -> ChainReport:
    rows = (
        await session.scalars(
            select(AuditEvent)
            .where(AuditEvent.org_id == org_id, AuditEvent.chain_seq.is_not(None))
            .order_by(AuditEvent.chain_seq)
        )
    ).all()
    unchained = int(
        (
            await session.execute(
                text("SELECT count(*) FROM audit_events WHERE org_id = :o AND chain_seq IS NULL"),
                {"o": org_id},
            )
        ).scalar()
        or 0
    )
    prev, expected = GENESIS, 1
    for row in rows:
        seq = row.chain_seq or 0
        if seq != expected:
            return ChainReport(False, expected - 1, unchained, None, expected, "missing_event")
        if row.prev_hash != prev:
            return ChainReport(False, expected - 1, unchained, None, seq, "broken_link")
        recomputed = compute_hash(
            prev_hash=prev,
            seq=seq,
            org_id=org_id,
            event_id=row.id,
            actor_user_id=row.actor_user_id,
            action=row.action,
            outcome=row.outcome,
            target_type=row.target_type,
            target_id=row.target_id,
            details=row.details,
            created_at=row.created_at,
        )
        if recomputed != row.entry_hash:
            return ChainReport(False, expected - 1, unchained, None, seq, "event_modified")
        prev, expected = recomputed, expected + 1
    return ChainReport(True, len(rows), unchained, prev if rows else None)
