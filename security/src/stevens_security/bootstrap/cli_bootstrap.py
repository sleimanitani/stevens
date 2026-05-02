"""``stevens bootstrap`` — wire up first-time setup in one command.

v0.10 step 4. The orchestrator that composes the building blocks shipped
in steps 1–3:

- step 1: ``bootstrap.migrate.apply_migrations`` — psql-free SQL runner
- step 2: ``bootstrap.postgres.detect / install_instructions /
  ensure_role_and_database / write_env_file``
- step 3: ``bootstrap.systemd.write_units / is_lingering /
  enable_linger_command / reload_user_daemon``

Top-level flow:

1. Preflight: Python ≥ 3.10, ``uv`` on PATH, the operator is **not** in
   the ``docker`` group (hard-fail per STEVENS.md §2 Principle 14;
   cleaned up + shared with ``doctor`` in step 5).
2. Detect Postgres state. If install/grant is needed, print the operator
   instructions and exit with rc=1. The operator runs the printed sudo
   block themselves; bootstrap never escalates.
3. Otherwise: idempotently provision the assistant role + DB + ``vector``
   extension, apply migrations, write ``~/.config/stevens/env``,
   generate/refresh systemd user units, and request a daemon-reload.
4. Print final state + the one or two follow-up commands the operator
   still needs to run themselves (typically ``stevens secrets init`` and
   ``stevens channels install <name>``).

``--dry-run`` (default mode is *not* dry — bootstrap is opt-out, not
opt-in) skips all mutating calls and just prints what would happen.
"""

from __future__ import annotations

import grp
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from . import migrate, postgres, systemd
from .postgres import DEFAULT_DSN, env_file_path


# ----------------------------- preflight ---------------------------------


@dataclass
class PreflightResult:
    failures: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


def _in_docker_group(user: Optional[str] = None) -> bool:
    """``True`` if the running OS user is a member of the ``docker`` group.

    STEVENS.md §2 Principle 14: docker-group membership is functionally
    passwordless root and is incompatible with running Stevens. This is the
    one preflight check that hard-fails bootstrap.

    Returns ``False`` if there is no ``docker`` group on this system, or
    if we can't determine the user.
    """
    me = user or os.environ.get("USER") or os.environ.get("LOGNAME")
    if not me:
        return False
    try:
        members = grp.getgrnam("docker").gr_mem
    except KeyError:
        return False
    if me in members:
        return True
    # Also check the user's primary group in case docker is somehow primary
    try:
        import pwd

        pw = pwd.getpwnam(me)
    except KeyError:
        return False
    try:
        primary = grp.getgrgid(pw.pw_gid).gr_name
    except KeyError:
        return False
    return primary == "docker"


def preflight() -> PreflightResult:
    """Run the cheap, deterministic checks that gate bootstrap.

    Anything that requires connecting to Postgres or writing files lives
    in the main flow, not here — preflight is supposed to fail fast.
    """
    r = PreflightResult()

    if sys.version_info < (3, 10):
        r.failures.append(
            f"Python 3.10+ required, found {sys.version_info.major}."
            f"{sys.version_info.minor}"
        )

    if shutil.which("uv") is None:
        r.failures.append(
            "`uv` is not on PATH. Install it: "
            "https://docs.astral.sh/uv/getting-started/installation/"
        )

    if _in_docker_group():
        r.failures.append(
            "your OS user is in the `docker` group, which is functionally "
            "passwordless root (you can mount / into a container and chroot "
            "in). Stevens refuses to run on accounts with this privilege. "
            "Remove yourself with: `sudo gpasswd -d $USER docker && newgrp $(id -gn)`"
        )

    if sys.platform != "linux":
        r.warnings.append(
            f"platform '{sys.platform}' — Linux is the v0.10 primary target. "
            "macOS and Windows paths exist but are best-effort."
        )

    return r


# ----------------------------- formatting --------------------------------


def _format_section(title: str, body: str) -> str:
    return f"\n--- {title} ---\n{body}"


