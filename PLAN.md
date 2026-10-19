# KMR AI — Implementation Plan

Source of requirements: `CLAUDE.md` (master build prompt). This file is the working plan: phase order, Phase 0 findings, open decisions, and the gaps found in review.
Rule for every phase: plan -> build -> verify -> report -> **stop and wait for the owner's "go"**.

## Progress

| Phase                            | Status                     | Notes                                                                                                                                                                                                                                                                                                                                                                                              |
| -------------------------------- | -------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 0 Assessment                     | Done                       | Repo was empty; see section 4.                                                                                                                                                                                                                                                                                                                                                                     |
| 1 Foundation and shell           | Passed                     | Backend, frontend shell, Docker Compose, checks.                                                                                                                                                                                                                                                                                                                                                   |
| 2 Auth, orgs, isolation          | Passed                     | Gate: 86 backend tests incl. cross-tenant, RLS, role and session tests; phone-size browser E2E passes.                                                                                                                                                                                                                                                                                             |
| 3 Projects and safe scanner      | Passed                     | Gate: SSRF/policy/proxy tests with mutation checks, RLS and queue tests, 15 scanner and 9 worker tests against real Chrome, `make scanner-check` against the real sandbox, phone-size browser E2E including a real scan.                                                                                                                                                                           |
| 4 Backend/API evidence           | Passed                     | Gate: API checks (host-locked, via egress proxy, encrypted write-only credentials), X-Request-ID correlation, customer log upload, correlation that never claims a root cause, honest source states; mutation-checked; browser E2E incl. real API checks.                                                                                                                                          |
| 5 AI diagnosis                   | Passed with limits         | Gate: provider adapter + built-in rules (labeled "not AI"), validator that drops uncited claims (mutation-checked), quotas/cost ceiling, Issues UI with Observed/Inferred/Unverified labels. Limits: no AI key, so the Anthropic adapter is tested only against a fake transport; benchmark labels are author-written, not human-reviewed (dev 143/144, held-out 55/55, held-out is a weak guard). |
| 6 Repository and isolated repair | Passed with limits         | Gate: patch policy + confined local-git adapter + GitHub adapter (fake-tested only); never touches the default branch or working tree; Docker sandbox verified for real (no network, read-only, unprivileged); mutation-checked.                                                                                                                                                                   |
| 7 Checks, approvals, audit       | Passed                     | Gate: approvals bound to the diff hash, expiry/revoke/invalidate, apply re-verification, one-time claim (race tested), hash-chained audit with tamper detection, no merge/deploy route; 8 mutation checks caught.                                                                                                                                                                                  |
| 8 Post-fix verification          | Passed                     | Gate: Fixed only when completed + gone + pages re-tested + no new material issue; Not Fixed / Needs Review otherwise; verified diff hash recorded; mutation-checked.                                                                                                                                                                                                                               |
| 9 Dashboard, history, operations | Passed with limits         | Gate: real counts verified against tables, usage/worker views, audit page with chain status, backup + restore-check run on the dev DB. Limits: no metrics endpoint/alerting.                                                                                                                                                                                                                       |
| 10 Hardening and beta readiness  | See docs/BETA-READINESS.md | Security headers, dependency audit, load check, axe audit, secrets scan, final suites.                                                                                                                                                                                                                                                                                                             |
| Redesign                         | Done                       | Luxury dark/gold theme, animated homepage with real product screenshots.                                                                                                                                                                                                                                                                                                                           |

Decisions made for Phase 3 (owner-confirmed): ownership by DNS TXT or file, Docker sandbox with an
egress proxy, local fixture site for tests (loopback-only exception, tests only).

Decisions made for Phase 2 (owner-confirmed): self-built auth on maintained libraries (Argon2id,
server-side sessions, CSRF), no email verification or password reset yet, local Docker Postgres
(Neon later). Deviations from the original plan: the Projects page is live in Phase 2 with names only
(URL and scanning stay in Phase 3), and the mobile experience (bottom tab bar, bottom sheets,
installable manifest, no offline) was added on the owner's request. Known gaps are listed in
`README.md`.

## 1. Goal

A multi-tenant SaaS that scans an authorized website, finds real frontend/backend issues with evidence, proposes a fix on an isolated branch, tests it in a sandbox, and verifies after an explicitly approved change.
Safety, evidence-based accuracy and data protection come before speed and feature count.

