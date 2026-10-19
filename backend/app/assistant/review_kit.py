"""Tools for the people who must review the learning module, and for recording what they decided.

    python -m app.assistant.review_kit export     # sheets for teachers and language reviewers
    python -m app.assistant.review_kit import FILE.csv --reviewer NAME \\
        --qualification TEXT --kind teaching
    python -m app.assistant.review_kit summarize  # what the recorded reviews add up to
    python -m app.assistant.review_kit sign --role teaching --reviewer NAME --organization ORG ...

Nothing here fills in a review. `sign` refuses unless the recorded reviews meet the thresholds (for
teaching and language) or a legal opinion document is supplied (for legal), and needs
KMR_SIGNOFF_SECRET. See docs/CHILD-LEARNING-SAFEGUARDS.md for the process.
"""

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any

from app.assistant import readiness
from app.assistant.learning import solve
from app.config import get_settings

REVIEWS = readiness.LEARNING_DIR / "reviews"
SHEETS = readiness.LEARNING_DIR / "review"
YES = {"y", "yes", "haan", "ha", "ok"}
NO = {"n", "no", "nahi", "nahin"}
# Thresholds a recorded review must meet before `sign` will accept it.
MIN_REVIEWERS = 2
MIN_APPROVAL = 0.95
MIN_LANGUAGE_APPROVAL = 0.90
COLUMNS = (
    "correct",
    "clear_for_age",
    "level_suitable",
    "wording_natural",
)


def _cases() -> list[dict[str, Any]]:
    return json.loads(readiness.CASES.read_text())  # type: ignore[no-any-return]


def _shown(case: dict[str, Any]) -> dict[str, str]:
    result = solve(case["question"], band=case["age_band"])
    return {
        "answer": result.result or result.message,
        "steps": " | ".join(result.steps),
        "notes": " | ".join(result.notes),
        "language": result.language,
    }


def export() -> list[Path]:
    SHEETS.mkdir(parents=True, exist_ok=True)
    out = []
    for kind, wanted in (("teaching", None), ("language", {"hi", "hinglish"})):
        path = SHEETS / f"{kind}-review-sheet.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "id", "category", "age_band", "language", "question", "answer_shown", "steps",
                    "notes", "correct (Y/N)", "clear_for_age (Y/N)", "level_suitable (Y/N)",
                    "wording_natural (Y/N/NA)", "comments",
                ]
            )  # fmt: skip
            for case in _cases():
                shown = _shown(case)
                if wanted and shown["language"] not in wanted:
                    continue
                writer.writerow(
                    [case["id"], case["category"], case["age_band"], shown["language"],
                     case["question"], shown["answer"], shown["steps"], shown["notes"],
                     "", "", "", "", ""]
                )  # fmt: skip
        out.append(path)
    return out


def _flag(value: str) -> bool | None:
    value = value.strip().lower()
    return True if value in YES else False if value in NO else None


def import_sheet(path: Path, *, reviewer: str, qualification: str, kind: str) -> Path:
    if kind not in ("teaching", "language"):
        raise SystemExit("--kind must be teaching or language")
    if not reviewer.strip() or not qualification.strip():
        raise SystemExit("Name and qualification of the reviewer are required.")
    ratings: dict[str, dict[str, Any]] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            vals = {
                "correct": _flag(row.get("correct (Y/N)", "")),
                "clear_for_age": _flag(row.get("clear_for_age (Y/N)", "")),
                "level_suitable": _flag(row.get("level_suitable (Y/N)", "")),
                "wording_natural": _flag(row.get("wording_natural (Y/N/NA)", "")),
            }
            if all(v is None for v in vals.values()):
                continue  # row left blank: not reviewed
            ratings[row["id"]] = {**vals, "comments": (row.get("comments") or "")[:500]}
    if not ratings:
        raise SystemExit("The sheet has no ratings filled in.")
    REVIEWS.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", reviewer.lower()).strip("-")
    target = REVIEWS / f"{kind}-{readiness.today()}-{slug}.json"
    target.write_text(
        json.dumps(
            {
                "kind": kind,
                "reviewer_name": reviewer.strip(),
                "qualification": qualification.strip(),
                "date": readiness.today(),
                **readiness.current_digests(),
                "ratings": ratings,
            },
            indent=1,
            ensure_ascii=False,
        )
    )
    return target


