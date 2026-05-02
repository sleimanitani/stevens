"""Apply SQL migrations against ``$DATABASE_URL`` via psycopg.

Drop-in replacement for the old ``scripts/db_migrate.sh`` shell-out to
``psql``: same lexicographic ordering, same idempotent re-run guarantee
(every migration uses ``IF NOT EXISTS`` guards), no host ``psql`` required.

Usage:
    DATABASE_URL=postgresql:///assistant uv run python -m demiurge.bootstrap.migrate
    # or, equivalently after v0.10:
    DATABASE_URL=postgresql:///assistant bash scripts/db_migrate.sh
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Iterable

import psycopg


def _default_migrations_dir() -> Path:
    """Locate the repo's migrations dir from this file's location.

    Works in dev (editable install). When the package is pip-installed from a
    wheel, callers must pass an explicit path or set ``$DEMIURGE_MIGRATIONS_DIR``.
    """
    return Path(__file__).resolve().parents[4] / "resources" / "migrations"


def _resolve_migrations_dir(arg: str | None) -> Path:
    if arg:
        return Path(arg).expanduser().resolve()
    env = os.environ.get("DEMIURGE_MIGRATIONS_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return _default_migrations_dir()


def apply_migrations(dsn: str, mig_dir: Path, *, on_progress=None) -> int:
    """Apply every ``*.sql`` file under ``mig_dir`` in lexicographic order.

    ``on_progress`` if given is called once per file with its name (used by the
    CLI to print progress; tests pass a list.append).

    Returns the number of migration files applied.
    """
    files = sorted(mig_dir.glob("*.sql"))
    with psycopg.connect(dsn, autocommit=True) as conn:
        for f in files:
            if on_progress is not None:
                on_progress(f.name)
            conn.execute(f.read_text())
    return len(files)


def main(argv: Iterable[str] | None = None) -> int:
    args = list(argv) if argv is not None else sys.argv[1:]
    mig_arg = args[0] if args else None

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("error: DATABASE_URL not set", file=sys.stderr)
        return 2

    mig_dir = _resolve_migrations_dir(mig_arg)
    if not mig_dir.is_dir():
        print(f"error: migrations directory not found at {mig_dir}", file=sys.stderr)
        return 2

    files = sorted(mig_dir.glob("*.sql"))
    if not files:
        print("(no migrations to apply)")
        return 0

    print(f"applying {len(files)} migration(s) against {dsn.split('://', 1)[0]}://...")
    apply_migrations(dsn, mig_dir, on_progress=lambda name: print(f"  → {name}"))
    print("done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
