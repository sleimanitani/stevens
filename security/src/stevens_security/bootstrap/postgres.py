"""Detect, install-instruct, and provision native Postgres 16 + pgvector.

Stevens v0.10 step 2. Designed to run as the **operator's own OS user**, not
root and not under sudo. The flow:

1. ``detect()`` probes the host for what's already in place (psql binary,
   server reachable on the local unix socket, pgvector package present, and
   — if peer auth works — what we can do as the running user inside Postgres).
2. ``install_instructions()`` returns the exact sudo block the operator must
   run themselves when something is missing. Bootstrap never escalates
   privileges; this is the line(s) the operator copy-pastes.
3. ``ensure_role_and_database()`` connects as the OS user via peer auth and
   idempotently creates the ``assistant`` role + ``assistant`` database +
   ``CREATE EXTENSION vector``. It assumes the operator already ran the sudo
   block (so they have a SUPERUSER role matching their Linux username).
4. ``write_env_file()`` writes ``~/.config/stevens/env`` with the
   ``DATABASE_URL`` line, idempotently. Subsequent ``stevens`` invocations
   pick that up via systemd unit ``EnvironmentFile=`` (step 3) or by the
   user sourcing it from their shell.

This module is the reusable substrate for ``stevens bootstrap`` (step 4) and
``stevens doctor`` (step 5). It does no I/O on import.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional

DEFAULT_ROLE = "assistant"
DEFAULT_DB = "assistant"
DEFAULT_DSN = f"postgresql:///{DEFAULT_DB}"


# ----------------------------- platform -----------------------------------


def _detect_platform() -> str:
    """Return one of: ``linux-debian``, ``linux-other``, ``macos``,
    ``windows``, ``unknown``.

    Linux is split because the install commands differ between Debian/Ubuntu
    (apt + the PGDG repo) and other distros (where we don't ship a recipe).
    """
    import sys

    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform.startswith("linux"):
        os_release = Path("/etc/os-release")
        if os_release.exists():
            txt = os_release.read_text()
            if "ID=debian" in txt or "ID=ubuntu" in txt or "ID_LIKE=debian" in txt:
                return "linux-debian"
        return "linux-other"
    return "unknown"


# ----------------------------- detection ----------------------------------


@dataclass
class PostgresState:
    """Snapshot of what's installed and reachable on this host.

    Every field is independent: a missing ``psql`` doesn't suppress probing
    for the running server (server is what matters; psql is convenience).
    """

    platform: str
    psql_present: bool
    psql_version: Optional[str]  # e.g. "16.13"
    server_reachable: bool
    pgvector_pkg_installed: Optional[bool]  # None = couldn't tell (non-debian)
    peer_role_exists: Optional[bool]  # None = couldn't connect to probe
    target_role_exists: Optional[bool]
    target_db_exists: Optional[bool]
    vector_extension_present: Optional[bool]

    @property
    def needs_install(self) -> bool:
        """True if the OS-level Postgres install is missing or broken."""
        return not self.server_reachable

    @property
    def needs_provisioning(self) -> bool:
        """True if the server is up but the assistant role/DB/extension isn't ready."""
        return self.server_reachable and not (
            self.target_role_exists
            and self.target_db_exists
            and self.vector_extension_present
        )


def _psql_version() -> tuple[bool, Optional[str]]:
    """Return ``(present, version_str_or_None)``."""
    if shutil.which("psql") is None:
        return False, None
    try:
        out = subprocess.run(
            ["psql", "--version"], capture_output=True, text=True, timeout=5, check=False
        ).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return True, None
    # "psql (PostgreSQL) 16.13 (Ubuntu 16.13-1.pgdg22.04+1)"
    parts = out.split()
    for token in parts:
        if token and token[0].isdigit() and "." in token:
            return True, token
    return True, None


def _server_reachable() -> bool:
    """``pg_isready`` exits 0 when the local server is accepting connections.

    We don't fall back to a manual TCP probe — Stevens runs over the unix
    socket, so the existence of ``/var/run/postgresql/.s.PGSQL.5432`` (or
    equivalent) is the only thing that matters.
    """
    if shutil.which("pg_isready") is None:
        return False
    try:
        rc = subprocess.run(
            ["pg_isready", "-q"], timeout=5, check=False
        ).returncode
    except (OSError, subprocess.TimeoutExpired):
        return False
    return rc == 0


def _pgvector_pkg_installed(platform: str) -> Optional[bool]:
    """On Debian/Ubuntu, ask dpkg if ``postgresql-16-pgvector`` is installed.

    Returns ``None`` on platforms where we don't have a check (caller treats
    None as "couldn't tell — assume the operator handled it").
    """
    if platform != "linux-debian":
        return None
    if shutil.which("dpkg-query") is None:
        return None
    try:
        proc = subprocess.run(
            ["dpkg-query", "-W", "-f=${Status}", "postgresql-16-pgvector"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.returncode == 0 and "install ok installed" in proc.stdout


def _probe_via_psycopg(
    role: str, database: str
) -> tuple[Optional[bool], Optional[bool], Optional[bool], Optional[bool]]:
    """Connect to the ``postgres`` maintenance DB as the OS user (peer auth)
    and ask: does my OS-named role exist, does ``role`` exist, does
    ``database`` exist, and inside ``database`` is ``vector`` already
    created.

    Returns ``(peer_role_exists, target_role_exists, target_db_exists,
    vector_extension_present)``. Any of them can be ``None`` if we couldn't
    connect to the maintenance DB (which is a strong signal that the
    operator hasn't run the sudo provisioning block yet).
    """
    try:
        import psycopg
    except ImportError:
        return None, None, None, None

    me = os.environ.get("USER") or os.environ.get("LOGNAME")

    try:
        with psycopg.connect("postgresql:///postgres", autocommit=True, connect_timeout=3) as conn:
            peer_role = None
            if me:
                cur = conn.execute(
                    "SELECT 1 FROM pg_roles WHERE rolname = %s", (me,)
                )
                peer_role = cur.fetchone() is not None
            cur = conn.execute(
                "SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)
            )
            target_role = cur.fetchone() is not None
            cur = conn.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (database,)
            )
            target_db = cur.fetchone() is not None
    except Exception:  # noqa: BLE001 — any failure means "we don't know"
        return None, None, None, None

    vector_present: Optional[bool] = None
    if target_db:
        try:
            with psycopg.connect(
                f"postgresql:///{database}", autocommit=True, connect_timeout=3
            ) as conn:
                cur = conn.execute(
                    "SELECT 1 FROM pg_extension WHERE extname = 'vector'"
                )
                vector_present = cur.fetchone() is not None
        except Exception:  # noqa: BLE001
            vector_present = None

    return peer_role, target_role, target_db, vector_present


def detect(*, role: str = DEFAULT_ROLE, database: str = DEFAULT_DB) -> PostgresState:
    """Snapshot what's installed and reachable. Pure detection — no writes."""
    platform = _detect_platform()
    psql_ok, psql_ver = _psql_version()
    server_up = _server_reachable()
    vector_pkg = _pgvector_pkg_installed(platform)

    peer_role: Optional[bool] = None
    target_role: Optional[bool] = None
    target_db: Optional[bool] = None
    vector_ext: Optional[bool] = None
    if server_up:
        peer_role, target_role, target_db, vector_ext = _probe_via_psycopg(role, database)

    return PostgresState(
        platform=platform,
        psql_present=psql_ok,
        psql_version=psql_ver,
        server_reachable=server_up,
        pgvector_pkg_installed=vector_pkg,
        peer_role_exists=peer_role,
        target_role_exists=target_role,
        target_db_exists=target_db,
        vector_extension_present=vector_ext,
    )


# ----------------------------- instructions -------------------------------


@dataclass
class InstallPlan:
    """Operator-facing instructions: the lines they need to run themselves.

    ``sudo_block`` is the multi-line shell block that requires elevated
    privileges. ``post_block`` is anything the operator should run as their
    own user afterwards (typically empty — bootstrap handles that itself).
    """

    sudo_block: str
    post_block: str = ""
    notes: List[str] = field(default_factory=list)


def install_instructions(state: PostgresState) -> Optional[InstallPlan]:
    """Return the operator instructions needed to reach a runnable state.

    None if the host is already in a state where ``ensure_role_and_database``
    will succeed (Postgres up + the running OS user has peer auth as a
    SUPERUSER). Caller should call ``detect()`` again after the operator
    runs the printed block.
    """
    me = os.environ.get("USER") or os.environ.get("LOGNAME") or "$USER"

    if not state.needs_install and state.peer_role_exists:
        # Server up and we can already connect as ourselves — nothing
        # privileged left to do.
        return None

    if state.platform == "linux-debian":
        lines = [
            "# Stevens v0.10 — Postgres 16 + pgvector install (one sudo block).",
            "# These are the only commands that need root. Bootstrap never runs sudo itself.",
            "",
        ]
        if state.needs_install:
            lines += [
                "sudo install -d /usr/share/postgresql-common/pgdg",
                "sudo curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \\",
                "    -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc",
                "echo \"deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] \\",
                "    https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main\" \\",
                "    | sudo tee /etc/apt/sources.list.d/pgdg.list",
                "sudo apt-get update",
                "sudo apt-get install -y postgresql-16 postgresql-16-pgvector",
            ]
        if not state.peer_role_exists:
            lines += [
                "",
                f"# Grant your OS user a matching SUPERUSER role so peer auth works.",
                f"# (Stevens will then connect over the unix socket as {me} with no password.)",
                f"sudo -u postgres createuser -s {me}",
            ]
        return InstallPlan(
            sudo_block="\n".join(lines),
            notes=[
                "After running the block above, re-run `stevens bootstrap` "
                "(or invoke ensure_role_and_database directly) to create the "
                "assistant role + DB and write ~/.config/stevens/env.",
            ],
        )

    if state.platform == "macos":
        lines = [
            "# Stevens v0.10 — Postgres 16 + pgvector install on macOS.",
            "brew install postgresql@16 pgvector",
            "brew services start postgresql@16",
        ]
        return InstallPlan(
            sudo_block="\n".join(lines),
            notes=[
                "Homebrew installs Postgres for the current user, so no peer-auth "
                "role grant is needed (you ARE the superuser).",
                "If `pgvector` isn't available in your Homebrew tap, "
                "see https://github.com/pgvector/pgvector#installation.",
            ],
        )

    if state.platform == "windows":
        return InstallPlan(
            sudo_block=(
                "# Stevens v0.10 — Postgres 16 + pgvector install on Windows.\n"
                "# Download and run the EnterpriseDB Postgres 16 installer:\n"
                "#   https://www.postgresql.org/download/windows/\n"
                "# Then install pgvector from:\n"
                "#   https://github.com/pgvector/pgvector#windows"
            ),
            notes=[
                "Windows support is best-effort in v0.10 — Linux is the primary "
                "target. File an issue if anything breaks.",
            ],
        )

    return InstallPlan(
        sudo_block=(
            "# Stevens needs Postgres 16 + pgvector. We don't ship a recipe for\n"
            f"# platform '{state.platform}' — install via your package manager and re-run."
        ),
        notes=["Pull request welcome with the recipe for your platform."],
    )


# ----------------------------- provisioning -------------------------------


def ensure_role_and_database(
    *,
    role: str = DEFAULT_ROLE,
    database: str = DEFAULT_DB,
    admin_dsn: str = "postgresql:///postgres",
) -> List[str]:
    """Create ``role`` + ``database`` + ``vector`` extension if missing.

    Connects to ``admin_dsn`` (the maintenance DB) as the OS user via peer
    auth. Caller must have already ensured that OS user has SUPERUSER (or
    at least CREATEROLE + CREATEDB) — see ``install_instructions``.

    Returns a list of human-readable action lines describing what changed
    (empty if everything was already in place).

    Idempotent: re-running on a fully-provisioned host returns ``[]``.
    """
    import psycopg
    from psycopg import sql

    actions: List[str] = []

    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        cur = conn.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)
        )
        if cur.fetchone() is None:
            conn.execute(
                sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(role))
            )
            actions.append(f"created role {role!r}")
        cur = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (database,)
        )
        if cur.fetchone() is None:
            conn.execute(
                sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    sql.Identifier(database), sql.Identifier(role)
                )
            )
            actions.append(f"created database {database!r} owned by {role!r}")

    with psycopg.connect(
        f"postgresql:///{database}", autocommit=True
    ) as conn:
        cur = conn.execute(
            "SELECT 1 FROM pg_extension WHERE extname = 'vector'"
        )
        if cur.fetchone() is None:
            conn.execute("CREATE EXTENSION vector")
            actions.append(f"created extension 'vector' in {database!r}")

    return actions


