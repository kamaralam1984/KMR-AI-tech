from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.auth.deps import (
    CurrentUser,
    allow,
    client_ip,
    current_user,
    digest,
    get_engine,
    get_rate_limiter,
    require_same_origin,
    secret_key,
)
from app.auth.service import clear_session_cookie, create_session, set_session_cookie
from app.config import Settings, get_settings
from app.db.audit import record_audit
from app.db.models import AuthSession, Membership, Organization, User
from app.db.tenant import tenant_session
from app.schemas import CsrfOut, LoginIn, MeOut, OrgSummary, RegisterIn, UserOut
from app.security.passwords import (
    hash_password,
    needs_rehash,
    verify_dummy,
    verify_password,
)
from app.security.rate_limit import RateLimiter
from app.security.sessions import csrf_token

router = APIRouter(prefix="/v1/auth", tags=["auth"])

# The same message for every failed login, so responses do not reveal which accounts exist.
_BAD_LOGIN = "Invalid email or password."


@router.post("/register", status_code=201, response_model=UserOut)
async def register(
    body: RegisterIn,
    request: Request,
    response: Response,
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
    limiter: RateLimiter = Depends(get_rate_limiter),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> UserOut:
    require_same_origin(request, settings)
    secret_key(settings)  # fail early if sessions are not configured
    if not await allow(
        limiter, f"register:ip:{client_ip(request)}", settings.register_max_per_hour, 3600
    ):
        raise HTTPException(
            429, "Too many sign-ups. Try again later.", headers={"Retry-After": "3600"}
        )

    password_hash = await hash_password(body.password)
    try:
        async with tenant_session(engine) as db:
            user = User(
                email=body.email, display_name=body.display_name, password_hash=password_hash
            )
            db.add(user)
            await db.flush()
            token = await create_session(db, user.id, request.headers.get("user-agent"), settings)
            await record_audit(
                db,
                action="auth.register",
                outcome="success",
                actor_user_id=user.id,
                target_type="user",
                target_id=user.id,
            )
    except IntegrityError as exc:
        raise HTTPException(409, "An account with this email already exists.") from exc

    set_session_cookie(response, token, settings)
    return UserOut(id=user.id, email=user.email, display_name=user.display_name)


@router.post("/login", response_model=UserOut)
async def login(
    body: LoginIn,
    request: Request,
    response: Response,
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
    limiter: RateLimiter = Depends(get_rate_limiter),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> UserOut:
    require_same_origin(request, settings)
    secret_key(settings)
    window = settings.login_window_minutes * 60
    email_tag = digest(body.email)
    allowed = await allow(
        limiter, f"login:ip:{client_ip(request)}", settings.login_max_attempts * 4, window
    ) and await allow(limiter, f"login:email:{email_tag}", settings.login_max_attempts, window)
    if not allowed:
        async with tenant_session(engine) as db:
            await record_audit(
                db,
                action="auth.login",
                outcome="denied",
                actor_user_id=None,
                details={"reason": "rate_limited", "email_tag": email_tag},
            )
        raise HTTPException(
            429, "Too many attempts. Try again later.", headers={"Retry-After": str(window)}
        )

    async with tenant_session(engine) as db:
        user = await db.scalar(select(User).where(User.email == body.email))

    if user is None or not user.is_active:
        await verify_dummy(body.password)  # keep timing similar to a real check
        valid = False
    else:
        valid = await verify_password(user.password_hash, body.password)

    if not valid or user is None:
        async with tenant_session(engine) as db:
            await record_audit(
                db,
                action="auth.login",
                outcome="failure",
                actor_user_id=None,
                details={"email_tag": email_tag},
            )
        raise HTTPException(401, _BAD_LOGIN)

    new_hash = await hash_password(body.password) if needs_rehash(user.password_hash) else None
    async with tenant_session(engine, user_id=user.id) as db:
        values: dict[str, object] = {"last_login_at": datetime.now(UTC)}
        if new_hash:
            values["password_hash"] = new_hash
        await db.execute(update(User).where(User.id == user.id).values(**values))
        token = await create_session(db, user.id, request.headers.get("user-agent"), settings)
        await record_audit(
            db,
            action="auth.login",
            outcome="success",
            actor_user_id=user.id,
            target_type="user",
            target_id=user.id,
        )

    set_session_cookie(response, token, settings)
    return UserOut(id=user.id, email=user.email, display_name=user.display_name)


@router.post("/logout", status_code=204)
async def logout(
    response: Response,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> None:
    async with tenant_session(engine, user_id=user.user_id) as db:
        await db.execute(
            update(AuthSession)
            .where(AuthSession.id == user.session_id)
            .values(revoked_at=datetime.now(UTC))
        )
        await record_audit(db, action="auth.logout", outcome="success", actor_user_id=user.user_id)
    clear_session_cookie(response, settings)


@router.post("/logout-all", status_code=204)
async def logout_all(
    response: Response,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> None:
    async with tenant_session(engine, user_id=user.user_id) as db:
        await db.execute(
            update(AuthSession)
            .where(AuthSession.user_id == user.user_id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        await record_audit(
            db, action="auth.logout_all", outcome="success", actor_user_id=user.user_id
        )
    clear_session_cookie(response, settings)


@router.get("/csrf", response_model=CsrfOut)
async def get_csrf(
    user: CurrentUser = Depends(current_user),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> CsrfOut:
    return CsrfOut(csrf_token=csrf_token(secret_key(settings), user.token_hash))


@router.get("/me", response_model=MeOut)
async def me(
    user: CurrentUser = Depends(current_user),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> MeOut:
    async with tenant_session(engine, user_id=user.user_id) as db:
        rows = (
            await db.execute(
                select(Organization.id, Organization.name, Membership.role)
                .join(Membership, Membership.org_id == Organization.id)
                .where(Membership.user_id == user.user_id)
                .order_by(Organization.name)
            )
        ).all()
    return MeOut(
        user=UserOut(id=user.user_id, email=user.email, display_name=user.display_name),
        organizations=[OrgSummary(id=r.id, name=r.name, role=r.role) for r in rows],
    )
