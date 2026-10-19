import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.config import get_settings
from tests.helpers import project_base, registered, verified_project


def _setup(new_api):  # type: ignore[no-untyped-def]
    api = registered(new_api)
    org = api.create_org()
    project = api.create_project(org)
    return api, org, project, project_base(org, project)


def test_a_new_project_has_no_site_and_shows_how_to_verify_once_one_is_set(new_api) -> None:  # type: ignore[no-untyped-def]
    api, org, project, base = _setup(new_api)
    fresh = api.get(f"/v1/organizations/{org}/projects/{project}").json()["site"]
    assert fresh["site_url"] is None and fresh["verification"]["verified"] is False
    assert fresh["verification"]["dns_record_name"] is None

    response = api.request("PUT", f"{base}/site", {"site_url": "https://Shop.Example.com/home#x"})
    assert response.status_code == 200
    site = response.json()["site"]
    assert site["site_url"] == "https://shop.example.com/home"
    assert site["site_host"] == "shop.example.com"
    verification = site["verification"]
    assert verification["verified"] is False
    assert verification["dns_record_name"] == "_kmr-verify.shop.example.com"
    assert verification["dns_record_value"].startswith("kmr-verification=")
    assert verification["file_url"] == "https://shop.example.com/.well-known/kmr-verification.txt"
    assert verification["file_content"] == verification["dns_record_value"]


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/",
        "http://127.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/",
        "http://[::1]/",
        "http://2130706433/",
        "file:///etc/passwd",
        "ftp://example.com/",
        "https://user:secret@example.com/",
        "https://example.com:22/",
        "http://metadata.google.internal/",
        "not a url",
    ],
)
def test_dangerous_site_addresses_are_refused_and_nothing_is_saved(new_api, url: str) -> None:  # type: ignore[no-untyped-def]
    api, org, project, base = _setup(new_api)
    response = api.request("PUT", f"{base}/site", {"site_url": url})
    assert response.status_code == 422, url
    assert "secret" not in response.text
    site = api.get(f"/v1/organizations/{org}/projects/{project}").json()["site"]
    assert site["site_url"] is None


def test_exclusions_and_page_limit_are_validated(new_api) -> None:  # type: ignore[no-untyped-def]
    api, _org, _project, base = _setup(new_api)
    site = {"site_url": "https://example.com/"}
    assert api.request("PUT", f"{base}/site", {**site, "exclusions": ["admin"]}).status_code == 422
    assert api.request("PUT", f"{base}/site", {**site, "exclusions": ["/a b"]}).status_code == 422
    assert api.request("PUT", f"{base}/site", {**site, "max_pages": 0}).status_code == 422
    assert api.request("PUT", f"{base}/site", {**site, "max_pages": 51}).status_code == 422
    ok = api.request(
        "PUT",
        f"{base}/site",
        {**site, "exclusions": ["/logout", "/admin", "/admin"], "max_pages": 7},
    )
    assert ok.status_code == 200
    assert ok.json()["site"]["exclusions"] == ["/admin", "/logout"]
    assert ok.json()["site"]["max_pages"] == 7


def test_only_members_and_above_can_set_the_site_or_verify(new_api) -> None:  # type: ignore[no-untyped-def]
    owner, org, _project, base = _setup(new_api)
    viewer = registered(new_api)
    assert owner.add_member(org, viewer.email, "viewer").status_code == 201  # type: ignore[arg-type]
    assert (
        viewer.request("PUT", f"{base}/site", {"site_url": "https://example.com/"}).status_code
        == 403
    )
    assert viewer.post(f"{base}/verify", {"method": "dns"}).status_code == 403


def test_other_tenants_get_404_for_site_and_verify(new_api) -> None:  # type: ignore[no-untyped-def]
    _owner, _org, _project, base = _setup(new_api)
    outsider = registered(new_api)
    assert (
        outsider.request("PUT", f"{base}/site", {"site_url": "https://example.com/"}).status_code
        == 404
    )
    assert outsider.post(f"{base}/verify", {"method": "dns"}).status_code == 404


def test_verification_requires_a_site_first(new_api, fake_verifier) -> None:  # type: ignore[no-untyped-def]
    api, _org, _project, base = _setup(new_api)
    assert api.post(f"{base}/verify", {"method": "dns"}).status_code == 409


