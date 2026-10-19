from tests.helpers import Api, registered


def _team(new_api):  # type: ignore[no-untyped-def]
    """An organization with one user of each role."""
    owner = registered(new_api, "Owner")
    org = owner.create_org("Team")
    users: dict[str, Api] = {"owner": owner}
    for role in ("admin", "member", "viewer"):
        users[role] = registered(new_api, role.title())
        assert owner.add_member(org, users[role].email, role).status_code == 201  # type: ignore[arg-type]
    return org, users


def _members(api: Api, org: str) -> dict[str, str]:
    return {m["email"]: m["role"] for m in api.get(f"/v1/organizations/{org}/members").json()}


def test_viewer_can_read_but_not_change_anything(new_api) -> None:  # type: ignore[no-untyped-def]
    org, u = _team(new_api)
    project = u["owner"].create_project(org)
    v = u["viewer"]
    assert v.get(f"/v1/organizations/{org}").status_code == 200
    assert v.get(f"/v1/organizations/{org}/projects").status_code == 200
    assert v.get(f"/v1/organizations/{org}/members").status_code == 200
    assert v.post(f"/v1/organizations/{org}/projects", {"name": "x"}).status_code == 403
    assert v.patch(f"/v1/organizations/{org}/projects/{project}", {"name": "x"}).status_code == 403
    assert v.patch(f"/v1/organizations/{org}", {"name": "x"}).status_code == 403
    assert v.add_member(org, u["member"].email, "viewer").status_code == 403  # type: ignore[arg-type]


def test_member_manages_projects_but_not_people_or_the_organization(new_api) -> None:  # type: ignore[no-untyped-def]
    org, u = _team(new_api)
    m = u["member"]
    project = m.create_project(org, "mine")
    assert (
        m.patch(f"/v1/organizations/{org}/projects/{project}", {"name": "renamed"}).status_code
        == 200
    )
    assert m.patch(f"/v1/organizations/{org}", {"name": "x"}).status_code == 403
    assert m.add_member(org, u["viewer"].email, "viewer").status_code == 403  # type: ignore[arg-type]
    assert (
        m.patch(
            f"/v1/organizations/{org}/members/{u['viewer'].user_id}", {"role": "member"}
        ).status_code
        == 403
    )


def test_admin_cannot_create_or_touch_other_admins_and_owners(new_api) -> None:  # type: ignore[no-untyped-def]
    org, u = _team(new_api)
    a = u["admin"]
    newcomer = registered(new_api)
    assert a.patch(f"/v1/organizations/{org}", {"name": "Renamed"}).status_code == 200
    assert a.add_member(org, newcomer.email, "admin").status_code == 403  # type: ignore[arg-type]
    assert a.add_member(org, newcomer.email, "owner").status_code == 403  # type: ignore[arg-type]
    assert a.add_member(org, newcomer.email, "viewer").status_code == 201  # type: ignore[arg-type]
    base = f"/v1/organizations/{org}/members"
    assert a.patch(f"{base}/{u['member'].user_id}", {"role": "admin"}).status_code == 403
    assert a.patch(f"{base}/{u['owner'].user_id}", {"role": "viewer"}).status_code == 403
    assert a.delete(f"{base}/{u['owner'].user_id}").status_code == 403
    assert a.patch(f"{base}/{u['member'].user_id}", {"role": "viewer"}).status_code == 200
    assert a.delete(f"{base}/{u['viewer'].user_id}").status_code == 204


def test_nobody_can_change_their_own_role(new_api) -> None:  # type: ignore[no-untyped-def]
    org, u = _team(new_api)
    for role in ("owner", "admin"):
        response = u[role].patch(
            f"/v1/organizations/{org}/members/{u[role].user_id}", {"role": "owner"}
        )
        assert response.status_code == 403, role


def test_an_organization_always_keeps_one_owner(new_api) -> None:  # type: ignore[no-untyped-def]
    org, u = _team(new_api)
    owner = u["owner"]
    me = f"/v1/organizations/{org}/members/{owner.user_id}"
    assert owner.delete(me).status_code == 409  # sole owner cannot leave

    second = u["admin"]
    base = f"/v1/organizations/{org}/members"
    assert owner.patch(f"{base}/{second.user_id}", {"role": "owner"}).status_code == 200
    # Two owners: either may now be demoted by the other...
    assert second.patch(f"{base}/{owner.user_id}", {"role": "admin"}).status_code == 200
    # ...but the last remaining owner is protected from removal and demotion.
    assert second.delete(f"{base}/{second.user_id}").status_code == 409
    assert second.delete(f"{base}/{owner.user_id}").status_code == 204
    assert second.delete(f"{base}/{second.user_id}").status_code == 409


def test_a_member_can_leave(new_api) -> None:  # type: ignore[no-untyped-def]
    org, u = _team(new_api)
    m = u["member"]
    assert m.delete(f"/v1/organizations/{org}/members/{m.user_id}").status_code == 204
    assert m.get(f"/v1/organizations/{org}").status_code == 404


def test_owner_can_promote_and_roles_are_listed(new_api) -> None:  # type: ignore[no-untyped-def]
    org, u = _team(new_api)
    owner = u["owner"]
    owner.patch(f"/v1/organizations/{org}/members/{u['viewer'].user_id}", {"role": "member"})
    listed = _members(owner, org)
    assert listed[u["viewer"].email] == "member"  # type: ignore[index]
    assert listed[owner.email] == "owner"  # type: ignore[index]


def test_membership_edge_cases(new_api) -> None:  # type: ignore[no-untyped-def]
    org, u = _team(new_api)
    owner = u["owner"]
    assert owner.add_member(org, "nobody-here@example.com").status_code == 404
    assert owner.add_member(org, u["member"].email).status_code == 409  # type: ignore[arg-type]
    assert (
        owner.post(
            f"/v1/organizations/{org}/members", {"email": u["viewer"].email, "role": "root"}
        ).status_code
        == 422
    )
    stranger_id = registered(new_api).user_id
    assert (
        owner.patch(
            f"/v1/organizations/{org}/members/{stranger_id}", {"role": "viewer"}
        ).status_code
        == 404
    )
    assert owner.delete(f"/v1/organizations/{org}/members/{stranger_id}").status_code == 404


def test_denied_role_checks_are_audited_inside_the_organization(new_api, owner_engine) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import text

    org, u = _team(new_api)
    u["viewer"].post(f"/v1/organizations/{org}/projects", {"name": "x"})
    with owner_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT outcome, details FROM audit_events "
                "WHERE org_id = :o AND action = 'access.denied'"
            ),
            {"o": org},
        ).one()
    assert row.outcome == "denied"
    assert row.details["reason"] == "insufficient_role"
    assert row.details["role"] == "viewer"