# ----------------------------- env file -----------------------------------


def env_file_path() -> Path:
    """``~/.config/stevens/env`` — operator-owned, mode 0600."""
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "stevens" / "env"


def write_env_file(
    *, dsn: str = DEFAULT_DSN, path: Optional[Path] = None
) -> tuple[Path, bool]:
    """Idempotently write ``DATABASE_URL=<dsn>`` to ``~/.config/stevens/env``.

    Preserves any other lines already in the file. Returns
    ``(path, changed)`` where ``changed=False`` means the file already had
    the same value and we touched nothing.

    File mode is forced to 0600 — this file is going to ride along as an
    ``EnvironmentFile=`` in step 3's systemd units, and may eventually
    accumulate other identifiers; default-tight is the right posture.
    """
    target = path if path is not None else env_file_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    existing: List[str] = []
    if target.exists():
        existing = target.read_text().splitlines()

    new_line = f"DATABASE_URL={dsn}"
    found = False
    out: List[str] = []
    for line in existing:
        if line.startswith("DATABASE_URL="):
            out.append(new_line)
            found = True
        else:
            out.append(line)
    if not found:
        out.append(new_line)

    new_text = "\n".join(out) + "\n"
    old_text = "\n".join(existing) + ("\n" if existing else "")
    if new_text == old_text and target.exists():
        os.chmod(target, 0o600)
        return target, False

    target.write_text(new_text)
    os.chmod(target, 0o600)
    return target, True