def test_dns_verification_succeeds_with_the_right_record(new_api, fake_verifier) -> None:  # type: ignore[no-untyped-def]
    api, org, project = registered(new_api), None, None
    org = api.create_org()
    project = api.create_project(org)
    base = project_base(org, project)
    site = api.request("PUT", f"{base}/site", {"site_url": "https://example.com/"}).json()["site"]
    fake_verifier.txt[site["verification"]["dns_record_name"]] = [
        "v=spf1 -all",
        site["verification"]["dns_record_value"],
    ]
    result = api.post(f"{base}/verify", {"method": "dns"}).json()
    assert result["verified"] is True and result["reason"] is None
    verification = result["project"]["site"]["verification"]
    assert verification["verified"] is True and verification["method"] == "dns"
    assert verification["verified_at"] is not None


def test_a_wrong_or_missing_record_does_not_verify(new_api, fake_verifier) -> None:  # type: ignore[no-untyped-def]
    api, org, project, base = _setup(new_api)
    site = api.request("PUT", f"{base}/site", {"site_url": "https://example.com/"}).json()["site"]
    name = site["verification"]["dns_record_name"]
    for records in ([], ["kmr-verification=somebody-elses-token"]):
        fake_verifier.txt[name] = records
        result = api.post(f"{base}/verify", {"method": "dns"}).json()
        assert result["verified"] is False and result["reason"]
        assert result["project"]["site"]["verification"]["verified"] is False


def test_file_verification(new_api, fake_verifier) -> None:  # type: ignore[no-untyped-def]
    api, _org, _project, base = _setup(new_api)
    site = api.request("PUT", f"{base}/site", {"site_url": "http://example.com/"}).json()["site"]
    verification = site["verification"]
    assert verification["file_url"].startswith("http://example.com/")
    fake_verifier.files[verification["file_url"]] = "wrong content"
    assert api.post(f"{base}/verify", {"method": "file"}).json()["verified"] is False
    fake_verifier.files[verification["file_url"]] = verification["file_content"] + "\n"
    result = api.post(f"{base}/verify", {"method": "file"}).json()
    assert result["verified"] is True
    assert result["project"]["site"]["verification"]["method"] == "file"


def test_an_unavailable_check_is_a_503_and_not_a_failed_proof(  # type: ignore[no-untyped-def]
    new_api, fake_verifier, owner_engine: Engine
) -> None:
    api, _org, _project, base = _setup(new_api)
    api.request("PUT", f"{base}/site", {"site_url": "https://example.com/"})
    fake_verifier.unavailable = True
    assert api.post(f"{base}/verify", {"method": "file"}).status_code == 503
    with owner_engine.connect() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM audit_events WHERE action = 'domain.verify'")
        ).scalar()
    assert count == 0


def test_verification_checks_are_rate_limited(new_api, fake_verifier, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    api, _org, _project, base = _setup(new_api)
    api.request("PUT", f"{base}/site", {"site_url": "https://example.com/"})
    monkeypatch.setenv("KMR_VERIFICATION_CHECKS_PER_HOUR", "3")
    get_settings.cache_clear()
    statuses = [api.post(f"{base}/verify", {"method": "dns"}).status_code for _ in range(5)]
    assert statuses == [200, 200, 200, 429, 429]


def test_changing_the_host_discards_verification_even_if_you_switch_back(  # type: ignore[no-untyped-def]
    new_api, fake_verifier
) -> None:
    api = registered(new_api)
    org, project = verified_project(api, fake_verifier, host="a.example.com")
    base = project_base(org, project)

    def verified() -> bool:
        site = api.get(f"/v1/organizations/{org}/projects/{project}").json()["site"]
        return bool(site["verification"]["verified"])

    assert verified() is True
    # Same host, different path: the proof still applies.
    api.request("PUT", f"{base}/site", {"site_url": "https://a.example.com/other"})
    assert verified() is True
    api.request("PUT", f"{base}/site", {"site_url": "https://b.example.com/"})
    assert verified() is False
    api.request("PUT", f"{base}/site", {"site_url": "https://a.example.com/"})
    assert verified() is False  # must be proven again: ownership may have changed meanwhile


def test_audit_records_site_and_verification_without_secrets(  # type: ignore[no-untyped-def]
    new_api, fake_verifier, owner_engine: Engine
) -> None:
    api = registered(new_api)
    verified_project(api, fake_verifier)
    with owner_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT action, outcome, details::text FROM audit_events "
                "WHERE action IN ('site.set', 'domain.verify')"
            )
        ).all()
        token = conn.execute(text("SELECT verification_token FROM projects")).scalar()
    assert {(r[0], r[1]) for r in rows} == {("site.set", "success"), ("domain.verify", "success")}
    assert all(token not in r[2] for r in rows)