def summarize() -> dict[str, Any]:
    digests = readiness.current_digests()
    cases = {c["id"]: c for c in _cases()}
    language_ids = {i for i, c in cases.items() if _shown(c)["language"] in ("hi", "hinglish")}
    reviews = []
    if REVIEWS.exists():
        for f in sorted(REVIEWS.glob("*.json")):
            data = json.loads(f.read_text())
            reviews.append(
                {
                    **data,
                    "file": f.name,
                    "current": all(data.get(k) == v for k, v in digests.items()),
                }
            )
    out: dict[str, Any] = {"digests": digests, "kinds": {}}
    for kind in ("teaching", "language"):
        mine = [r for r in reviews if r["kind"] == kind and r["current"]]
        stale = [r["file"] for r in reviews if r["kind"] == kind and not r["current"]]
        scope = set(cases) if kind == "teaching" else language_ids
        per_case: dict[str, list[dict[str, Any]]] = {}
        for r in mine:
            for cid, rating in r["ratings"].items():
                if cid in scope:
                    per_case.setdefault(cid, []).append(rating)
        key = "correct" if kind == "teaching" else "wording_natural"
        rated = [v for v in per_case.values() if len({id(x) for x in v}) >= 1]
        enough = [cid for cid, v in per_case.items() if len(v) >= MIN_REVIEWERS]
        yes = sum(1 for v in rated for x in v if x.get(key) is True)
        answered = sum(1 for v in rated for x in v if x.get(key) is not None)
        flagged = sorted(cid for cid, v in per_case.items() if any(x.get(key) is False for x in v))
        threshold = MIN_APPROVAL if kind == "teaching" else MIN_LANGUAGE_APPROVAL
        rate = (yes / answered) if answered else 0.0
        out["kinds"][kind] = {
            "cases_in_scope": len(scope),
            "cases_reviewed_by_enough_people": len(enough),
            "required_reviewers_per_case": MIN_REVIEWERS,
            "approval_rate": round(rate, 4),
            "threshold": threshold,
            "flagged_cases": flagged,
            "reviewers": sorted({r["reviewer_name"] for r in mine}),
            "stale_review_files": stale,
            "meets_thresholds": bool(scope)
            and len(enough) == len(scope)
            and rate >= threshold
            and not flagged,
        }
    return out


def sign(args: argparse.Namespace) -> Path:
    secret = get_settings().signoff_secret
    if secret is None:
        raise SystemExit(
            "Set KMR_SIGNOFF_SECRET first. It is what makes a sign-off tamper-evident."
        )
    role = args.role
    entry: dict[str, Any] = {
        "role": role,
        "reviewer_name": args.reviewer.strip(),
        "reviewer_qualification": args.qualification.strip(),
        "organization": args.organization.strip(),
        "date": readiness.today(),
        "result": args.result,
        "scope": args.scope.strip(),
        "conditions": args.condition or [],
        **readiness.current_digests(),
    }
    if not all(
        entry[k] for k in ("reviewer_name", "reviewer_qualification", "organization", "scope")
    ):
        raise SystemExit("Reviewer name, qualification, organization and scope are required.")
    if role == "legal":
        if not args.document:
            raise SystemExit("A legal sign-off needs --document: the written opinion (file).")
        entry["document_sha256"] = readiness.sha256_file(Path(args.document))
    else:
        summary = summarize()["kinds"][role]
        if not summary["meets_thresholds"]:
            raise SystemExit(
                f"The recorded {role} reviews do not meet the thresholds yet:\n"
                + json.dumps(summary, indent=2, ensure_ascii=False)
            )
        entry["review_summary"] = {k: summary[k] for k in ("approval_rate", "reviewers")}
    entry["signature"] = readiness.sign_entry(secret.get_secret_value(), entry)
    data: dict[str, Any] = {"signoffs": []}
    if readiness.SIGNOFFS.exists():
        data = json.loads(readiness.SIGNOFFS.read_text())
    data["signoffs"] = [e for e in data["signoffs"] if e.get("role") != role] + [entry]
    readiness.SIGNOFFS.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    return readiness.SIGNOFFS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="review_kit")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("export")
    sub.add_parser("summarize")
    imp = sub.add_parser("import")
    imp.add_argument("file", type=Path)
    imp.add_argument("--reviewer", required=True)
    imp.add_argument("--qualification", required=True)
    imp.add_argument("--kind", required=True, choices=["teaching", "language"])
    sg = sub.add_parser("sign")
    sg.add_argument("--role", required=True, choices=list(readiness.ROLES))
    sg.add_argument("--reviewer", required=True)
    sg.add_argument("--qualification", required=True)
    sg.add_argument("--organization", required=True)
    sg.add_argument("--result", required=True, choices=["approved", "rejected"])
    sg.add_argument("--scope", required=True)
    sg.add_argument("--condition", action="append")
    sg.add_argument("--document")
    args = parser.parse_args(argv)
    if args.cmd == "export":
        for p in export():
            print(f"wrote {p}")
    elif args.cmd == "import":
        saved = import_sheet(
            args.file, reviewer=args.reviewer, qualification=args.qualification, kind=args.kind
        )
        print(f"recorded {saved}")
    elif args.cmd == "summarize":
        print(json.dumps(summarize(), indent=2, ensure_ascii=False))
    else:
        print(f"recorded in {sign(args)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
