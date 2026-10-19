# KMR AI operations guide

Written for a controlled beta. Everything here was exercised on the development machine only;
nothing has been run against a staging or production environment, because none exists yet.

## Health and monitoring hooks

| What                         | Where                                                           | Notes                                                                                                                                |
| ---------------------------- | --------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| Liveness                     | `GET /health`                                                   | No dependencies, no secrets.                                                                                                         |
| Readiness                    | `GET /ready`                                                    | Database and Redis reachability (TCP). Does not verify credentials or schema.                                                        |
| Per-organization worker view | `GET /v1/organizations/{org}/system` (admin)                    | Queued, running and stalled scans; sandbox availability. Also shown in Settings.                                                     |
| Usage against limits         | `GET /v1/organizations/{org}/usage` (admin)                     | Scans, diagnoses, repair plans, checks.                                                                                              |
| Logs                         | stdout, one JSON line per event                                 | Every line has a `request_id`; the same id is returned in `X-Request-ID` and in every error body. Secrets are masked before logging. |
| Audit trail                  | `GET /v1/organizations/{org}/audit` and `/audit/verify` (admin) | Hash-chained. A broken chain is shown on the History page.                                                                           |

There is no metrics endpoint and no alerting yet. Point an external uptime check at `/ready` and
ship stdout to a log service; alert on `scans_stalled > 0` from the system endpoint if you poll it.

## Retries, timeouts and limits (all configurable, see `.env.example`)

- Scans: per-scan page, request, byte and time limits; one bounded retry for a dead worker; the queue
  reclaims a scan whose heartbeat stopped.
- Diagnosis: bounded provider retries with backoff; daily run limit and optional monthly cost ceiling
  per organization.
- Repairs: daily planning limit per organization; patch size and file-count limits.
- Checks: run in a sandbox with a time limit (`KMR_CHECK_TIMEOUT_SECONDS`), memory, CPU and process
  limits, no network; hourly rate limit per organization.
- Approvals expire after `KMR_APPROVAL_TTL_HOURS` (default 24).

## Backup and restore

```bash
make backup                                   # ./backups/kmr-db-<stamp>.dump and kmr-evidence-<stamp>.tar.gz
bash scripts/restore-check.sh backups/kmr-db-<stamp>.dump
```

`restore-check.sh` restores into a disposable database (never the live one), prints the migration
head and compares row counts with the live database, then drops the scratch database. Run it after
every backup you intend to rely on.

Tested on the development database: the dump restored with migration head `0006` (taken before
migration 0007 existed) and every compared table matched row for row. Not yet tested: restoring on a
different host, point-in-time recovery, or restoring the evidence archive.

To recover for real: stop the API and worker, create an empty database, `pg_restore --no-owner` the
dump into it, run `make db-init` (roles and migrations), point `KMR_DATABASE_URL` at it, start the
services, then open the History page and confirm the audit chain is intact.

Backups contain customer data. Store them encrypted and restrict access; `backups/` is git-ignored.

## Staging deployment plan (not executed)

Staging needs its own explicit authorization. Proposed shape:

1. Build both images from a tagged commit; no secrets in images.
2. Provision Postgres and Redis (managed or containers), the egress proxy and browser sandbox on an
   internal network, and a separate unprivileged runner for checks.
3. Set the `.env` values from `.env.example` using a secret manager: new `KMR_SECRET_KEY`,
   `KMR_ENCRYPTION_KEY`, database passwords. Set `KMR_APP_ENV=staging`.
4. Run `make db-init` with the owner role; the app runs as the restricted role.
5. Put the web app behind TLS (HSTS is already sent when `KMR_APP_ENV` is staging or production).
6. Check `/ready`, create a test organization, scan an owned test site, run the full flow with a
   throwaway repository.

Rollback: keep the previous image tag and the pre-deploy backup. Migrations 0001-0007 have
`downgrade` functions, but downgrading drops data created by newer tables, so prefer restoring the
pre-deploy backup. Never roll back by deleting audit rows.

## Incident procedure (draft)

1. **Suspected data exposure or cross-tenant access:** stop the API (`make down` or stop the
   service), preserve logs and a database backup, and do not delete anything.
2. **Rotate** `KMR_SECRET_KEY` (signs CSRF tokens; sessions stay valid but tokens change),
   `KMR_ENCRYPTION_KEY` (re-enter stored tokens and API-check credentials afterwards: ciphertext made
   with the old key can no longer be read), database passwords, and any provider keys.
3. **Check the audit chain** (`/audit/verify`) and `access.denied` events for the period.
4. **Unwanted branch or approval:** revoke the approval from the issue page; the branch can be deleted
   by the repository owner (KMR AI never deletes branches).
5. Write down what happened, what evidence supports it, and what was changed.

## Known operational limits

- A single API process and a single worker were tested. No horizontal scaling test exists.
- The sandbox host-gateway warning from `make scanner-check` is reported, not fixed (see README).
- Local-git repositories apply a fix by creating a branch in the local repository; nothing is pushed.
- The GitHub adapter is untested against real GitHub.
