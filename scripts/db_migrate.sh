#!/usr/bin/env bash
# db_migrate.sh — apply all migrations against $DATABASE_URL.
#
# Thin wrapper around the Python migrator (no host `psql` required since
# v0.10). Idempotent: every migration uses CREATE … IF NOT EXISTS.
#
# Usage:
#   DATABASE_URL=postgresql:///assistant ./scripts/db_migrate.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"
exec uv run python -m stevens_security.bootstrap.migrate "$@"
