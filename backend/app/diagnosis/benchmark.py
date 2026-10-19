"""Measures a diagnosis provider against labeled scenarios.

    backend/.venv/bin/python -m app.diagnosis.benchmark --set dev --provider rules
    backend/.venv/bin/python -m app.diagnosis.benchmark --set heldout --provider rules

The data is author-labeled (see benchmarks/diagnosis/README.md). The numbers say whether a provider
agrees with those labels and whether it has regressed since a stored baseline; they are not a
claim about accuracy on real websites. Targets are reported as measured, never adjusted.
"""

import argparse
import asyncio
import hashlib
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import Settings
from app.diagnosis.anthropic_provider import AnthropicProvider, ProviderError
from app.diagnosis.pipeline import DiagnosisProvider
from app.diagnosis.rules import RulesProvider
from app.diagnosis.schemas import DiagnosisInput, IssueDraft
from app.diagnosis.validate import validate_output

ROOT = Path(__file__).resolve().parents[3] / "benchmarks" / "diagnosis"
_ORDER = ["info", "low", "medium", "high", "critical"]
TARGETS = {
    "issue_precision": 0.90,
    "recall_high_severity": 0.85,
    "evidence_coverage": 0.95,
    "regression_rate_max": 0.05,
}


@dataclass
class ScenarioResult:
    id: str
    tags: list[str]
    passed: bool
    expected: int
    produced: int
    matched: int
    false_positives: int
    cited: int
    cited_valid: int
    problems: list[str] = field(default_factory=list)


