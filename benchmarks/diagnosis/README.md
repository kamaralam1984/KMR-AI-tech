# Diagnosis benchmark

Scenarios for measuring a diagnosis provider (the built-in rules, or an AI model through an
adapter). Each scenario is a set of scan evidence plus the issues a careful reviewer would expect.

```bash
backend/.venv/bin/python -m app.diagnosis.benchmark --set both --provider rules   # from backend/
make benchmark
```

Files: `v1/dev.jsonl` (used while building the rules), `v1/heldout.jsonl` (kept aside),
`generate.py` (rebuilds both deterministically), `baseline-*.json` (per-scenario pass/fail used to
detect regressions), `results/` (every run, plus `latest-*`).

## Read this before trusting any number

- **The labels were written by the same author as the built-in rules, and nobody independently
  reviewed them.** The numbers measure agreement with those labels and catch regressions. They do
  **not** measure accuracy on real websites.
- **The held-out set is only a weak guard.** It uses different paths, wording and mixes, but it was
  written by the same person at the same time, so it shares that person's assumptions. Treat a
  perfect held-out score as "no obvious over-fitting to the dev set", not as proof.
- **No AI model has been measured.** No provider key was available, so the `anthropic` provider is
  tested only against a fake HTTP transport. Its quality is unknown until `--provider anthropic`
  is run with a real key.
- The task asks for 100 scenarios reviewed by humans. The set has 144 dev and 55 held-out
  scenarios, **none human-reviewed**. That requirement is not met.
- Verification-result correctness (a target in the plan) belongs to Phase 8 and is measured there.

## What was measured, honestly

Targets: issue precision >= 0.90, recall of high/critical defects >= 0.85, valid evidence
coverage >= 0.95, regression rate <= 0.05.

| Run | Scenarios passed | Precision | Recall (high+) | Evidence coverage |
|---|---|---|---|---|
| rules-1, dev (before improvements) | 134/144 | 0.8951 (not met) | 0.9259 | 1.0 |
| rules-1, held-out (before; an earlier held-out set, see note) | 51/55 | 0.9245 | 0.9412 | 1.0 |
| rules-2, dev (after) | 143/144 | 0.9926 | 1.0 | 1.0 |
| rules-2, held-out (after) | 55/55 | 1.0 | 1.0 | 1.0 |

The first version of the benchmark had only the easy families and the rules scored 100%, which was
a sign it measured nothing (the rules and the scenarios came from the same head). The harder
families (browser policy errors such as CORS/CSP/mixed content/certificates, one error on many
pages, mixed multi-cause scans) were added with their labels written first. rules-1 failed 10 dev
and 4 held-out scenarios on them. rules-2 was then improved for those known patterns, *using the
dev failures*, so the dev score is no longer an independent measure; the held-out score is the
only one that was not looked at while fixing, and it has the weaknesses above.

Note on the held-out rows: a review found that the first held-out set contained verbatim copies of
four dev scenarios (the same generator was run for both sets). Those were replaced with distinct
scenarios and a test now fails if any held-out scenario repeats dev content. The rules-1 held-out
row was measured on the earlier, partly copied set and was not re-measured (rules-1 no longer
exists); the rules-2 held-out row is on the corrected set.

Known remaining miss (dev-policy-02): a mixed-content warning that names a `.js` file without the
word "script" is rated `low` instead of `medium`.

Targets that cannot be judged from this data: anything about real-world accuracy, and AI quality.
