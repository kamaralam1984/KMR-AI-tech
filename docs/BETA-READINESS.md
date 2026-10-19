# KMR AI beta readiness report

Date of the runs below: 2026-10-18/19, on the development machine (Linux, local Docker). Every number
comes from a command that was actually run; nothing here is an estimate. No staging or production
environment exists, and nothing was deployed.

## Verdict

**Ready for a small, supervised beta on a staging environment, for the built-in-rules diagnosis and
local-git repositories. Not ready for production, and not ready to claim AI diagnosis or GitHub
integration works**, because neither has ever run against the real service (no credentials existed).

No mandatory release blocker was found by the tests that exist (list below). That is not proof that
none exists: it means the checks written for each blocker pass.

## What was run, and what happened

| Check                                              | Command                                                                                                                            | Result                                                                                                                                 |
| -------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| Backend lint, format, types                        | `ruff check`, `ruff format --check`, `mypy app tests`                                                                              | clean (117 source files)                                                                                                               |
| Backend tests                                      | `pytest` (real Postgres per run, real Chrome, real Docker sandbox)                                                                 | **524 passed**                                                                                                                         |
| Frontend lint, types, format                       | `eslint`, `tsc`, `prettier --check`                                                                                                | clean                                                                                                                                  |
| Frontend tests                                     | `vitest`                                                                                                                           | **136 passed**                                                                                                                         |
| Production build                                   | `pnpm build`                                                                                                                       | succeeded (all routes)                                                                                                                 |
| Browser E2E (phone size, real scan of example.com) | `make e2e`                                                                                                                         | **22 steps passed**                                                                                                                    |
| Responsive smoke                                   | `node frontend/scripts/responsive-smoke.mjs`                                                                                       | 48 page/viewport combinations, 96 checks, no overflow, clipped headings or console errors                                              |
| Accessibility                                      | `node frontend/scripts/a11y-audit.mjs` (axe-core 4.14, WCAG 2.0/2.1/2.2 A+AA, phone and laptop, every page) + keyboard focus check | no serious or critical violations; every focus stop has a visible indicator                                                            |
| Real sandbox                                       | `make scanner-check`                                                                                                               | all pass, **1 warning** (see limits)                                                                                                   |
| Dependency vulnerabilities                         | `pip-audit`, `pnpm audit --prod`                                                                                                   | no known vulnerabilities                                                                                                               |
| Backup and restore                                 | `scripts/backup.sh`, `scripts/restore-check.sh`                                                                                    | restored into a scratch database; migration head and every compared table matched the live one; scratch dropped                        |
| Controlled load                                    | `scripts/load-check.py`, 300 requests x 3 endpoints, concurrency 20, one dev API process                                           | 900 requests, 0 errors. p95: /health 133 ms, projects list 226 ms, dashboard 408 ms. 5 bad logins allowed, then 429                    |
| Secrets                                            | pattern scan of every file outside caches; `.env` is git-ignored                                                                   | only documentation-style fake keys in test fixtures. The git repository has no commits, so there is no history to leak from            |
| Security headers                                   | `curl -I` on API and web                                                                                                           | CSP, nosniff, frame denial, referrer and permissions policy, no `X-Powered-By`; HSTS when `KMR_APP_ENV` is staging/production (tested) |

Mutation checks were used throughout: a safety rule was deliberately broken, the suite had to fail,
then the code was restored and compared byte for byte. Where a rule survived, a test was added
(three survivors in phase 7 led to new tests, including a race test for the one-time approval claim,
and an earlier set of "role" tests turned out to be passing for the wrong reason, a missing CSRF
token, and were rewritten).

## Release blockers from CLAUDE.md

