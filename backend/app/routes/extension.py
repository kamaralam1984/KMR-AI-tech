"""Owner-side management of extension tokens (cookie session, like every other /v1 route)."""

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from app.auth.deps import OrgAccess, get_engine, org_access
from app.db.audit import record_audit
from app.db.models import ExtensionToken
from app.db.tenant import has_role, tenant_session
from app.extension.schemas import TokenCreatedOut, TokenIn, TokenOut
from app.extension.service import new_token

router = APIRouter(prefix="/v1/organizations/{org_id}/extension-tokens", tags=["extension"])
MAX_ACTIVE_PER_USER = 5


def _out(t: ExtensionToken) -> TokenOut:
    return TokenOut(
        id=t.id,
        name=t.name,
        token_prefix=t.token_prefix,
        kind=t.kind,
        user_id=t.user_id,
        expires_at=t.expires_at,
        revoked=t.revoked_at is not None,
        last_used_at=t.last_used_at,
        created_at=t.created_at,
    )


@router.get("", response_model=list[TokenOut])
async def list_tokens(
    access: OrgAccess = Depends(org_access("member")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> list[TokenOut]:
    """Members see their own tokens; admins see every token of the organization."""
    query = select(ExtensionToken).where(ExtensionToken.org_id == access.org_id)
    if not has_role(access.role, "admin"):
        query = query.where(ExtensionToken.user_id == access.user.user_id)
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        rows = (await db.scalars(query.order_by(ExtensionToken.created_at.desc()).limit(100))).all()
    return [_out(t) for t in rows]


@router.post("", status_code=201, response_model=TokenCreatedOut)
async def create_token(
    body: TokenIn,
    access: OrgAccess = Depends(org_access("member")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> TokenCreatedOut:
    raw, digest, prefix = new_token(body.kind)
    now = datetime.now(UTC)
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        active = (
            await db.scalars(
                select(ExtensionToken.id).where(
                    ExtensionToken.org_id == access.org_id,
                    ExtensionToken.user_id == access.user.user_id,
                    ExtensionToken.kind == body.kind,
                    ExtensionToken.revoked_at.is_(None),
                    ExtensionToken.expires_at > now,
                )
            )
        ).all()
        if len(active) >= MAX_ACTIVE_PER_USER:
            raise HTTPException(
                409, f"You already have {MAX_ACTIVE_PER_USER} active tokens. Revoke one first."
            )
        token = ExtensionToken(
            org_id=access.org_id,
            user_id=access.user.user_id,
            name=body.name.strip(),
            token_hash=digest,
            token_prefix=prefix,
            kind=body.kind,
            expires_at=now + timedelta(days=body.days),
            created_at=now,
        )
        db.add(token)
        await db.flush()
        await record_audit(
            db,
            action="extension.token_create",
            outcome="success",
            actor_user_id=access.user.user_id,
            org_id=access.org_id,
            target_type="extension_token",
            target_id=token.id,
            details={"days": body.days, "kind": body.kind},
        )
        out = _out(token)
    return TokenCreatedOut(**out.model_dump(), token=raw)


@router.post("/{token_id}/revoke", response_model=TokenOut)
async def revoke_token(
    token_id: uuid.UUID,
    access: OrgAccess = Depends(org_access("member")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> TokenOut:
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        token = await db.scalar(
            select(ExtensionToken).where(
                ExtensionToken.id == token_id, ExtensionToken.org_id == access.org_id
            )
        )
        if token is None or (
            token.user_id != access.user.user_id and not has_role(access.role, "admin")
        ):
            raise HTTPException(404, "Token not found.")
        if token.revoked_at is None:
            now = datetime.now(UTC)
            await db.execute(
                update(ExtensionToken).where(ExtensionToken.id == token_id).values(revoked_at=now)
            )
            token.revoked_at = now
            await record_audit(
                db,
                action="extension.token_revoke",
                outcome="success",
                actor_user_id=access.user.user_id,
                org_id=access.org_id,
                target_type="extension_token",
                target_id=token.id,
            )
        return _out(token)
