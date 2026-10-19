# KMR AI

Scans websites, explains real issues with evidence, proposes fixes on isolated branches, and verifies
the result after an explicitly approved change. Requirements and safety rules are in `CLAUDE.md`; the
phase plan is in `PLAN.md`.

**Status: pre-release, controlled beta only (all ten MVP phases built).** Sign in, organize work in
projects, scan a website you have proven you own, get evidence-backed issues, plan a fix against a
connected repository, review the exact diff, run checks in a sandbox, approve one exact change, have
KMR AI create a new branch (never merge or deploy), then scan again and get Fixed / Not Fixed /
Needs Review. See `docs/BETA-READINESS.md` for what was measured and what is still unproven, and
`docs/OPERATIONS.md` for backups, monitoring and the incident procedure.

Two things are **not** verified against the real service because no credentials exist here: the AI
provider (the default diagnosis is the built-in rules, labeled "not AI") and GitHub. Both have
adapters tested only against labeled fakes.

## Layout

```
backend/    FastAPI API and scan worker (Python), Alembic migrations in backend/migrations
benchmarks/ diagnosis benchmark datasets, baselines and results (see benchmarks/diagnosis/README.md)
docs/       operations guide and the beta readiness report
scripts/    backup, restore check, load check, scanner firewall (optional)
frontend/   Next.js app (TypeScript, Tailwind), installable on phones
docker/     the scanner browser image
docker-compose.yml   Postgres, Redis, and (profile "scanner") the isolated scanner sandbox
Makefile    common commands (`make help`)
```

## Prerequisites

Python 3.11+ (a venv is created in `backend/.venv`), Node 20+ with pnpm, Docker with Compose, and
Google Chrome or Chromium only for `make smoke` / `make e2e`.

## First-time setup

```bash
make setup      # creates .env, the Python venv, installs both dependency sets
# edit .env: replace every "change-me" value. Also set KMR_ENCRYPTION_KEY (a Fernet key, see
# .env.example) to store repository tokens and API-check credentials, and KMR_REPO_LOCAL_ROOTS
# (a JSON list such as '["/home/you/repos"]') to allow local git repositories. Use the same password inside KMR_DATABASE_URL and
# KMR_APP_DB_PASSWORD, the owner password inside KMR_MIGRATION_DATABASE_URL, and generate
# KMR_SECRET_KEY with: openssl rand -hex 32
make up         # Postgres on 127.0.0.1:55432, Redis on 127.0.0.1:56379
make db-init    # creates the restricted roles, applies migrations
make scanner-up # builds and starts the scanner sandbox (first build downloads ~1.5 GB)
```

`.env` is git-ignored. `.env.example` holds placeholders only; never put real secrets in it.

## Run

```bash
make dev-api    # http://127.0.0.1:8000  (/health, /ready, /docs outside production)
make dev-web    # http://localhost:3000
make worker     # runs scans; without it a started scan stays "Queued"
```

Open the app at **http://localhost:3000**, not `127.0.0.1`: the API only accepts browser requests
whose origin is listed in `KMR_CORS_ORIGINS` (default `http://localhost:3000`).

## How accounts and isolation work

- **Two database roles.** Migrations run as the owner role (`KMR_MIGRATION_DATABASE_URL`). The API
  runs as a restricted role (`KMR_DATABASE_URL`) with no superuser rights and no `BYPASSRLS`.
- **Row-Level Security** is forced on organizations, memberships, projects and audit events. Every
  request and background job enters a tenant through `app/db/tenant.py`, which sets the active user
  and organization for that one transaction only. The API also checks membership and role first.
- **Sessions** are random tokens in an HttpOnly, SameSite=Lax cookie; only a SHA-256 of the token is
  stored. Idle and absolute expiry, logout and "sign out of all devices" revoke them.
- **CSRF**: state-changing calls need an `X-CSRF-Token` header derived from the session, and browser
  requests must come from an allowed origin. The browser only talks to the Next.js origin; `/api/*`
  is proxied to the API (`KMR_API_URL`), so the cookie is first-party.
- **Passwords** are hashed with Argon2id. Login and sign-up are rate limited (Redis; in-memory in
  local/test only). Failed logins return one generic message.
- Roles: owner, admin, member, viewer. Owners manage everyone; admins manage members and viewers;
  an organization always keeps at least one owner.

## Checks

