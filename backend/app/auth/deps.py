"""Authentication, CSRF and organization-authorization dependencies.

Every protected route depends on `current_user` (directly or through `org_access`), which is
what the "no route without authentication" test in tests/test_auth_required.py relies on.
"""

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import Settings, get_settings
from app.db.audit import record_audit
from app.db.models import AuthSession, User
from app.db.tenant import Role, get_role, has_role, tenant_session
from app.security.rate_limit import RateLimiter, RateLimiterUnavailable
from app.security.sessions import CSRF_HEADER, SAFE_METHODS, cookie_name, csrf_matches, hash_token

# Idle expiry is only extended when the session was last touched more than this long ago.
_TOUCH_INTERVAL = timedelta(seconds=60)


def get_engine(request: Request) -> AsyncEngine:
    engine: AsyncEngine | None = getattr(request.app.state, "engine", None)
    if engine is None:
        raise HTTPException(503, "The database is not configured.")
    return engine


def get_rate_limiter(request: Request) -> RateLimiter:
    limiter: RateLimiter | None = getattr(request.app.state, "rate_limiter", None)
    if limiter is None:
        raise HTTPException(503, "Rate limiting is not available.")
    return limiter


def secret_key(settings: Settings) -> str:
    if settings.secret_key is None:
        raise HTTPException(503, "The server is not configured for sessions.")
    return settings.secret_key.get_secret_value()


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def digest(value: str) -> str:
    """A short, non-reversible tag used in rate-limit keys and audit details instead of PII."""
    return hashlib.sha256(value.encode()).hexdigest()[:16]


async def allow(limiter: RateLimiter, key: str, limit: int, window_seconds: int) -> bool:
    try:
        return await limiter.allow(key, limit, window_seconds)
    except RateLimiterUnavailable as exc:
        # Fail closed: without a working limiter, brute force could not be slowed down.
        raise HTTPException(503, "Temporarily unavailable. Please try again shortly.") from exc


def require_same_origin(request: Request, settings: Settings) -> None:
    """Rejects cross-site browser requests. Requests without an Origin header are not browsers."""
    origin = request.headers.get("origin")
    if origin is not None and origin not in settings.cors_origins:
        raise HTTPException(403, "Cross-origin request rejected.")


@dataclass(frozen=True)
class CurrentUser:
    user_id: uuid.UUID
    email: str
    display_name: str
    session_id: uuid.UUID
    token_hash: bytes


async def current_user(
    request: Request,
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> CurrentUser:
    unauthorized = HTTPException(401, "Authentication required.")
    token = request.cookies.get(cookie_name(settings.cookie_secure_effective))
    if not token:
        raise unauthorized
    token_hash = hash_token(token)
    now = datetime.now(UTC)

    async with tenant_session(engine) as db:
        row = (
            await db.execute(
                select(AuthSession, User)
                .join(User, User.id == AuthSession.user_id)
                .where(AuthSession.token_hash == token_hash)
            )
        ).first()
        if row is None:
            raise unauthorized
        session, user = row
        if (
            session.revoked_at is not None
            or session.idle_expires_at <= now
            or session.absolute_expires_at <= now
            or not user.is_active
        ):
            raise unauthorized
        if now - session.last_seen_at > _TOUCH_INTERVAL:
            idle = min(
                now + timedelta(minutes=settings.session_idle_minutes), session.absolute_expires_at
            )
            await db.execute(
                update(AuthSession)
                .where(AuthSession.id == session.id)
                .values(last_seen_at=now, idle_expires_at=idle)
            )

    if request.method not in SAFE_METHODS:
        require_same_origin(request, settings)
        if not csrf_matches(secret_key(settings), token_hash, request.headers.get(CSRF_HEADER)):
            raise HTTPException(403, "Missing or invalid CSRF token.")

    return CurrentUser(user.id, user.email, user.display_name, session.id, token_hash)


@dataclass(frozen=True)
class OrgAccess:
    user: CurrentUser
    org_id: uuid.UUID
    role: Role


def org_access(min_role: Role):  # type: ignore[no-untyped-def]
    """Dependency factory: the caller must belong to the organization with at least `min_role`.

    Non-members get 404 (not 403) so the existence of an organization is not revealed.
    """

    async def dependency(
        org_id: uuid.UUID,
        user: CurrentUser = Depends(current_user),  # noqa: B008
        engine: AsyncEngine = Depends(get_engine),  # noqa: B008
    ) -> OrgAccess:
        role = await get_role(engine, user.user_id, org_id)
        if role is None:
            # Org-less on purpose: writing into the probed organization's audit log would let
            # an outsider pollute it.
            async with tenant_session(engine, user_id=user.user_id) as db:
                await record_audit(
                    db,
                    action="access.denied",
                    outcome="denied",
                    actor_user_id=user.user_id,
                    details={"reason": "not_a_member", "target_org": str(org_id)},
                )
            raise HTTPException(404, "Organization not found.")
        if not has_role(role, min_role):
            async with tenant_session(engine, user_id=user.user_id, org_id=org_id) as db:
                await record_audit(
                    db,
                    action="access.denied",
                    outcome="denied",
                    actor_user_id=user.user_id,
                    org_id=org_id,
                    details={"reason": "insufficient_role", "role": role, "required": min_role},
                )
            raise HTTPException(403, "You do not have permission to do this.")
        return OrgAccess(user=user, org_id=org_id, role=role)

    return dependency