| Blocker                                       | Evidence                                                                                                                                                                                                                                                          |
| --------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Cross-tenant data access                      | Cross-tenant read/write/guessed-UUID tests on every module, forced Row-Level Security tested directly in Postgres as the restricted role                                                                                                                          |
| Approval bypass or unauthorized merge/deploy  | Approval bound to a hash of repository, base commit, branch, issues, diff and action; 8 mutations of the approval rules caught; a test lists every route and fails if any merge/deploy/migrate/force/delete path exists; the app role cannot change bound columns |
| Confirmed secret leak                         | Redaction tests for logs, evidence, API-check credentials and repository tokens; files containing secrets never reach a model; proposals that add a credential are refused                                                                                        |
| Scan bypassing target restrictions            | SSRF/policy tests plus the real sandbox and proxy check (metadata, loopback, private and link-local targets refused)                                                                                                                                              |
| Destructive action enabled by default         | There is no destructive endpoint; apply only creates a new branch and never overwrites one (tested)                                                                                                                                                               |
| Falsely reported passing test or verified fix | Check statuses are Passed/Failed/Skipped/Unavailable and never promoted; Fixed requires scan completed, issue gone, pages re-tested and no new material issue; each rule is mutation-checked                                                                      |
| Critical unresolved security issue            | None known                                                                                                                                                                                                                                                        |

## Diagnosis accuracy (rules provider only)

Measured with `python -m app.diagnosis.benchmark` (details and caveats in
`benchmarks/diagnosis/README.md`):

| Set           | Scenarios | Passed | Issue precision | Recall (high/critical) | Evidence coverage | Regression rate |
| ------------- | --------- | ------ | --------------- | ---------------------- | ----------------- | --------------- |
| Visible (dev) | 144       | 143    | 0.9926          | 1.0                    | 1.0               | 0.0             |
| Held-out      | 55        | 55     | 1.0             | 1.0                    | 1.0               | 0.0             |

**These numbers must not be read as the product's real accuracy.** The scenarios and their labels
were written by the same author as the rules, they were not human-reviewed (the requirement for 100
human-reviewed scenarios is **not met**), the held-out set is small and was built after the rules were
tuned on the visible set, and the rules were improved between a first run (visible: 134 of 144,
precision 0.8951, below target) and the one above. No AI model has been measured at all.

## Known limitations

- **AI provider unverified.** The Anthropic adapter is tested only against a fake HTTP transport.
  Diagnosis and repair proposals use built-in rules by default and say so on screen.
- **GitHub unverified.** The adapter is tested against a stateful fake. It uses a personal access
  token, not a GitHub App with installation checks.
- **Checks are narrow.** Built-in checks are file syntax (JSON, Python, HTML) and `node --check`; the
  project's own tests run only if an admin configures one command. There is no automatic detection of
  a repository's lint, type-check, build or test commands.
- **Verification is a re-scan, not a replay.** It does not re-run recorded reproduction steps or a
  Playwright journey; it compares a newer scan with the original.
- **No browser E2E** for the repair, approval and verification screens (API and component tests
  cover them).
- **Sandbox host gateway.** Docker internal networks still let the browser container reach the host's
  bridge address (reported as a warning). All browser traffic goes through the egress proxy, so this
  matters only if the browser were compromised. `scripts/scanner-firewall.sh` closes it but was never
  run. Chromium also runs without its own sandbox inside the container.
- **No authenticated scanning**, no email flows (verification, reset, invitations), no two-step login,
  no retention policy for scans and evidence.
- **Single process.** One API and one worker were tested; no horizontal scaling or long soak test.
- **No metrics endpoint or alerting.** Health, readiness, a per-organization worker view, structured
  logs and the audit chain exist (see `docs/OPERATIONS.md`).
- **Backups** were restored on the same host only; evidence-archive restore and point-in-time recovery
  were not tested.
- **Homepage images** are drawn artwork and real product screenshots. No stock photography ships; add
  your own licensed photos under `frontend/public/images/` (see README).

## Changes the final review made

- Added the missing security headers on both the API and the web app.
- Tightened the database grant on repair plans (only `status` can change; migration 0008) after the
  permission review.
- Fixed dashboard tile and inline-link touch targets, and a definition-list and scroll-region
  accessibility problem that axe found.
- Added a "Before you approve" summary (files, checks, effect, rollback) and the explicit "Would you
  like KMR AI to plan a fix?" question, both required by CLAUDE.md.
- Extended the test-data cleaner to the new audit and load scripts.

## Before any staging deployment

Needs explicit authorization. Follow `docs/OPERATIONS.md`. Suggested order: rotate to fresh secrets,
run the whole flow on a throwaway repository, try the AI provider and GitHub adapter against test
accounts (and re-measure the benchmark with the AI provider and a human-reviewed dataset), run the
firewall script on the sandbox host, then decide.
