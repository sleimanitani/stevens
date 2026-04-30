#!/usr/bin/env bash
# db_migrate.sh — apply all migrations in lexicographic order against $DATABASE_URL.
#
# Idempotent: every migration uses `CREATE TABLE IF NOT EXISTS` and similar
# guards. Re-running is safe.
#
# Usage:
#   DATABASE_URL=postgres://user:pass@host:5432/db ./scripts/db_migrate.sh
#
# Or via compose:
#   docker compose exec -T postgres bash -c 'PGPASSWORD=$POSTGRES_PASSWORD \
#     psql -U $POSTGRES_USER -d $POSTGRES_DB' < /repo/resources/migrations/<file>.sql
set -euo pipefail

if [[ -z "${DATABASE_URL:-}" ]]; then
    echo "error: DATABASE_URL not set" >&2
    exit 2
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MIG_DIR="${REPO_ROOT}/resources/migrations"

if [[ ! -d "${MIG_DIR}" ]]; then
    echo "error: migrations directory not found at ${MIG_DIR}" >&2
    exit 2
fi

shopt -s nullglob
files=("${MIG_DIR}"/*.sql)
if [[ ${#files[@]} -eq 0 ]]; then
    echo "(no migrations to apply)"
    exit 0
fi

# Sort lexicographically (001_*, 002_*, …).
IFS=$'\n' sorted=($(sort <<<"${files[*]}")); unset IFS

echo "applying ${#sorted[@]} migration(s) against ${DATABASE_URL%%:*}://..."
for f in "${sorted[@]}"; do
    name="$(basename "$f")"
    echo "  → ${name}"
    psql "${DATABASE_URL}" -v ON_ERROR_STOP=1 -q -f "$f"
done
echo "done."
