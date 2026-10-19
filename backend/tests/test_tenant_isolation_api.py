import uuid

from sqlalchemy import text
from sqlalchemy.engine import Engine

from tests.helpers import registered


def _two_tenants(new_api):  # type: ignore[no-untyped-def]
    a = registered(new_api, "Alice")
    b = registered(new_api, "Bob")
    org_a, org_b = a.create_org("Org A"), b.create_org("Org B")
    return a, b, org_a, org_b, a.create_project(org_a, "A site"), b.create_project(org_b, "B site")


def test_user_cannot_read_another_tenants_data(new_api) -> None:  # type: ignore[no-untyped-def]
    a, _b, _org_a, org_b, _pa, pb = _two_tenants(new_api)
    base = f"/v1/organizations/{org_b}"
    for path in (base, f"{base}/members", f"{base}/projects", f"{base}/projects/{pb}"):
        response = a.get(path)
        assert response.status_code == 404, path


def test_user_cannot_write_to_another_tenant(new_api, owner_engine: Engine) -> None:  # type: ignore[no-untyped-def]
    a, _b, _org_a, org_b, _pa, pb = _two_tenants(new_api)
    base = f"/v1/organizations/{org_b}"
    assert a.post(f"{base}/projects", {"name": "evil"}).status_code == 404
    assert a.patch(f"{base}/projects/{pb}", {"name": "pwned"}).status_code == 404
    assert a.patch(base, {"name": "pwned"}).status_code == 404
    assert a.add_member(org_b, "x@example.com").status_code == 404
    with owner_engine.connect() as conn:
        assert (
            conn.execute(text("SELECT name FROM projects WHERE id = :p"), {"p": pb}).scalar()
            == "B site"
        )
        assert (
            conn.execute(
                text("SELECT name FROM organizations WHERE id = :o"), {"o": org_b}
            ).scalar()
            == "Org B"
        )
        assert conn.execute(text("SELECT count(*) FROM projects")).scalar() == 2


def test_guessing_another_projects_uuid_grants_nothing(new_api) -> None:  # type: ignore[no-untyped-def]
    a, _b, org_a, _org_b, _pa, pb = _two_tenants(new_api)
    # B's real project id, but reached through A's own organization.
    assert a.get(f"/v1/organizations/{org_a}/projects/{pb}").status_code == 404
    assert a.patch(f"/v1/organizations/{org_a}/projects/{pb}", {"name": "x"}).status_code == 404


def test_existing_and_nonexistent_organizations_are_indistinguishable(new_api) -> None:  # type: ignore[no-untyped-def]
    a, _b, _org_a, org_b, _pa, _pb = _two_tenants(new_api)
    real = a.get(f"/v1/organizations/{org_b}")
    fake = a.get(f"/v1/organizations/{uuid.uuid4()}")
    assert real.status_code == fake.status_code == 404
    real_body, fake_body = real.json()["error"], fake.json()["error"]
    assert real_body["message"] == fake_body["message"]
    assert real_body["code"] == fake_body["code"]


def test_listings_only_contain_own_data(new_api) -> None:  # type: ignore[no-untyped-def]
    a, b, org_a, _org_b, _pa, _pb = _two_tenants(new_api)
    assert [o["id"] for o in a.get("/v1/organizations").json()] == [org_a]
    assert [p["name"] for p in a.get(f"/v1/organizations/{org_a}/projects").json()] == ["A site"]
    assert [o["name"] for o in b.get("/v1/organizations").json()] == ["Org B"]
    assert [o["name"] for o in a.get("/v1/auth/me").json()["organizations"]] == ["Org A"]


def test_probing_does_not_write_into_the_victims_audit_log(new_api, owner_engine: Engine) -> None:  # type: ignore[no-untyped-def]
    a, _b, _org_a, org_b, _pa, _pb = _two_tenants(new_api)
    for _ in range(3):
        a.get(f"/v1/organizations/{org_b}")
    with owner_engine.connect() as conn:
        in_victim = conn.execute(
            text(
                "SELECT count(*) FROM audit_events WHERE org_id = :o AND action = 'access.denied'"
            ),
            {"o": org_b},
        ).scalar()
        org_less = conn.execute(
            text(
                "SELECT count(*) FROM audit_events "
                "WHERE org_id IS NULL AND action = 'access.denied'"
            )
        ).scalar()
    assert in_victim == 0
    assert org_less == 3


def test_removed_member_loses_access_immediately(new_api) -> None:  # type: ignore[no-untyped-def]
    owner = registered(new_api, "Owner")
    member = registered(new_api, "Member")
    org = owner.create_org()
    assert owner.add_member(org, member.email, "member").status_code == 201  # type: ignore[arg-type]
    assert member.get(f"/v1/organizations/{org}/projects").status_code == 200
    assert owner.delete(f"/v1/organizations/{org}/members/{member.user_id}").status_code == 204
    assert member.get(f"/v1/organizations/{org}/projects").status_code == 404
