"""Phase 11C: verifiable guardian consent, and the gate behind `production_ready`."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.assistant import readiness, review_kit
from app.config import get_settings
from tests.helpers import registered
from tests.test_assistant_api import base, hdr, make_install


@pytest.fixture
def setup(new_api):  # type: ignore[no-untyped-def]
    api = registered(new_api, "Owner")
    org = api.create_org("School")
    install, key = make_install(
        api, org, modules=["guide", "learning"], acknowledge_child_safeguards=True
    )
    return api, org, install, key, new_api()


def issue(api, org, install, **over) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    body = {
        "label": "class-7B-roll-14",
        "age_band": "12-14",
        "consent_basis": "Signed paper form on file at the school office",
        "days": 30,
        **over,
    }
    r = api.post(f"{base(org)}/installs/{install}/consents", body)
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


def ask(v, key, token=None, band="12-14", **over):  # type: ignore[no-untyped-def]
    headers = hdr(key)
    if token:
        headers["X-KMR-Consent"] = token
    body = {"question": "What is 6 x 7?", "age_band": band, **over}
    return v.post("/public/v1/learning/ask", body, csrf=False, headers=headers)


# ----------------------------------------------------------------------- consent tokens


def test_under_18_needs_a_valid_consent_token_and_the_checkbox_no_longer_counts(setup) -> None:  # type: ignore[no-untyped-def]
    api, org, install, key, v = setup
    assert ask(v, key, guardian_confirmed=True).status_code == 403  # a checkbox is only a claim
    consent = issue(api, org, install)
    r = ask(v, key, consent["token"])
    assert r.status_code == 200 and r.json()["result"] == "42"
    assert ask(v, key, band="18+").status_code == 200  # adults need none


def test_a_token_is_signed_scoped_and_not_guessable(setup, new_api) -> None:  # type: ignore[no-untyped-def]
    api, org, install, key, v = setup
    consent = issue(api, org, install)
    token = consent["token"]
    forged = token[:-4] + ("aaaa" if not token.endswith("aaaa") else "bbbb")
    assert ask(v, key, forged).status_code == 403
    assert ask(v, key, f"kmr_lc_{consent['id']}.").status_code == 403
    assert ask(v, key, f"kmr_lc_{uuid.uuid4()}.{'0' * 32}").status_code == 403
    assert ask(v, key, "garbage").status_code == 403
    # A token from one install does not work on another install of the same organization.
    other, other_key = make_install(
        api, org, name="Second", modules=["learning"], acknowledge_child_safeguards=True
    )
    assert ask(v, other_key, token).status_code == 403


def test_a_token_does_not_work_across_organizations(setup, new_api) -> None:  # type: ignore[no-untyped-def]
    api, org, install, key, v = setup
    token = issue(api, org, install)["token"]
    b = registered(new_api, "Other")
    org_b = b.create_org("Other school")
    _i, key_b = make_install(b, org_b, modules=["learning"], acknowledge_child_safeguards=True)
    assert ask(new_api(), key_b, token).status_code == 403
    assert b.get(f"{base(org)}/installs/{install}/consents").status_code in (403, 404)
    assert b.post(
        f"{base(org)}/installs/{install}/consents",
        {"label": "x", "age_band": "9-11", "consent_basis": "nothing at all", "days": 5},
    ).status_code in (403, 404)


def test_revoked_and_expired_consents_stop_working(setup, owner_engine: Engine) -> None:  # type: ignore[no-untyped-def]
    api, org, install, key, v = setup
    first = issue(api, org, install)
    assert ask(v, key, first["token"]).status_code == 200
    revoked = api.post(f"{base(org)}/installs/{install}/consents/{first['id']}/revoke")
    assert revoked.status_code == 200 and revoked.json()["revoked"] is True
    assert ask(v, key, first["token"]).status_code == 403

    second = issue(api, org, install, label="class-7B-roll-15")
    assert ask(v, key, second["token"]).status_code == 200
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE learning_consents SET expires_at = now() - interval '1 minute' WHERE id = :i"
            ),
            {"i": second["id"]},
        )
    assert ask(v, key, second["token"]).status_code == 403


def test_a_consent_does_not_let_a_learner_claim_an_older_band(setup) -> None:  # type: ignore[no-untyped-def]
    api, org, install, key, v = setup
    token = issue(api, org, install, age_band="9-11")["token"]
    assert ask(v, key, token, band="9-11").status_code == 200
    assert ask(v, key, token, band="6-8").status_code == 200  # younger content is fine
    assert ask(v, key, token, band="12-14").status_code == 403
    assert ask(v, key, token, band="15-17").status_code == 403


def test_consent_records_hold_no_personal_data_and_are_audited(setup, owner_engine: Engine) -> None:  # type: ignore[no-untyped-def]
    api, org, install, key, v = setup
    consent = issue(api, org, install)
    listing = api.get(f"{base(org)}/installs/{install}/consents").json()
    assert listing[0]["id"] == consent["id"] and "token" not in listing[0]
    ask(v, key, consent["token"])
    with owner_engine.begin() as conn:
        audit = conn.execute(
            text("SELECT details::text FROM audit_events WHERE action = 'learning.consent_issue'")
        ).scalar_one()
    assert consent["token"] not in audit and "class-7B" not in audit
    assert json.loads(audit) == {"age_band": "12-14", "days": 30}


def test_only_admins_with_the_learning_module_on_can_issue_consents(setup, new_api) -> None:  # type: ignore[no-untyped-def]
    api, org, install, _key, _v = setup
    member = registered(new_api, "Member")
    api.add_member(org, member.email or "", "member")
    body = {"label": "x", "age_band": "9-11", "consent_basis": "form on file", "days": 5}
    assert member.post(f"{base(org)}/installs/{install}/consents", body).status_code == 403
    guide_only, _k = make_install(api, org, name="Guide only")
    assert api.post(f"{base(org)}/installs/{guide_only}/consents", body).status_code == 409
    bad = {**body, "age_band": "18+"}
    assert api.post(f"{base(org)}/installs/{install}/consents", bad).status_code == 422


def test_turning_enforcement_off_is_possible_but_reported(setup) -> None:  # type: ignore[no-untyped-def]
    api, org, install, key, v = setup
    config = v.get("/public/v1/config", headers=hdr(key)).json()
    assert config["learning"]["consent_required"] is True
    assert config["learning"]["production_ready"] is False
    patched = api.patch(f"{base(org)}/installs/{install}", {"learning_consent_required": False})
    assert patched.status_code == 200 and patched.json()["learning_consent_required"] is False
    off = v.get("/public/v1/config", headers=hdr(key)).json()["learning"]
    assert off["consent_required"] is False and off["production_ready"] is False


# ------------------------------------------------------------------------ readiness gate


SECRET = "test-signoff-secret-0123456789"  # noqa: S105 - throwaway test value


@pytest.fixture
def gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    path = tmp_path / "signoffs.json"
    monkeypatch.setattr(readiness, "SIGNOFFS", path)
    monkeypatch.setattr(review_kit, "REVIEWS", tmp_path / "reviews")
    monkeypatch.setenv("KMR_SIGNOFF_SECRET", SECRET)
    get_settings.cache_clear()
    yield path
    get_settings.cache_clear()


def entry(role: str, **over: Any) -> dict[str, Any]:
    e: dict[str, Any] = {
        "role": role,
        "reviewer_name": "A. Reviewer",
        "reviewer_qualification": "Qualified person",
        "organization": "Some Org",
        "date": datetime.now(UTC).date().isoformat(),
        "result": "approved",
        "scope": "everything",
        "conditions": [],
        **readiness.current_digests(),
        **over,
    }
    e["signature"] = readiness.sign_entry(SECRET, e)
    return e


def write(path: Path, *entries: dict[str, Any]) -> None:
    path.write_text(json.dumps({"signoffs": list(entries)}))


def test_with_no_signoffs_it_is_not_ready_and_says_what_is_missing(gate) -> None:  # type: ignore[no-untyped-def]
    status = readiness.status()
    assert status.production_ready is False
    assert len(status.blockers) == 3 and all("Missing" in b for b in status.blockers)
    assert "Beta" in status.message


def test_all_three_valid_signoffs_open_the_gate(gate) -> None:  # type: ignore[no-untyped-def]
    write(gate, *(entry(r) for r in readiness.ROLES))
    status = readiness.status()
    assert status.production_ready is True and status.blockers == []
    assert "can still be wrong" in status.message


@pytest.mark.parametrize(
    ("mutate", "needle"),
    [
        (lambda e: {**e, "reviewer_name": "Someone Else"}, "signature is not valid"),
        (lambda e: entry(e["role"], result="rejected"), "not approved"),
        (lambda e: entry(e["role"], organization=""), "does not name"),
        (
            lambda e: entry(
                e["role"], date=(datetime.now(UTC) - timedelta(days=400)).date().isoformat()
            ),
            "older than 12 months",
        ),
        (lambda e: entry(e["role"], cases_sha256="0" * 64), "earlier version"),
        (lambda e: entry(e["role"], solver_sha256="0" * 64), "earlier version"),
    ],
)
def test_any_flaw_in_one_signoff_keeps_the_gate_shut(gate, mutate, needle) -> None:  # type: ignore[no-untyped-def]
    good = [entry(r) for r in readiness.ROLES]
    write(gate, good[0], mutate(good[1]), good[2])
    status = readiness.status()
    assert status.production_ready is False
    assert any(needle in b for b in status.blockers), status.blockers


def test_without_the_signing_secret_nothing_can_be_trusted(gate, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    write(gate, *(entry(r) for r in readiness.ROLES))
    monkeypatch.delenv("KMR_SIGNOFF_SECRET")
    get_settings.cache_clear()
    status = readiness.status()
    assert status.production_ready is False and "KMR_SIGNOFF_SECRET" in status.blockers[0]


def test_a_failing_evaluation_shuts_the_gate_even_with_signoffs(gate, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    write(gate, *(entry(r) for r in readiness.ROLES))
    monkeypatch.setattr(readiness, "_eval_passes", lambda: False)
    status = readiness.status()
    assert status.production_ready is False and "automated evaluation" in status.blockers[0]


def test_a_garbage_signoff_file_is_treated_as_no_signoffs(gate) -> None:  # type: ignore[no-untyped-def]
    gate.write_text("not json")
    assert readiness.status().production_ready is False
    gate.write_text(json.dumps({"signoffs": "nope"}))
    assert readiness.status().production_ready is False


# ------------------------------------------------------------------------- review kit


def test_export_import_summarize_and_sign_follow_the_thresholds(
    gate, tmp_path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(review_kit, "SHEETS", tmp_path / "sheets")
    sheets = review_kit.export()
    teaching = next(p for p in sheets if p.name.startswith("teaching"))
    assert len(teaching.read_text().splitlines()) == len(review_kit._cases()) + 1

    summary = review_kit.summarize()["kinds"]["teaching"]
    assert summary["meets_thresholds"] is False and summary["cases_reviewed_by_enough_people"] == 0

    def fill(path: Path, mark: str, only: set[str] | None = None) -> Path:
        import csv

        rows = list(csv.reader(path.open(encoding="utf-8")))
        header = rows[0]
        out = [header]
        for row in rows[1:]:
            if only is None or row[0] in only:
                for i in range(len(header)):
                    if header[i].endswith("(Y/N)") or header[i].endswith("(Y/N/NA)"):
                        row[i] = mark
            out.append(row)
        filled = tmp_path / f"filled-{mark}-{len(out)}.csv"
        with filled.open("w", newline="", encoding="utf-8") as h:
            csv.writer(h).writerows(out)
        return Path(filled)

    review_kit.import_sheet(
        fill(teaching, "Y"), reviewer="Teacher One", qualification="B.Ed, 10 years", kind="teaching"
    )
    one = review_kit.summarize()["kinds"]["teaching"]
    assert one["meets_thresholds"] is False  # one reviewer is not enough
    review_kit.import_sheet(
        fill(teaching, "Y"), reviewer="Teacher Two", qualification="M.Ed", kind="teaching"
    )
    two = review_kit.summarize()["kinds"]["teaching"]
    assert two["meets_thresholds"] is True and two["reviewers"] == ["Teacher One", "Teacher Two"]

    # One reviewer flags a case: the thresholds stop being met until it is resolved.
    review_kit.import_sheet(
        fill(teaching, "N", {"arithmetic-001"}),
        reviewer="Teacher Three",
        qualification="M.Sc",
        kind="teaching",
    )
    flagged = review_kit.summarize()["kinds"]["teaching"]
    assert flagged["meets_thresholds"] is False and "arithmetic-001" in flagged["flagged_cases"]


def test_a_blank_sheet_is_not_a_review(gate, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(review_kit, "SHEETS", tmp_path / "sheets")
    teaching = next(p for p in review_kit.export() if p.name.startswith("teaching"))
    with pytest.raises(SystemExit):
        review_kit.import_sheet(teaching, reviewer="X", qualification="Y", kind="teaching")
    with pytest.raises(SystemExit):
        review_kit.import_sheet(teaching, reviewer="", qualification="Y", kind="teaching")


def test_sign_refuses_without_meeting_thresholds_a_document_or_the_secret(
    gate, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    import argparse

    def args(**over: Any) -> argparse.Namespace:
        base_args = dict(
            role="teaching",
            reviewer="R",
            qualification="Q",
            organization="O",
            result="approved",
            scope="s",
            condition=None,
            document=None,
        )
        return argparse.Namespace(**{**base_args, **over})

    with pytest.raises(SystemExit, match="thresholds"):
        review_kit.sign(args())
    with pytest.raises(SystemExit, match="--document"):
        review_kit.sign(args(role="legal"))
    monkeypatch.delenv("KMR_SIGNOFF_SECRET")
    get_settings.cache_clear()
    with pytest.raises(SystemExit, match="KMR_SIGNOFF_SECRET"):
        review_kit.sign(args(role="legal"))


def test_a_legal_signoff_records_the_document_hash_and_validates(gate, tmp_path) -> None:  # type: ignore[no-untyped-def]
    import argparse

    document = tmp_path / "opinion.txt"
    document.write_text("Counsel's written opinion (test fixture).")
    review_kit.sign(
        argparse.Namespace(
            role="legal",
            reviewer="Counsel",
            qualification="Advocate",
            organization="Firm LLP",
            result="approved",
            scope="DPDP review",
            condition=["Collect consent offline"],
            document=str(document),
        )
    )
    saved = json.loads(gate.read_text())["signoffs"][0]
    assert saved["document_sha256"] == readiness.sha256_file(document) and saved["conditions"] == [
        "Collect consent offline"
    ]
    status = readiness.status()
    assert not any("legal" in b.lower() and "Missing" in b for b in status.blockers)
    assert status.production_ready is False  # teaching and language are still missing


def test_the_repository_has_no_prefilled_signoffs() -> None:
    """The committed state must be 'not reviewed'. Nobody's approval is invented by the code."""
    path = readiness.SIGNOFFS
    if path.exists():
        assert json.loads(path.read_text()).get("signoffs", []) == [], (
            "signoffs.json holds sign-offs: they must come from real reviews"
        )