# ----------------------------- formatting ---------------------------------


def format_state(state: PostgresState) -> str:
    """Operator-readable rundown of detected state."""

    def fmt(label: str, value, ok=None):
        if ok is None:
            ok = bool(value) if not isinstance(value, str) else True
        symbol = "✓" if ok else ("·" if value is None else "✗")
        return f"  {symbol} {label}: {value}"

    lines = [
        f"Postgres detection ({state.platform}):",
        fmt("psql binary", state.psql_present),
        fmt(
            "psql version",
            state.psql_version or "(unknown)",
            ok=state.psql_version is not None
            and state.psql_version.split(".", 1)[0] == "16",
        ),
        fmt("server reachable", state.server_reachable),
    ]
    if state.pgvector_pkg_installed is not None:
        lines.append(fmt("pgvector pkg", state.pgvector_pkg_installed))
    if state.peer_role_exists is not None:
        lines.append(fmt("peer auth role", state.peer_role_exists))
    if state.target_role_exists is not None:
        lines.append(fmt("assistant role", state.target_role_exists))
    if state.target_db_exists is not None:
        lines.append(fmt("assistant DB", state.target_db_exists))
    if state.vector_extension_present is not None:
        lines.append(fmt("vector extension", state.vector_extension_present))
    return "\n".join(lines)


