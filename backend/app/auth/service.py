import uuid
from datetime import UTC, datetime, timedelta

from fastapi import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import AuthSession
from app.security.sessions import cookie_name, hash_token, new_token


async def create_session(
    db: AsyncSession, user_id: uuid.UUID, user_agent: str | None, settings: Settings
) -> str:
    """Starts a brand-new session (never reuses one, so login cannot fixate a session)."""
    token = new_token()
    now = datetime.now(UTC)
    absolute = now + timedelta(days=settings.session_absolute_days)
    db.add(
        AuthSession(
            user_id=user_id,
            token_hash=hash_token(token),
            last_seen_at=now,
            idle_expires_at=min(now + timedelta(minutes=settings.session_idle_minutes), absolute),
            absolute_expires_at=absolute,
            user_agent=(user_agent or "")[:300] or None,
        )
    )
    return token


def set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        cookie_name(settings.cookie_secure_effective),
        token,
        max_age=settings.session_absolute_days * 86400,
        httponly=True,
        secure=settings.cookie_secure_effective,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        cookie_name(settings.cookie_secure_effective),
        httponly=True,
        secure=settings.cookie_secure_effective,
        samesite="lax",
        path="/",
    )
