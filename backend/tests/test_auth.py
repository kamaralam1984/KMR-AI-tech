import hashlib

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.config import get_settings
from tests.helpers import COOKIE, PASSWORD, Api, random_email, registered


def _set(owner_engine: Engine, sql: str, **params: object) -> None:
    with owner_engine.begin() as conn:
        conn.execute(text(sql), params)


# ----------------------------------------------------------------------------- registration


def test_register_signs_the_user_in_and_sets_a_hardened_cookie(new_api) -> None:  # type: ignore[no-untyped-def]
    api: Api = new_api()
    response = api.register()
    assert response.status_code == 201
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "path=/" in cookie
    assert "secure" not in cookie  # local/test only: http://localhost cannot carry Secure cookies
    assert api.get("/v1/auth/me").json()["user"]["email"] == api.email


def test_cookie_is_secure_and_host_prefixed_outside_local(
    new_api,
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
) -> None:
    monkeypatch.setenv("KMR_COOKIE_SECURE", "true")
    get_settings.cache_clear()
    response = new_api().register()
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("__Host-kmr_session=")
    assert "Secure" in cookie and "HttpOnly" in cookie


def test_password_is_stored_as_an_argon2id_hash(new_api, owner_engine: Engine) -> None:  # type: ignore[no-untyped-def]
    api = registered(new_api)
    with owner_engine.connect() as conn:
        stored = conn.execute(
            text("SELECT password_hash FROM users WHERE email = :e"), {"e": api.email}
        ).scalar()
    assert stored is not None
    assert stored.startswith("$argon2id$")
    assert PASSWORD not in stored


def test_session_token_is_stored_only_as_a_hash(new_api, owner_engine: Engine) -> None:  # type: ignore[no-untyped-def]
    api = registered(new_api)
    token = api.jar[COOKIE]
    with owner_engine.connect() as conn:
        stored = conn.execute(text("SELECT token_hash FROM auth_sessions")).scalar()
    assert stored is not None
    assert bytes(stored) == hashlib.sha256(token.encode()).digest()
    assert token.encode() not in bytes(stored)


@pytest.mark.parametrize(
    "password",
    ["short", "a" * 129],
)
def test_password_length_policy(new_api, password: str) -> None:  # type: ignore[no-untyped-def]
    assert new_api().register(password=password).status_code == 422


def test_password_must_differ_from_email(new_api) -> None:  # type: ignore[no-untyped-def]
    email = "long-enough-address@example.com"
    assert new_api().register(email=email, password=email).status_code == 422


def test_duplicate_email_is_rejected_case_insensitively(new_api) -> None:  # type: ignore[no-untyped-def]
    first = registered(new_api)
    assert new_api().register(email=first.email.upper()).status_code == 409  # type: ignore[union-attr]


# ----------------------------------------------------------------------------- login


def test_failed_logins_look_identical_for_unknown_and_wrong_password(new_api) -> None:  # type: ignore[no-untyped-def]
    known = registered(new_api)
    wrong_password = new_api().login(known.email, "totally the wrong password")  # type: ignore[arg-type]
    unknown_account = new_api().login(random_email(), PASSWORD)
    assert wrong_password.status_code == unknown_account.status_code == 401
    assert wrong_password.json()["error"]["message"] == unknown_account.json()["error"]["message"]
    assert wrong_password.json()["error"]["code"] == unknown_account.json()["error"]["code"]
    assert "set-cookie" not in wrong_password.headers


def test_login_issues_a_new_session_each_time(new_api) -> None:  # type: ignore[no-untyped-def]
    user = registered(new_api)
    again = new_api()
    assert again.login(user.email).status_code == 200  # type: ignore[arg-type]
    assert again.jar[COOKIE] != user.jar[COOKIE]
    assert user.get("/v1/auth/me").status_code == 200
    assert again.get("/v1/auth/me").status_code == 200


def test_login_is_rate_limited_per_account(new_api) -> None:  # type: ignore[no-untyped-def]
    user = registered(new_api)
    attacker = new_api()
    statuses = [attacker.login(user.email, f"wrong password {i}").status_code for i in range(7)]  # type: ignore[arg-type]
    assert statuses[:5] == [401] * 5
    assert statuses[5:] == [429, 429]
    # Even the right password is refused while the account is throttled.
    blocked = attacker.login(user.email)  # type: ignore[arg-type]
    assert blocked.status_code == 429
    assert "retry-after" in blocked.headers


def test_inactive_user_cannot_log_in_or_keep_a_session(new_api, owner_engine: Engine) -> None:  # type: ignore[no-untyped-def]
    user = registered(new_api)
    _set(owner_engine, "UPDATE users SET is_active = false WHERE email = :e", e=user.email)
    assert user.get("/v1/auth/me").status_code == 401
    assert new_api().login(user.email).status_code == 401  # type: ignore[arg-type]


