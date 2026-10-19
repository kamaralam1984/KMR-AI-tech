#!/usr/bin/env bash
# Backs up the KMR AI database (custom-format pg_dump) and the evidence files.
# Usage: scripts/backup.sh [output-dir]      (default: ./backups)
# Reads the connection from the compose postgres container; never prints secrets.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT="${1:-backups}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$OUT"
set -a; . ./.env; set +a
CONTAINER="${KMR_PG_CONTAINER:-kmr-ai-postgres-1}"
DB="${KMR_POSTGRES_DB:-kmr}"
USER_="${KMR_POSTGRES_USER:-kmr}"
docker exec "$CONTAINER" pg_dump -U "$USER_" -d "$DB" -Fc > "$OUT/kmr-db-$STAMP.dump"
if [ -d .data/evidence ]; then
  tar -czf "$OUT/kmr-evidence-$STAMP.tar.gz" -C .data evidence
fi
chmod 600 "$OUT"/kmr-*"$STAMP"*
echo "backup written: $OUT/kmr-db-$STAMP.dump"
