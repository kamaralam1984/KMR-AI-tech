import json

from sqlalchemy import text
from sqlalchemy.engine import Engine

from tests.helpers import COOKIE, PASSWORD, random_email, registered


def _events(owner_engine: Engine) -> list[dict]:  # type: ignore[type-arg]
    with owner_engine.connect() as conn:
        rows = (
            conn.execute(
                text(
                    "SELECT action, outcome, org_id, actor_user_id, details FROM audit_events "
                    "ORDER BY created_at"
                )
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]


def test_key_actions_are_recorded(new_api, owner_engine: Engine) -> None:  # type: ignore[no-untyped-def]
    user = registered(new_api)
    new_api().login(user.email, "wrong password entirely")  # type: ignore[arg-type]
    other = new_api()
    other.login(user.email)  # type: ignore[arg-type]
    org = user.create_org("Audited")
    user.create_project(org)
    seen = {(e["action"], e["outcome"]) for e in _events(owner_engine)}
    assert {
        ("auth.register", "success"),
        ("auth.login", "failure"),
        ("auth.login", "success"),
        ("org.create", "success"),
        ("project.create", "success"),
    } <= seen


def test_org_events_carry_the_org_and_actor(new_api, owner_engine: Engine) -> None:  # type: ignore[no-untyped-def]
    user = registered(new_api)
    org = user.create_org()
    event = next(e for e in _events(owner_engine) if e["action"] == "org.create")
    assert str(event["org_id"]) == org
    assert str(event["actor_user_id"]) == user.user_id


def test_failed_login_event_has_no_actor_and_no_email(new_api, owner_engine: Engine) -> None:  # type: ignore[no-untyped-def]
    target = random_email()
    new_api().login(target)
    event = next(e for e in _events(owner_engine) if e["outcome"] == "failure")
    assert event["actor_user_id"] is None
    assert event["org_id"] is None
    assert target not in json.dumps(event["details"], default=str)
    assert "email_tag" in event["details"]


def test_audit_log_never_contains_secrets(new_api, owner_engine: Engine) -> None:  # type: ignore[no-untyped-def]
    user = registered(new_api)
    token = user.jar[COOKIE]
    user.create_org()
    new_api().login(user.email, "another wrong password")  # type: ignore[arg-type]
    dump = json.dumps(_events(owner_engine), default=str)
    assert PASSWORD not in dump
    assert "another wrong password" not in dump
    assert token not in dump


def test_sensitive_detail_keys_are_masked_before_storage(new_api, owner_engine: Engine, pg) -> None:  # type: ignore[no-untyped-def]
    import anyio

    from app.db.audit import record_audit
    from app.db.engine import create_engine
    from app.db.tenant import tenant_session

    async def write() -> None:
        engine = create_engine(pg.app_url)
        try:
            async with tenant_session(engine) as db:
                await record_audit(
                    db,
                    action="test.mask",
                    outcome="success",
                    actor_user_id=None,
                    details={"token": "abc", "authorization": "Bearer x", "ok": 1},
                )
        finally:
            await engine.dispose()

    anyio.run(write)
    event = next(e for e in _events(owner_engine) if e["action"] == "test.mask")
    assert event["details"] == {"token": "[REDACTED]", "authorization": "[REDACTED]", "ok": 1}