```bash
make check          # ruff, mypy, pytest (needs Postgres, Chrome), eslint, prettier, tsc, vitest, build
make scanner-check  # the REAL sandbox: isolation, hardening, SSRF blocks, a scan of example.com
make smoke          # responsive check at 375, 768, 1280 and 1920px (API + web running)
make audit          # known-vulnerability check of Python and JS dependencies (needs network)
node frontend/scripts/a11y-audit.mjs   # axe-core (WCAG 2.2 A/AA) and keyboard-focus audit
make backup         # database + evidence backup; scripts/restore-check.sh proves a restore
make e2e            # phone-size browser flow incl. a real scan (API, web, worker, sandbox running)
```

`make smoke` and `make e2e` need the API and the web app running (`make dev-api`, and either
`make dev-web` or `pnpm start` after `make build`). `make e2e` also needs the worker and the
sandbox (it scans example.com); `SMOKE_SCAN=1 make smoke` does the same to cover the scan page.
**Both create users and organizations in the development database** (random `@example.com` emails), so run them against development data only.
`make clean-test-data` removes those rows and their screenshots (`make clean-test-data DRY=1`
previews). Nobody here owns example.com, so the e2e/smoke scans record the ownership proof with a
local-only dev tool (`app/db/dev_tools.py`, refuses to run unless `KMR_APP_ENV=local`); the API has
no such path. Sign-up is rate
limited to 10 per hour per IP, so repeated runs can hit HTTP 429; wait, or raise
`KMR_REGISTER_MAX_PER_HOUR` in `.env` for heavy local testing.

Backend tests create a disposable database and role per run and drop them afterwards; they never
touch the development database. If Postgres is unreachable they are skipped and reported as such.

## How scanning stays safe

- **Ownership first.** A scan only starts for a website you proved you control: a TXT record at
  `_kmr-verify.<host>` or a file at `/.well-known/kmr-verification.txt`. Changing the host discards
  the proof. Verification checks are rate limited; the file check goes through the egress proxy.
- **One way out.** The browser runs in a container on an internal Docker network whose only
  neighbour is the egress proxy. The proxy resolves every name itself, requires every address to be
  public (localhost, private ranges, link-local and the cloud metadata address are refused,
  including IPv4-mapped and NAT64/6to4 forms), and connects to an address it just checked, so DNS
  rebinding cannot swap the target. Only ports 80 and 443 are allowed.
- **Passive.** Only GET/HEAD/OPTIONS leave the browser (form posts and XHR writes are blocked and
  recorded); top-level navigation stays on the verified host; links like logout/delete/unsubscribe
  are never followed; popups, dialogs and downloads are shut. Limits cover pages, requests, bytes,
  time per page and time per scan.
- **Sanitized evidence.** Cookies, headers and response bodies are never captured. URLs, console
  text and errors are masked for credentials, tokens, emails and card-like numbers before storage.
  Screenshots are served only through a tenant-checked API route, never as static files.
- **Hardened containers.** Read-only filesystem, all capabilities dropped, no-new-privileges, memory,
  CPU and process limits; the browser's flags and proxy are fixed server-side. `make scanner-check`
  verifies this against the running containers.
- **Tenant isolation continues.** Scans, observations and screenshots are covered by the same
  forced Row-Level Security. The worker re-checks that the requesting user is still a member before
  it touches the network, and the queue's two cross-tenant operations are narrow SECURITY DEFINER
  functions (claim a scan, recover a dead one).

### Scanner sandbox limits

- Docker's internal networks still allow a container to reach the host's address on that bridge.
  All browser traffic goes through the proxy, which refuses private addresses, so this matters only
  if the browser itself were compromised: it could then reach host services bound to `0.0.0.0`.
  `make scanner-check` reports this as a warning. `scripts/scanner-firewall.sh` (root, optional,
  not run by anything here) adds a firewall rule that closes it; keep host dev services on
  `127.0.0.1` otherwise.
- The browser container runs Chromium without its own sandbox (it cannot use user namespaces after
  capabilities are dropped); the container is the isolation layer.
- Scanning a site whose pages require login is not supported (no authenticated journeys yet).
- The sandbox needs Docker on Linux (the worker reaches the browser by its container address).
- The DNS ownership check uses the machine's resolver; it cannot be tested against a real domain
  here, so it is covered by tests with a labeled fake (`tests/fakes.py`) and by the file method's
  path through the real proxy.

