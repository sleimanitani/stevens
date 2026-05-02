"""First-time setup machinery for Stevens (v0.10).

Submodules:
- ``migrate``: apply SQL migrations against $DATABASE_URL via psycopg.
- ``postgres``: detect Postgres 16 + pgvector; print the one sudo block the
  user needs if missing; idempotently provision the ``assistant`` role + DB
  via peer auth; write ``~/.config/stevens/env`` with the unix-socket DSN.

Future submodules (v0.10 step 3+): ``systemd``, ``preflight``.
"""