Flow for every module: **Scan (read-only) -> Highlight -> Ask -> Fix (approved scope only) -> Verify.**

## 2. Scope

**MVP:** auth + organizations, projects, safe Playwright scanning, evidence-backed findings, AI diagnosis, GitHub connection, repair proposals on isolated branches, sandboxed checks, diff-hash-bound approval, post-fix retest, audit, usage/cost limits.

**Not in MVP:** desktop/Android agents, auto production repairs, predictive monitoring, white-label/agency, affiliate, subscription billing, enterprise features.

**Post-MVP (separate threat models):** Exam Form Assistant, Child Learning & Question Support.

## 3. Stack (to confirm before Phase 1)

| Area      | Choice                                                                                                            |
| --------- | ----------------------------------------------------------------------------------------------------------------- |
| Frontend  | Next.js App Router, React, TypeScript strict, Tailwind, Framer Motion (sparingly), lucide-react                   |
| API       | Python 3.11+ (prefer 3.12/3.13, see Phase 0), FastAPI, Pydantic                                                   |
| DB        | PostgreSQL on Neon (managed, cloud), explicit migrations, Row-Level Security. Local Docker Postgres for dev/tests |
| Jobs      | Redis + dedicated worker                                                                                          |
| Browser   | Playwright behind an egress proxy                                                                                 |
| Tests     | Pytest, API/integration, Playwright E2E, frontend tests                                                           |
| Local env | Docker Compose                                                                                                    |
| AI        | One provider adapter, model IDs in validated config                                                               |

## 4. Phase 0 findings (done, read-only)

**Repository:** only `CLAUDE.md` (37 KB). No code, package files, tests, `.env` or git history. Not a git repo. Treat as new; nothing to preserve. No secrets seen in the prompt text.

**Machine (versions only, nothing run):** git 2.53, node 24.19, npm 11.17, pnpm 11.22, python 3.14.4, docker 29.1.3, compose 5.5.1, psql 18.6, redis 8.0.5.

**Notes:**

- Python 3.14 is very new; some packages may lack wheels. Use a 3.12/3.13 virtualenv.
- Disk is 88% used (29 GB free) and about 5.7 GB RAM was available. Docker images, Playwright browsers and Postgres will be heavy; watch this before Phase 3.
- `CLAUDE.md` is large and loads every session. Consider trimming it and moving phase detail into `docs/`.

## 5. Build order (vertical slice first)

A thin end-to-end slice (scan -> issue -> diagnosis) is shown early so the core idea is validated before the full platform is built. Auth and RLS are not skipped.

### Phase 1 — Foundation and app shell

- `git init`, frontend + backend skeleton, typed config, `.env.example` (placeholders only).
- Health/readiness endpoints, error format, request IDs, structured logging.
- Docker Compose, lint/type-check/test/build commands, dev setup guide.
- Design tokens, core components, responsive shell; scaffolded routes show honest "not yet available" states.
- Gate: app starts locally, baseline checks pass, no secrets tracked.

### Phase 2 — Auth, organizations, tenant isolation

- Maintained auth approach (no custom crypto), orgs, memberships, roles, UUIDs, migrations, RLS + backend checks.
- Rate limiting, session expiry/revocation.
- Neon notes: set the tenant context with `SET LOCAL` inside each transaction (session-level settings leak across clients behind a pooled/transaction-mode connection). Use a separate non-owner DB role for the app so RLS is actually enforced. Connection strings live in env only, never in git.
- Tests: unauthenticated, wrong role, cross-tenant read/write, UUID guessing, job authorization.
- Gate: **all isolation tests pass** or stop.

### Phase 3 — Projects and safe scanner

- Projects CRUD, strict URL validation, scan scope and limits.
- Domain ownership verification (DNS TXT or file) before any scan. _(added in review)_
- SSRF: validate every redirect, re-check resolved IPs, block private/link-local/metadata ranges; run Playwright through an egress proxy that filters every sub-request. _(added)_
- Isolated worker with CPU/memory/time/network limits; capture screenshots, console, failed requests, status codes, DOM/a11y signals.
- Redact before persistence/AI use. Statuses: Queued, Running, Completed, Failed, Cancelled.
- Gate: repeatable scans on authorized public test sites; SSRF and redaction tests pass.

