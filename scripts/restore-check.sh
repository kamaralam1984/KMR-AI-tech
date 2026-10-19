#!/usr/bin/env bash
# Restores a database dump into a DISPOSABLE database and checks it. Never touches the live one.
# Usage: scripts/restore-check.sh path/to/kmr-db-<stamp>.dump
set -euo pipefail
cd "$(dirname "$0")/.."
DUMP="${1:?usage: restore-check.sh <dump file>}"
set -a; . ./.env; set +a
CONTAINER="${KMR_PG_CONTAINER:-kmr-ai-postgres-1}"
USER_="${KMR_POSTGRES_USER:-kmr}"
LIVE="${KMR_POSTGRES_DB:-kmr}"
SCRATCH="kmr_restore_check_$(date +%s)"
[ "$SCRATCH" != "$LIVE" ] || { echo "refusing: scratch equals live"; exit 1; }
psql_() { docker exec -i "$CONTAINER" psql -U "$USER_" -v ON_ERROR_STOP=1 -At "$@"; }
trap 'psql_ -d postgres -c "DROP DATABASE IF EXISTS $SCRATCH" >/dev/null 2>&1 || true' EXIT
psql_ -d postgres -c "CREATE DATABASE $SCRATCH" >/dev/null
docker exec -i "$CONTAINER" pg_restore -U "$USER_" -d "$SCRATCH" --no-owner --exit-on-error < "$DUMP"
echo "migration head : $(psql_ -d "$SCRATCH" -c 'SELECT version_num FROM alembic_version')"
for t in users organizations projects scans observations issues repair_plans approvals audit_events; do
  echo "$t: restored=$(psql_ -d "$SCRATCH" -c "SELECT count(*) FROM $t") live=$(psql_ -d "$LIVE" -c "SELECT count(*) FROM $t")"
done
echo "restore check finished; scratch database dropped"