# ----------------------------------------------------------------------------- sessions


def test_logout_revokes_the_session(new_api) -> None:  # type: ignore[no-untyped-def]
    user = registered(new_api)
    stolen = new_api()
    stolen.jar = dict(user.jar)  # an attacker holding a copy of the cookie
    assert user.post("/v1/auth/logout").status_code == 204
    assert user.get("/v1/auth/me").status_code == 401
    assert stolen.get("/v1/auth/me").status_code == 401


def test_logout_all_revokes_every_session(new_api) -> None:  # type: ignore[no-untyped-def]
    first = registered(new_api)
    second = new_api()
    assert second.login(first.email).status_code == 200  # type: ignore[arg-type]
    assert first.post("/v1/auth/logout-all").status_code == 204
    assert first.get("/v1/auth/me").status_code == 401
    assert second.get("/v1/auth/me").status_code == 401


def test_idle_expiry(new_api, owner_engine: Engine) -> None:  # type: ignore[no-untyped-def]
    user = registered(new_api)
    _set(owner_engine, "UPDATE auth_sessions SET idle_expires_at = now() - interval '1 minute'")
    assert user.get("/v1/auth/me").status_code == 401


def test_absolute_expiry_even_if_recently_active(new_api, owner_engine: Engine) -> None:  # type: ignore[no-untyped-def]
    user = registered(new_api)
    _set(owner_engine, "UPDATE auth_sessions SET absolute_expires_at = now() - interval '1 minute'")
    assert user.get("/v1/auth/me").status_code == 401


def test_idle_expiry_slides_forward_with_activity_but_not_past_the_absolute_limit(
    new_api,
    owner_engine: Engine,  # type: ignore[no-untyped-def]
) -> None:
    user = registered(new_api)
    _set(
        owner_engine,
        "UPDATE auth_sessions SET last_seen_at = now() - interval '5 minutes', "
        "idle_expires_at = now() + interval '1 minute'",
    )
    assert user.get("/v1/auth/me").status_code == 200
    with owner_engine.connect() as conn:
        extended = conn.execute(
            text("SELECT idle_expires_at > now() + interval '1 hour' FROM auth_sessions")
        ).scalar()
    assert extended is True

    _set(
        owner_engine,
        "UPDATE auth_sessions SET last_seen_at = now() - interval '5 minutes', "
        "absolute_expires_at = now() + interval '10 minutes'",
    )
    assert user.get("/v1/auth/me").status_code == 200
    with owner_engine.connect() as conn:
        capped = conn.execute(
            text("SELECT idle_expires_at <= absolute_expires_at FROM auth_sessions")
        ).scalar()
    assert capped is True


# ----------------------------------------------------------------------------- CSRF / origin


def test_state_changing_request_needs_a_valid_csrf_token(new_api) -> None:  # type: ignore[no-untyped-def]
    user = registered(new_api)
    body = {"name": "Acme"}
    assert user.post("/v1/organizations", body, csrf=False).status_code == 403
    wrong = user.post("/v1/organizations", body, csrf=False, headers={"X-CSRF-Token": "0" * 64})
    assert wrong.status_code == 403
    assert user.post("/v1/organizations", body).status_code == 201


def test_csrf_token_from_another_session_is_rejected(new_api) -> None:  # type: ignore[no-untyped-def]
    victim, attacker = registered(new_api), registered(new_api)
    forged = victim.post(
        "/v1/organizations",
        {"name": "x"},
        csrf=False,
        headers={"X-CSRF-Token": attacker.csrf_token()},
    )
    assert forged.status_code == 403


def test_safe_requests_do_not_need_csrf(new_api) -> None:  # type: ignore[no-untyped-def]
    assert registered(new_api).get("/v1/organizations").status_code == 200


def test_cross_site_origin_is_rejected_even_with_a_valid_token(new_api) -> None:  # type: ignore[no-untyped-def]
    user = registered(new_api)
    evil = {"Origin": "https://evil.example"}
    assert user.post("/v1/organizations", {"name": "x"}, headers=evil).status_code == 403
    ok = {"Origin": "http://localhost:3000"}
    assert user.post("/v1/organizations", {"name": "x"}, headers=ok).status_code == 201


def test_login_and_register_reject_cross_site_origin(new_api) -> None:  # type: ignore[no-untyped-def]
    user = registered(new_api)
    evil = {"Origin": "https://evil.example"}
    api: Api = new_api()
    assert (
        api.request(
            "POST", "/v1/auth/login", {"email": user.email, "password": PASSWORD}, headers=evil
        ).status_code
        == 403
    )
    assert (
        api.request(
            "POST",
            "/v1/auth/register",
            {"email": random_email(), "password": PASSWORD, "display_name": "x"},
            headers=evil,
        ).status_code
        == 403
    )