# ----------------------------- main flow ---------------------------------


def run_bootstrap(*, dry_run: bool = False, repo_root: Optional[Path] = None) -> int:
    """Execute the bootstrap flow. Returns a Unix exit code.

    rc=0 — host is fully ready (or, in dry-run, would be).
    rc=1 — operator action required (sudo block printed) — re-run after.
    rc=2 — preflight failure or hard error.
    """
    print("Stevens bootstrap — preflight checks.")
    pre = preflight()
    for w in pre.warnings:
        print(f"  ! {w}")
    if not pre.ok:
        print()
        for f in pre.failures:
            print(f"  ✗ {f}")
        print("\npreflight failed — fix the above and re-run.")
        return 2
    print("  ✓ python, uv, docker-group check, platform")

    # ---------------- Postgres detect ------------------
    state = postgres.detect()
    print(_format_section("postgres", postgres.format_state(state)))

    instructions = postgres.install_instructions(state)
    if instructions is not None:
        print()
        print(instructions.sudo_block)
        for note in instructions.notes:
            print(f"\n# {note}")
        print(
            "\nbootstrap paused: run the block above, then re-run "
            "`stevens bootstrap`."
        )
        return 1

    # ---------------- Provision role + DB + extension --
    if dry_run:
        print(_format_section("provision", "(dry-run) would call ensure_role_and_database()"))
    else:
        actions = postgres.ensure_role_and_database()
        if actions:
            print(_format_section("provision", "\n".join(f"  → {a}" for a in actions)))
        else:
            print(_format_section("provision", "  · already provisioned (no-op)"))

    # ---------------- Apply migrations -----------------
    dsn = os.environ.get("DATABASE_URL") or DEFAULT_DSN
    if dry_run:
        print(_format_section("migrations", f"(dry-run) would apply migrations against {dsn}"))
    else:
        n = migrate.apply_migrations(dsn, migrate._resolve_migrations_dir(None))
        print(_format_section("migrations", f"  → applied {n} migration file(s) (idempotent)"))

    # ---------------- Write env file -------------------
    target_env = env_file_path()
    if dry_run:
        print(_format_section("env file", f"(dry-run) would write DATABASE_URL={dsn} to {target_env}"))
    else:
        path, changed = postgres.write_env_file(dsn=dsn)
        verb = "wrote" if changed else "verified"
        print(_format_section("env file", f"  → {verb} {path} (DATABASE_URL={dsn})"))

    # ---------------- systemd user units ---------------
    rr = repo_root or _default_repo_root()
    if sys.platform != "linux":
        print(
            _format_section(
                "systemd",
                "(skipped — non-Linux platform; macOS launchd / Windows scheduled tasks land in a follow-up)",
            )
        )
    elif dry_run:
        print(_format_section("systemd", "(dry-run) would write 6 unit files into ~/.config/systemd/user/"))
    else:
        actions = systemd.write_units(repo_root=rr)
        print(_format_section("systemd", systemd.format_actions(actions)))
        reloaded = systemd.reload_user_daemon()
        if reloaded:
            print("  ✓ systemctl --user daemon-reload")
        else:
            print("  · systemctl --user daemon-reload skipped (no user-session bus)")

        if not systemd.is_lingering():
            cmd = systemd.enable_linger_command()
            print(
                f"\nFor units to start at boot without a login session, "
                f"run once:\n  {cmd}"
            )

    # ---------------- Final summary --------------------
    print()
    print("--- next steps ---")
    print(
        "  1. `stevens secrets init`  — create the sealed store + set the "
        "passphrase (one-time)."
    )
    print(
        "  2. `stevens channels install <name>`  — onboard a channel "
        "(lands in v0.11; until then use the per-channel runbooks under "
        "`docs/runbooks/`)."
    )
    print(
        "  3. `systemctl --user start stevens-security`  — bring up Enkidu "
        "(once the sealed store exists)."
    )
    return 0


def _default_repo_root() -> Path:
    """Repo root from this file's location."""
    return Path(__file__).resolve().parents[4]