# ----------------------------- CLI ----------------------------------------


def _print_lines(lines: Iterable[str]) -> None:
    for line in lines:
        print(line)


def main(argv: Optional[Iterable[str]] = None) -> int:
    """``python -m stevens_security.bootstrap.postgres`` entrypoint.

    Modes:
    - default: detect + print state + print install_instructions if needed.
    - ``--ensure``: provision role/DB/extension (assumes the host is already
      installed and the running user has SUPERUSER via peer auth).
    - ``--write-env``: write ``~/.config/stevens/env``.
    """
    import argparse

    p = argparse.ArgumentParser(
        prog="stevens_security.bootstrap.postgres",
        description="Detect / provision native Postgres for Stevens.",
    )
    p.add_argument(
        "--ensure",
        action="store_true",
        help="create assistant role + DB + vector extension (idempotent)",
    )
    p.add_argument(
        "--write-env",
        action="store_true",
        help="write ~/.config/stevens/env with DATABASE_URL",
    )
    p.add_argument("--role", default=DEFAULT_ROLE)
    p.add_argument("--database", default=DEFAULT_DB)
    args = p.parse_args(list(argv) if argv is not None else None)

    state = detect(role=args.role, database=args.database)
    print(format_state(state))

    if not args.ensure and not args.write_env:
        plan = install_instructions(state)
        if plan is None:
            print("\nPostgres is ready. Re-run with --ensure to provision the assistant role/DB.")
            return 0
        print("\n" + plan.sudo_block)
        if plan.post_block:
            print("\n" + plan.post_block)
        for note in plan.notes:
            print(f"\n# {note}")
        return 1  # not an error per se, but state isn't actionable yet

    if args.ensure:
        if state.needs_install:
            print(
                "\nerror: Postgres server isn't reachable — run the install block first.",
            )
            return 2
        actions = ensure_role_and_database(role=args.role, database=args.database)
        if actions:
            print("\nProvisioned:")
            for a in actions:
                print(f"  → {a}")
        else:
            print("\nAlready provisioned — no changes.")

    if args.write_env:
        dsn = f"postgresql:///{args.database}"
        path, changed = write_env_file(dsn=dsn)
        verb = "wrote" if changed else "verified"
        print(f"\n{verb} {path} (DATABASE_URL={dsn})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