### Phase 4 — Full-stack evidence

- Optional read-only API health checks and customer-supplied logs; correlate by request/correlation ID.
- Label every evidence source: browser-observed, API-observed, repository-derived, customer-supplied.
- Never claim a backend/DB root cause from frontend symptoms alone.
- Gate: findings separate observed facts from inferred causes; missing telemetry is stated.

### Phase 5 — AI diagnosis and evaluation

- Provider adapter, schema-validated output, evidence citations, `Needs Review` abstention, usage quotas and cost ceilings.
- Secret scan on repository content before anything is sent to a model. _(added)_
- Benchmark from seeded-defect test sites (known ground truth) plus owner-reviewed labels; held-out regression set. _(changed)_
- Targets set after a measured baseline. _(changed)_
- Gate: reproducible stored results; prompt-injection tests pass.

### Phase 6 — GitHub connection and isolated repair

- GitHub App with least privilege; verify installation/repo/branch before each action; encrypted tokens.
- One branch/worktree per repair; smallest patch; path/size/scope validation; never touch the default branch.
- Sandbox spec: rootless container with gVisor or Firecracker, no network, read-only FS, resource limits, no production secrets. _(added)_
- Gate: reviewable patch, checks run in isolation, no merge/deploy path exists.

### Phase 7 — Checks, approval gates, audit

- Check statuses: Passed, Failed, Skipped, Unavailable (skipped never becomes passed).
- Approval bound to a hash of repository, branch, base commit SHA, issue IDs, diff, action, approver. _(base SHA added)_
- Expiry, revocation, invalidation on any change, tamper-evident audit log.
- Merge/deploy stay disabled; each consequential action later needs its own approval.
- Gate: all approval-bypass and audit tests pass.

### Phase 8 — Post-fix verification

- Re-run exact repro and Playwright journey, compare before/after, run regression suite, check for new console/network errors.
- Outcome is exactly one of `Fixed`, `Not Fixed`, `Needs Review`; a passing build alone is never `Fixed`.
- Gate: verification reports match actual test output.

### Phase 9 — Dashboard, history, ops

- Real persisted data only; tenant-scoped search/filter/pagination; diff viewer; usage/cost reporting; worker status; backup/restore docs.
- Gate: controlled beta usable end-to-end with no fake data.

### Phase 10 — Hardening and beta readiness

- Full suites, dependency audit, CORS/cookies/CSRF/headers, backup restore in non-prod, permission review, secret-history check, load check, UI audit.
- Staging plan, rollback, incident procedure, beta readiness report.
- Release blockers: cross-tenant access, approval bypass, secret leak, scan target-restriction bypass, destructive default, falsely reported pass/fix, unresolved critical security issue.

### Phase 11 — End-user assistant (post-MVP, only after Phase 10 passes)

A visitor-facing assistant that a company installs on its own website. This is a different surface from the owner-facing scanner, with its own threat model, retention rules, acceptance criteria and tests. Each sub-module is built, gated and approved separately, in this order. Not started until the owner approves it.

**Shared prerequisites (before any sub-module)**

- Embeddable widget with per-tenant install key, allowed-origin list and rate limits.
- Per-tenant knowledge base built only from content the tenant authorizes; strict tenant isolation, including retrieval.
- Visitor privacy: data minimization, redaction in logs/model context, retention and deletion rules, consent text.
- Prompt-injection defenses: page content, uploads and visitor messages are untrusted data.
- Abstain/clarify behavior: say "not found" or ask a question instead of guessing.

**11A — Website guide**

- Answers "what is on this website?" from the tenant's indexed public pages, citing the source page.
- Read-only; takes no action on the site. Replies "not found on this site" when the answer is not in the indexed content.
- Gate: answers match indexed content; injected instructions in pages are ignored; no cross-tenant retrieval.

**11B — Exam/job form assistant**

