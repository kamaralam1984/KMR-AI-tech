from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.auth.deps import OrgAccess, get_engine, org_access
from app.db.audit_chain import verify_chain
from app.db.models import AuditEvent
from app.db.tenant import tenant_session
from app.schemas import AuditEventOut, AuditVerifyOut

router = APIRouter(prefix="/v1/organizations/{org_id}/audit", tags=["audit"])


@router.get("", response_model=list[AuditEventOut])
async def list_events(
    action: Annotated[str | None, Query(max_length=80)] = None,
    target_id: Annotated[str | None, Query(max_length=80)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    access: OrgAccess = Depends(org_access("admin")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> list[AuditEventOut]:
    filters = [AuditEvent.org_id == access.org_id]
    if action:
        filters.append(AuditEvent.action == action)
    if target_id:
        filters.append(AuditEvent.target_id == target_id)
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        rows = await db.scalars(
            select(AuditEvent)
            .where(*filters)
            .order_by(AuditEvent.created_at.desc(), AuditEvent.id)
            .limit(limit)
            .offset(offset)
        )
        return [
            AuditEventOut(
                id=e.id,
                chain_seq=e.chain_seq,
                action=e.action,
                outcome=e.outcome,
                actor_user_id=e.actor_user_id,
                target_type=e.target_type,
                target_id=e.target_id,
                details=e.details,
                entry_hash=e.entry_hash,
                created_at=e.created_at,
            )
            for e in rows
        ]


@router.get("/verify", response_model=AuditVerifyOut)
async def verify(
    access: OrgAccess = Depends(org_access("admin")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> AuditVerifyOut:
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        report = await verify_chain(db, access.org_id)
    return AuditVerifyOut(
        ok=report.ok,
        checked=report.checked,
        unchained=report.unchained,
        tip=report.tip,
        broken_at=report.broken_at,
        reason=report.reason,
    )
