import json
from pathlib import Path

import pytest

from app.diagnosis.benchmark import (
    ROOT,
    baseline_path,
    load_scenarios,
    regression_rate,
    run_benchmark,
    score_scenario,
    summarize,
)
from app.diagnosis.rules import RulesProvider
from app.diagnosis.schemas import (
    DiagnosisInput,
    DiagnosisOutput,
    Fact,
    IssueDraft,
    ProviderResult,
    Usage,
)

pytestmark = pytest.mark.anyio
REQUIRED_TAGS = {
    "frontend",
    "backend_api",
    "auth",
    "validation",
    "database",
    "responsive",
    "accessibility",
    "clean",
    "ambiguous",
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def sets() -> dict[str, list[dict]]:  # type: ignore[type-arg]
    return {name: load_scenarios(ROOT / "v1" / f"{name}.jsonl") for name in ("dev", "heldout")}


def test_the_dataset_is_large_diverse_and_well_formed() -> None:
    data = sets()
    assert len(data["dev"]) >= 100 and len(data["heldout"]) >= 30
    ids = [s["id"] for s in data["dev"] + data["heldout"]]
    assert len(ids) == len(set(ids)), "scenario ids must be unique across both sets"
    for name, scenarios in data.items():
        tags = {t for s in scenarios for t in s["tags"]}
        assert REQUIRED_TAGS <= tags or name == "heldout", (
            f"{name} lacks tags {REQUIRED_TAGS - tags}"
        )
        for s in scenarios:
            inp = DiagnosisInput.model_validate(s["input"])
            known = {e.id for e in inp.evidence}
            assert s["label_source"] == "author"  # never claimed to be anything else
            for expected in s["expected"]:
                assert set(expected["cites"]) <= known, (
                    f"{s['id']} expects evidence that is not in its input"
                )
            for c in inp.correlations:
                assert set(c.evidence_ids) <= known


def _content_key(scenario: dict) -> str:  # type: ignore[type-arg]
    """What a scenario says, ignoring its ids, so a renamed copy still counts as a copy."""
    items = sorted(
        json.dumps([e["kind"], e["severity"], e["page_url"], e["data"]], sort_keys=True)
        for e in scenario["input"]["evidence"]
    )
    return json.dumps(items)


def test_heldout_contains_no_copy_of_a_dev_scenario() -> None:
    data = sets()
    dev_content = {_content_key(s) for s in data["dev"]}
    copies = [s["id"] for s in data["heldout"] if _content_key(s) in dev_content]
    assert copies == [], f"held-out scenarios that are copies of dev ones: {copies}"


async def test_the_benchmark_is_reproducible() -> None:
    scenarios = sets()["dev"]
    first = await run_benchmark(RulesProvider(), scenarios)
    second = await run_benchmark(RulesProvider(), scenarios)
    assert first == second


async def test_the_stored_baseline_still_holds_for_both_sets() -> None:
    for name, scenarios in sets().items():
        base = baseline_path(name, "rules")
        assert base.exists(), f"missing {base.name}: run the benchmark with --write-baseline"
        baseline = json.loads(Path(base).read_text())["per_scenario"]
        report = await run_benchmark(RulesProvider(), scenarios, baseline)
        assert report["metrics"]["regression_rate"] <= 0.05, report["regressions"]


class _Silent:
    name, model, config_version = "silent", "silent", "1"

    async def diagnose(self, inp: DiagnosisInput) -> ProviderResult:
        return ProviderResult(output=DiagnosisOutput(issues=[]), usage=Usage(), model="silent")


class _Inventor:
    name, model, config_version = "inventor", "inventor", "1"

    async def diagnose(self, inp: DiagnosisInput) -> ProviderResult:
        ghost = "00000000-0000-4000-8000-000000000000"
        issue = IssueDraft(
            title="Invented",
            category="backend_api",
            severity="critical",
            confidence="high",
            classification="diagnosed",
            evidence_ids=[ghost],
            facts=[Fact(text="made up", evidence_ids=[ghost])],
        )
        return ProviderResult(
            output=DiagnosisOutput(issues=[issue]), usage=Usage(), model="inventor"
        )


async def test_the_metrics_punish_silence_and_invention() -> None:
    scenarios = sets()["dev"]
    silent = (await run_benchmark(_Silent(), scenarios))["metrics"]
    assert (
        silent["recall_high_severity"] == 0 and silent["issue_precision"] == 1.0
    )  # nothing said, nothing wrong
    assert silent["scenarios_passed"] == silent["clean_scenarios"]  # only the clean scenarios pass
    invented = await run_benchmark(_Inventor(), scenarios)
    assert invented["metrics"]["evidence_coverage"] == 0.0  # every citation was fake
    assert invented["metrics"]["recall_high_severity"] == 0  # none of it was real, so none counts
    assert invented["metrics"]["scenarios_passed"] == invented["metrics"]["clean_scenarios"]


async def test_a_stated_cause_where_none_is_supported_fails_the_scenario() -> None:
    scenario = next(s for s in sets()["dev"] if s["id"] == "dev-nodata-00")
    result = await score_scenario(RulesProvider(), scenario)
    assert result.passed
    exp = scenario["expected"][0]
    assert exp["no_hypotheses"] is True and exp["classification"] == "needs_review"


def test_regression_rate_counts_only_what_used_to_pass() -> None:
    rate, broke = regression_rate(
        {"a": True, "b": False, "c": False}, {"a": True, "b": True, "c": False}
    )
    assert broke == ["b"] and rate == 0.5
    assert regression_rate({"a": True}, {"a": False})[0] == 0.0


def test_summaries_break_results_down_by_tag() -> None:
    from app.diagnosis.benchmark import ScenarioResult

    scenarios = [
        {
            "id": "x",
            "tags": ["frontend"],
            "expected": [
                {
                    "category": "frontend_error",
                    "severity_min": "high",
                    "classification": "diagnosed",
                    "cites": [],
                }
            ],
        }
    ]
    report = summarize([ScenarioResult("x", ["frontend"], True, 1, 1, 1, 0, 1, 1)], scenarios)
    assert report["by_tag"]["frontend"] == {
        "scenarios": 1,
        "passed": 1,
        "precision": 1.0,
        "recall": 1.0,
    }
    assert report["metrics"]["recall_high_severity"] == 1.0
