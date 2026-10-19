import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.audit_chain import chain_event
from app.db.models import AuditEvent
from app.logging_config import redact

Outcome = Literal["success", "denied", "failure"]


async def record_audit(
    session: AsyncSession,
    *,
    action: str,
    outcome: Outcome,
    actor_user_id: uuid.UUID | None,
    org_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: uuid.UUID | str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Appends an audit row. Values under secret-looking keys are masked before storage.

    Events of an organization are added to its hash chain (see audit_chain.py).
    """
    event = AuditEvent(
        org_id=org_id,
        actor_user_id=actor_user_id,
        action=action,
        outcome=outcome,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        details=redact(details or {}),
    )
    if org_id is not None:
        event.id = uuid.uuid4()
        event.created_at = datetime.now(UTC)
        await chain_event(session, event)
    session.add(event)