## Diagnosis, repairs, approvals and verification

- **Diagnosis** turns stored observations into issues. Every statement cites evidence ids; claims
  without a real citation are dropped; "Observed", "Inferred" and "Unverified" are kept apart; a
  backend cause is never claimed from a frontend symptom alone (that gives Needs Review). The default
  provider is built-in rules and is labeled "Built-in rules (not AI)". An Anthropic adapter exists
  (`KMR_AI_PROVIDER=anthropic` plus a key); it is tested against a fake HTTP transport only.
  Quotas and an optional monthly cost ceiling are per organization. The benchmark and its honest
  limits are in `benchmarks/diagnosis/README.md`.
- **Repairs** are planned against an exact base commit of a connected repository (a local git folder
  inside `KMR_REPO_LOCAL_ROOTS`, or GitHub). Planning only reads. Whatever the proposer returns (rules,
  an AI model, or a person) is validated: confined paths, no CI/dependency/secret/migration files, no
  deletions, no added credentials, size limits. Files that contain secrets are never shown to a model.
- **Checks** run on a copy inside a container with no network, read-only mounts, dropped capabilities,
  an unprivileged user and resource limits. Results are Passed, Failed, Skipped or Unavailable and
  are never promoted.
- **Approval** is by an owner or admin, only after checks, and is bound to a hash of the repository,
  base commit, branch, issues, exact diff and action. It expires, can be revoked, is invalidated when
  the plan changes, and is re-verified (including that the target files did not change) at the moment
  of applying. **Applying only creates a new branch.** There is no merge, deploy, migration or delete
  path anywhere in the API; a test enumerates the routes to keep it that way.
- **Audit**: each organization's events form a hash chain; History shows whether it is intact. The
  application role can only append to the audit table.
- **Verification** compares a newer scan with the one that found the issue. Fixed requires: the new
  scan completed, the issue is gone, every page where it was seen was loaded again, and nothing
  material and new appeared. Still present is Not Fixed. Anything that cannot be established is
  Needs Review.

## Post-MVP modules

- **Visitor assistant** (Phase 11): `docs/ASSISTANT.md`, `docs/CHILD-LEARNING-SAFEGUARDS.md`.
- **Owner scanner enhancements** (Phase 12): impact ranking (heuristic), ticket export, device and
  slow-network scan profiles, scan comparison. `docs/OWNER-SCANNER.md`.
- **Live browser agent** (Phase 13): Chrome extension in `extension/` that only looks and highlights.
  `docs/EXTENSION.md`. Test it with `make ext-test`.

## Homepage images

The homepage uses drawn artwork for the four steps and real screenshots of the product (made by
`frontend/scripts/make-showcase.mjs` from a real scan of example.com). No stock photos ship with the
project. To use your own licensed photos, put `scan`, `highlight`, `approve` and `verify` images
(`.webp`, `.jpg` or `.png`) into `frontend/public/images/` and rebuild; they replace the artwork.

## Mobile

The layout is mobile-first: bottom tab bar on phones, bottom-sheet dialogs, 40-44px touch targets,
16px form text (no zoom on focus), safe-area insets, and a web app manifest so it can be added to the
home screen. There is no service worker, so it does not work offline.

## Known gaps

- No email verification, password reset, invitations or two-step verification (no email service yet).
- Registering or adding a member by email reveals whether an account exists. Both are rate limited.
- No password-breach check; the password policy is length only (12 to 128 characters).
- The AI diagnosis and repair providers and the GitHub adapter are untested against the real services.
- The benchmark labels are written by the author, not human-reviewed; the held-out set is small.
- Local-git repositories "apply" by creating a branch in that repository; nothing is pushed.
- No browser E2E of the repair, approval and verification screens (they are covered by API tests
  and component tests).
- No retention/deletion policy for scans, screenshots and evidence yet.
- Behind a reverse proxy, the client IP used for rate limiting is the proxy's until forwarded
  headers are configured.
- Merge and deploy are deliberately not implemented. Enabling them needs a separate design.

## Notes

- Ports 55432 and 56379 avoid clashes with other local stacks. Change them in `.env` if needed.
- `/ready` reports a dependency as `reachable` when its host and port accept a TCP connection. It does
  not verify credentials or schema.
- Request IDs are returned in the `X-Request-ID` header and included in every error body and log line.
# KMR-AI-tech
# KMR-AI-tech
