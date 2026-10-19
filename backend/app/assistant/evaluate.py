"""Runs benchmarks/learning/cases.json against the solver and reports accuracy by category.

    python -m app.assistant.evaluate [--fail-under 1.0]

The cases are author-written and not teacher-reviewed (see benchmarks/learning/README.md), so a
perfect score here is a regression guard, not a claim about educational quality.
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.assistant.learning import LearningResult, solve

CASES = Path(__file__).resolve().parents[3] / "benchmarks" / "learning" / "cases.json"


def judge(case: dict[str, Any], result: LearningResult) -> list[str]:
    """Returns the reasons a case failed (empty means it passed)."""
    want = case["expect"]
    failures: list[str] = []
    if "kind" in want and result.kind != want["kind"]:
        failures.append(f"kind {result.kind!r} != {want['kind']!r}")
    if "result" in want and result.result != want["result"]:
        failures.append(f"result {result.result!r} != {want['result']!r}")
    if "result_prefix" in want and not (result.result or "").startswith(want["result_prefix"]):
        failures.append(f"result {result.result!r} does not start with {want['result_prefix']!r}")
    if want.get("no_result") and result.result is not None:
        failures.append(f"unexpected result {result.result!r}")
    if "verdict" in want and result.verdict != want["verdict"]:
        failures.append(f"verdict {result.verdict!r} != {want['verdict']!r}")
    if "options" in want and len(result.options) != want["options"]:
        failures.append(f"{len(result.options)} options != {want['options']}")
    if "language" in want and result.language != want["language"]:
        failures.append(f"language {result.language!r} != {want['language']!r}")
    blob = json.dumps(result.as_dict(), ensure_ascii=False)
    for banned in want.get("must_not_contain", []):
        if banned in blob:
            failures.append(f"output contains {banned!r}")
    return failures


def evaluate(path: Path = CASES) -> dict[str, Any]:
    cases = json.loads(path.read_text())
    per_category: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    failed: list[dict[str, Any]] = []
    for case in cases:
        result = solve(case["question"], band=case["age_band"])
        reasons = judge(case, result)
        per_category[case["category"]][1] += 1
        if reasons:
            failed.append({"id": case["id"], "question": case["question"], "reasons": reasons})
        else:
            per_category[case["category"]][0] += 1
    total = len(cases)
    passed = total - len(failed)
    return {
        "total": total,
        "passed": passed,
        "accuracy": round(passed / total, 4) if total else 0.0,
        "by_category": {
            k: {"passed": v[0], "total": v[1]} for k, v in sorted(per_category.items())
        },
        "failed": failed,
        "reviewed_by_humans": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fail-under", type=float, default=1.0)
    args = parser.parse_args()
    report = evaluate()
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["accuracy"] >= args.fail_under else 1


if __name__ == "__main__":
    sys.exit(main())