- First define supported form categories and exclude unsupported or officially restricted workflows.
- Field-by-field guidance; validates required values, formats, dates, document type/size and upload outcomes.
- Draft-fill only from information the user provides; never invents personal details, qualifications, marks or eligibility.
- Review/preview screen. **No auto-submit.** Submission, declaration and payment each need a separate confirmation tied to that exact action.
- Diagnoses form UI/API failures using authorized evidence only; no bypass of CAPTCHA, validation or access controls.
- Document/photo checker: tells the user what is wrong with an upload (type, size, dimensions) and how to fix it. Resizing/conversion runs in the user's browser; the file is not sent to the server or a model.
- Human handoff: when the assistant cannot resolve the issue, it passes the company's support team a summary of the conversation so the user does not repeat themselves. Summary is redacted; the user sees what is shared before it is sent.
- Gate: no final submission or payment can occur without explicit scoped confirmation; sensitive-data leakage and cross-tenant tests pass.

**11C — Child learning and question support**

- Before implementation: define age boundaries, supported languages (Hindi, Hinglish, English), guardian/consent safeguards, and a legal review (for example India's DPDP Act) of child-data handling.
- Typed questions first; image input only after upload and image understanding are implemented and tested.
- Step-by-step explanations grounded in the tenant's own learning content where possible; checks calculations and facts.
- Unclear or ambiguous question: ask for clarification, do not guess. Never claims an answer is guaranteed correct.
- Gate: educational-quality evaluation, privacy/security tests and end-to-end tests pass; not presented as production-ready before that.

### Phase 12 — Owner-side scanner enhancements (post-MVP, after Phase 10 passes)

Extends the Phase 3-9 scanner for site owners. Independent of Phase 11; the owner decides the order. Each item keeps the read-only scan rules and the evidence labels.

**12A — Business-impact ranking** _(built; see `docs/OWNER-SCANNER.md`)_

- Rank findings by how much they hurt critical journeys (checkout, login, signup, payment) before cosmetic issues.
- Without analytics access this is a **labeled heuristic** based on detected page/journey type, not a measured impact. With owner-authorized analytics, traffic can be added as evidence.
- Never show revenue or user-loss numbers that were not measured.
- Gate: ranking reasons are shown per finding; heuristic vs measured is always labeled.

**12B — Developer-ready ticket export** _(built; GitHub issue off by default, unverified against real GitHub)_

- One click turns a finding into a ticket (GitHub Issue, email or PDF) with repro steps, screenshots, evidence and confidence label.
- Redaction applies to the export; the user sees the content before it leaves KMR AI. Creating an external issue needs an explicit user action.
- Gate: exported content matches stored evidence; no secrets or personal data in exports.

**12C — Device and slow-network scan profiles** _(built; Chromium and Firefox verified, WebKit unavailable)_

- Optional scan profiles: small phone viewport, low-end CPU throttle, 3G-like network, and more than one browser engine.
- Reports which findings appear only under a given profile; load-time numbers are measured values from the scan environment, labeled as such.
- Resource and time limits from Phase 3 still apply; more profiles means more cost, so quota-count them.
- Gate: profile results are reproducible; limits and quotas enforced.

### Phase 13 — Live browser agent (owner priority; this is the original product idea)

Owner request: the agent works on whatever website is open in the owner's own Chrome. It reads the whole screen, has its own visible mouse cursor, highlights mistakes on the page, and fixes them only when told. Owner decisions: cloud AI **with local redaction first** (option B); first version is **look and highlight only** (no click, no typing). This overrides the "desktop agents are not in MVP" line in `CLAUDE.md` for this feature, on the owner's explicit instruction. Every safety rule in `CLAUDE.md` still applies.

**Hard rules (owner, non-negotiable)**

1. The agent never enters an OTP, password, card number or any personal value by itself. In this version it does not type at all. When typing is added later, it must ask the user first, every time, per field.
2. No personal information leaves the user's machine to Google or any other platform. Only redacted data may go to the AI provider. If redaction cannot be shown to work for a piece of data, that data is not sent.
3. No fix without an explicit user request. Highlighting is read-only.
4. Failing closed: if the page looks sensitive (login, payment, OTP, banking, mail) or redaction fails, the agent shows "not reading this page" and sends nothing.

**Scope of the first version**

- Chrome extension (Manifest V3): reads the active tab only when the user turns the agent on for that tab. Collects DOM structure, visible text, console errors, failed requests and a screenshot.
- Local redaction step in the extension before anything leaves it: password, OTP, card, email, phone, name/address-like fields and values, tokens in URLs. Screenshots get the same regions blacked out. A "what will be sent" preview is shown on request.
- Per-site block list (default: banks, mail, payment, government login pages) and a visible on/off indicator.
- Page overlay: highlight boxes and notes on the faulty element, and a separate visible agent cursor that moves to the finding. The cursor is drawn by the extension; it does not click.
- Findings use the existing diagnosis pipeline and labels (Observed / Inferred / Unverified, confidence, evidence IDs).
- "Fix this" (on user request only) creates a repair proposal through the existing repair, check, approval and verification flow. Nothing is changed on the live page or in any repository without the existing diff-bound approval.

**Not in the first version**

Click or typing by the agent, form submission, auto-fix, other browsers, mobile.

**Files likely to change or be added**

- New `extension/` (manifest, content script, background worker, overlay, redaction module, tests).
- Backend: an authenticated, tenant-scoped endpoint to receive redacted page evidence; reuse of `app/diagnosis` and `app/repair`.
- Docs: threat model for the extension, redaction coverage list, update `README.md`.

**Risks**

- Redaction is pattern-based and will miss things. Mitigation: allow-list of what is collected rather than a block-list where possible, block list for sensitive sites, preview, and a measured redaction test set whose misses are reported honestly.
- A page can contain text that tries to instruct the agent (prompt injection). Page content is untrusted data; the agent cannot act on it.
- The extension has broad browser permissions. Mitigation: least permissions, activate per tab on user action, no remote code.
- Screenshots can contain personal data that DOM redaction does not see. Mitigation: mask known sensitive regions; if unsure, send no screenshot.

**Acceptance criteria**

- Redaction tests (emails, phones, card numbers, OTP/password fields, tokens in URLs, personal-looking text) pass; the miss rate on the labeled set is measured and reported, not assumed zero.
- A test page with fake personal data shows none of it in the outgoing request (captured and asserted).
- On a login/OTP/payment page the agent sends nothing and says so.
- The extension never types, clicks or submits (asserted by test).
- Highlight and cursor appear on the correct element on a test page and match stored findings.
- No fix path runs without an explicit user action; existing approval-bypass tests still pass.
- Unselected or unrequested issues remain untouched.

Status: first version built (owner approved on 2026-10-19). See `docs/EXTENSION.md` for what exists, how it was tested and its limits. Not tested in a real Chrome profile end to end.

## 6. Open decisions (owner to answer before Phase 1)

1. Stack confirmed as in section 3?
2. AI provider and model for the primary adapter, and the monthly AI/API cost budget?
3. Database is decided: Neon (cloud). Still open: where do the API, worker and Playwright run (laptop, VPS, cloud: which), and which Neon region and plan (free vs paid; check current limits and autosuspend behavior)?
4. Auth: self-built with a maintained library, or a managed provider?
5. Is a GitHub remote repo ready?
6. Who labels the benchmark scenarios, and which seeded test sites will be used?
7. Domain verification method: DNS TXT, file, or both?

## 7. Risks

- Scope: ten MVP phases is large, and Phase 11 is a separate product surface. Protect Phases 2, 3, 6, 7 (the safety core) over polish; do not start Phase 11 early.
- Phase 12 ideas are untested with real users; talk to a few target owners first and build what they ask for.
- Phase 11C involves children's data and needs legal review before any build.
- SSRF and sandbox mistakes are the highest-impact security risk.
- Benchmark labeling needs human time not covered by the code plan.
- AI cost can grow quietly; quotas must exist before Phase 5 ships.
- Local disk/RAM headroom is thin for Docker + browsers.
- Neon: free-tier limits and compute autosuspend (first request after idle can be slow) may affect scans and jobs; pick plan and region deliberately. Data stored in a given region may matter for customer and children's-data rules.
- Never point tests or migration experiments at the production Neon database; use a separate Neon branch or project, or local Postgres.

## 8. Gaps in `CLAUDE.md` vs this plan

`CLAUDE.md` does not yet include: domain ownership verification, egress-proxy SSRF design, concrete sandbox spec, base commit SHA in the approval hash, pre-model secret scan, seeded-site benchmark, an explicit "stop after each phase" rule. Add them or treat this plan as the override where it is stricter.