def load_scenarios(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def dataset_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _count_citations(output_issues: list[IssueDraft], known: set[str]) -> tuple[int, int]:
    total = valid = 0
    for issue in output_issues:
        ids = list(issue.evidence_ids)
        for fact in issue.facts:
            ids += fact.evidence_ids
        for hyp in issue.hypotheses:
            ids += hyp.evidence_ids
        total += len(ids)
        valid += sum(1 for i in ids if i in known)
    return total, valid


def _matches(expected: dict[str, Any], issue: IssueDraft) -> tuple[bool, str | None]:
    if issue.category != expected["category"]:
        return False, None
    if not set(expected["cites"]) & set(issue.evidence_ids):
        return False, None
    if _ORDER.index(issue.severity) < _ORDER.index(expected["severity_min"]):
        return False, f"severity {issue.severity} below {expected['severity_min']}"
    if issue.classification != expected["classification"]:
        return (
            False,
            f"classification {issue.classification}, expected {expected['classification']}",
        )
    if expected.get("no_hypotheses") and issue.hypotheses:
        return False, "stated a cause where none is supported"
    return True, None


async def score_scenario(provider: DiagnosisProvider, scenario: dict[str, Any]) -> ScenarioResult:
    inp = DiagnosisInput.model_validate(scenario["input"])
    result = ScenarioResult(
        scenario["id"], scenario["tags"], False, len(scenario["expected"]), 0, 0, 0, 0, 0
    )
    try:
        provided = await provider.diagnose(inp)
    except ProviderError as exc:
        result.problems.append(f"provider error: {exc.code}")
        return result
    known = {e.id for e in inp.evidence}
    result.cited, result.cited_valid = _count_citations(provided.output.issues, known)
    produced = validate_output(provided.output, inp).issues
    result.produced = len(produced)
    unused = list(range(len(produced)))
    for expected in scenario["expected"]:
        hit = None
        near: str | None = None
        for index in unused:
            ok, why = _matches(expected, produced[index])
            if ok:
                hit = index
                break
            near = near or why
        if hit is None:
            result.problems.append(near or f"missing {expected['category']} issue")
        else:
            unused.remove(hit)
            result.matched += 1
    result.false_positives = len(unused)
    for index in unused:
        result.problems.append(
            f"unexpected {produced[index].category} issue: {produced[index].title[:60]}"
        )
    result.passed = result.matched == result.expected and result.false_positives == 0
    return result


def _ratio(num: int, den: int, empty: float = 1.0) -> float:
    return round(num / den, 4) if den else empty


def summarize(results: list[ScenarioResult], scenarios: list[dict[str, Any]]) -> dict[str, Any]:
    produced = sum(r.produced for r in results)
    matched = sum(r.matched for r in results)
    expected_high = high_matched = 0
    by_severity: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    needs_review_expected = needs_review_right = 0
    by_tag: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    by_id = {r.id: r for r in results}
    for sc in scenarios:
        r = by_id[sc["id"]]
        # Which expected issues were matched is not stored per issue, so severity recall is
        # computed from scenarios whose only expectations are of that severity or per-scenario pass.
        for exp in sc["expected"]:
            sev = exp["severity_min"]
            by_severity[sev][1] += 1
            by_severity[sev][0] += 1 if r.passed or r.matched == r.expected else 0
            if sev in ("high", "critical"):
                expected_high += 1
                high_matched += 1 if r.matched == r.expected else 0
            if exp["classification"] == "needs_review":
                needs_review_expected += 1
                needs_review_right += 1 if r.matched == r.expected else 0
        for tag in sc["tags"]:
            by_tag[tag]["scenarios"] += 1
            by_tag[tag]["passed"] += 1 if r.passed else 0
            by_tag[tag]["expected"] += r.expected
            by_tag[tag]["matched"] += r.matched
            by_tag[tag]["produced"] += r.produced
    clean = [r for r in results if r.expected == 0]
    metrics = {
        "scenarios": len(results),
        "scenarios_passed": sum(r.passed for r in results),
        "issue_precision": _ratio(matched, produced),
        "recall_all": _ratio(matched, sum(r.expected for r in results)),
        "recall_high_severity": _ratio(high_matched, expected_high),
        "evidence_coverage": _ratio(
            sum(r.cited_valid for r in results), sum(r.cited for r in results)
        ),
        "abstention_accuracy": _ratio(needs_review_right, needs_review_expected),
        "clean_scenarios": len(clean),
        "clean_false_positive_scenarios": sum(1 for r in clean if r.produced > 0),
    }
    return {
        "metrics": metrics,
        "by_tag": {
            tag: {
                "scenarios": c["scenarios"],
                "passed": c["passed"],
                "precision": _ratio(c["matched"], c["produced"]),
                "recall": _ratio(c["matched"], c["expected"]),
            }
            for tag, c in sorted(by_tag.items())
        },
        "by_expected_severity": {
            sev: {"expected": den, "scenario_level_matched": num, "recall": _ratio(num, den)}
            for sev, (num, den) in sorted(by_severity.items(), key=lambda kv: _ORDER.index(kv[0]))
        },
        "failures": [{"id": r.id, "problems": r.problems} for r in results if not r.passed],
        "per_scenario": {r.id: r.passed for r in results},
    }


def regression_rate(
    per_scenario: dict[str, bool], baseline: dict[str, bool]
) -> tuple[float, list[str]]:
    """Share of scenarios that passed in the baseline and fail now."""
    was_passing = [i for i, ok in baseline.items() if ok]
    broke = [i for i in was_passing if not per_scenario.get(i, False)]
    return _ratio(len(broke), len(was_passing), 0.0), broke


async def run_benchmark(
    provider: DiagnosisProvider,
    scenarios: list[dict[str, Any]],
    baseline: dict[str, bool] | None = None,
) -> dict[str, Any]:
    results = [await score_scenario(provider, sc) for sc in scenarios]
    report = summarize(results, scenarios)
    metrics = report["metrics"]
    if baseline is not None:
        rate, broke = regression_rate(report["per_scenario"], baseline)
        metrics["regression_rate"] = rate
        report["regressions"] = broke
    report["targets"] = {
        name: {
            "target": value,
            "actual": metrics.get(name if name != "regression_rate_max" else "regression_rate"),
            "met": None
            if (name == "regression_rate_max" and baseline is None)
            else (
                metrics["regression_rate"] <= value
                if name == "regression_rate_max"
                else metrics[name] >= value
            ),
        }
        for name, value in TARGETS.items()
    }
    return report


def _provider(name: str) -> DiagnosisProvider:
    if name == "rules":
        return RulesProvider()
    settings = Settings()
    return AnthropicProvider(settings)


def baseline_path(set_name: str, provider: str) -> Path:
    return ROOT / f"baseline-{set_name}-{provider}.json"


async def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--set", choices=["dev", "heldout", "both"], default="both")
    parser.add_argument("--provider", choices=["rules", "anthropic"], default="rules")
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "results")
    parser.add_argument("--fail-on-regression", action="store_true")
    args = parser.parse_args(argv)

    exit_code = 0
    provider = _provider(args.provider)
    for set_name in ("dev", "heldout") if args.set == "both" else (args.set,):
        path = ROOT / "v1" / f"{set_name}.jsonl"
        scenarios = load_scenarios(path)
        base_file = baseline_path(set_name, args.provider)
        baseline = json.loads(base_file.read_text())["per_scenario"] if base_file.exists() else None
        report = await run_benchmark(provider, scenarios, baseline)
        report["meta"] = {
            "set": set_name,
            "provider": provider.name,
            "model": provider.model,
            "config_version": provider.config_version,
            "dataset": path.name,
            "dataset_sha256": dataset_hash(path),
            "scenarios": len(scenarios),
            "label_source": "author (not independently reviewed)",
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        args.out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        (args.out_dir / f"{set_name}-{args.provider}-{stamp}.json").write_text(
            json.dumps(report, indent=2, sort_keys=True)
        )
        (args.out_dir / f"latest-{set_name}-{args.provider}.json").write_text(
            json.dumps(report, indent=2, sort_keys=True)
        )
        if args.write_baseline:
            base_file.write_text(
                json.dumps(
                    {
                        "per_scenario": report["per_scenario"],
                        "dataset_sha256": report["meta"]["dataset_sha256"],
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        m = report["metrics"]
        print(f"\n== {set_name} ({len(scenarios)} scenarios, provider={provider.name}) ==")
        print(f"  scenarios passed        {m['scenarios_passed']}/{m['scenarios']}")
        for name, info in report["targets"].items():
            actual = info["actual"]
            verdict = (
                "n/a (no baseline)"
                if info["met"] is None
                else ("MET" if info["met"] else "NOT MET")
            )
            shown = actual if actual is not None else "-"
            print(f"  {name:<22} {shown!s:<8} target {info['target']:<5} {verdict}")
        print(f"  abstention accuracy     {m['abstention_accuracy']}")
        clean_ok = m["clean_scenarios"] - m["clean_false_positive_scenarios"]
        print(
            f"  clean scenarios         {clean_ok}/{m['clean_scenarios']} without false positives"
        )
        for f in report["failures"][:15]:
            print(f"  FAIL {f['id']}: {'; '.join(f['problems'])[:150]}")
        if args.fail_on_regression and report.get("regressions"):
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(asyncio.run(_main(sys.argv[1:])))
