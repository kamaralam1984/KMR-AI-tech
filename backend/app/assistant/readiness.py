"""Is the learning module allowed to say it is production-ready? Only with signed human reviews.

`production_ready` is true only when ALL of these hold:
- the automated evaluation passes every case;
- a legal review, a teaching review and a language (Hindi/Hinglish) review are each recorded as
  approved in `benchmarks/learning/signoffs.json`;
- each sign-off names the reviewer and organization, has not expired (12 months), and is bound to
  the exact cases file and solver/wording source that exist now, so changing either invalidates it;
- each sign-off carries a valid signature made with KMR_SIGNOFF_SECRET.

The signature proves a file was not edited by someone without the secret. It cannot prove that the
named people really reviewed anything: whoever holds the secret is trusted to record real reviews.
Nothing here generates, suggests or fills in a sign-off.
"""

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import get_settings

ROOT = Path(__file__).resolve().parents[3]
LEARNING_DIR = ROOT / "benchmarks" / "learning"
CASES = LEARNING_DIR / "cases.json"
SIGNOFFS = LEARNING_DIR / "signoffs.json"
SOLVER = Path(__file__).resolve().parent / "learning.py"
ROLES = ("legal", "teaching", "language")
VALIDITY = timedelta(days=365)
LABEL = {
    "legal": "legal review (child-data law, verifiable consent)",
    "teaching": "teacher review of the explanations and level limits",
    "language": "Hindi and Hinglish wording review by native speakers",
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def current_digests() -> dict[str, str]:
    return {"cases_sha256": sha256_file(CASES), "solver_sha256": sha256_file(SOLVER)}


def canonical(entry: dict[str, Any]) -> bytes:
    body = {k: v for k, v in entry.items() if k != "signature"}
    return json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def sign_entry(secret: str, entry: dict[str, Any]) -> str:
    key = hmac.new(secret.encode(), b"kmr-learning-signoff-v1", hashlib.sha256).digest()
    return hmac.new(key, canonical(entry), hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class Readiness:
    production_ready: bool
    blockers: list[str]
    message: str


def _entry_blockers(
    role: str, entry: dict[str, Any] | None, secret: str | None, now: datetime
) -> list[str]:
    name = LABEL[role]
    if entry is None:
        return [f"Missing: {name}."]
    if secret is None:
        return [f"Cannot verify the {role} sign-off: KMR_SIGNOFF_SECRET is not set."]
    sig = str(entry.get("signature", ""))
    if not hmac.compare_digest(sig, sign_entry(secret, entry)):
        return [f"The {role} sign-off signature is not valid."]
    if entry.get("result") != "approved":
        return [f"The {role} review is recorded as '{entry.get('result')}', not approved."]
    if not entry.get("reviewer_name") or not entry.get("organization"):
        return [f"The {role} sign-off does not name the reviewer and organization."]
    try:
        signed_on = datetime.fromisoformat(str(entry["date"])).replace(tzinfo=UTC)
    except (KeyError, ValueError):
        return [f"The {role} sign-off has no valid date."]
    if now - signed_on > VALIDITY:
        return [f"The {role} sign-off is older than 12 months."]
    digests = current_digests()
    if role != "legal" or entry.get("cases_sha256"):
        for key, value in digests.items():
            if entry.get(key) != value:
                return [
                    f"The {role} review was of an earlier version: {key.split('_')[0]} changed "
                    "since it was signed."
                ]
    return []


@lru_cache(maxsize=1)
def _eval_passes() -> bool:
    from app.assistant.evaluate import evaluate

    return bool(evaluate()["accuracy"] >= 1.0)


def status(now: datetime | None = None) -> Readiness:
    now = now or datetime.now(UTC)
    secret_value = get_settings().signoff_secret
    secret = secret_value.get_secret_value() if secret_value else None
    entries: dict[str, dict[str, Any]] = {}
    try:
        data = json.loads(SIGNOFFS.read_text())
        entries = {e["role"]: e for e in data.get("signoffs", []) if e.get("role") in ROLES}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        entries = {}
    blockers = [] if _eval_passes() else ["The automated evaluation has failing cases."]
    for role in ROLES:
        blockers += _entry_blockers(role, entries.get(role), secret, now)
    if blockers:
        return Readiness(
            False,
            blockers,
            "Beta. Not yet reviewed by a lawyer, teachers and native speakers; may be wrong.",
        )
    return Readiness(
        True,
        [],
        "Reviewed (legal, teaching, language). It can still be wrong: check important answers.",
    )


def today() -> str:
    return date.today().isoformat()
