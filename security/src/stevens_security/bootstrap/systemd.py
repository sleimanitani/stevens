"""Generate systemd user-unit files for Stevens services.

v0.10 step 3. Replaces ``compose.yaml`` as the canonical install path on
Linux. Services run as the operator's own OS user (no root, no
``docker`` group), under their personal systemd user instance, with
``loginctl enable-linger`` so units start at boot without a login.

Why user units, not system units:
- No sudo on install. The whole point of v0.10 is that everyday operation
  needs zero elevated privileges. System units would put us back to
  ``sudo systemctl start stevens-security`` for every change.
- Files live in ``~/.config/systemd/user/``, owned by the operator. No
  ``/etc/systemd/system`` writes, no ``systemctl daemon-reload`` requiring
  root.
- ``loginctl enable-linger`` is a *one-time* per-user grant that survives
  reboots, and the operator types their own password for that — not
  Stevens' problem to escalate.

Scope of this module:
- Linux only. macOS launchd plists and Windows scheduled tasks are stubbed
  with ``NotImplementedError`` and a clear message — they land in a follow-up
  step or milestone, after the Linux path is dialed in. Sol's box is Linux,
  v0.10's acceptance gate is Linux.
- Whatsapp Web (the Node.js channel) is excluded — it's getting migrated to
  a v0.11 plugin and shouldn't be ossified into in-tree systemd units.
- Postgres is *not* in the catalog — it's an OS-managed service via the
  PGDG package, started by the system-level systemd unit ``postgresql@16-main``.
- Langfuse is *not* in the catalog — it's a developer-time observability
  surface; for v0.10 we leave that to the developer compose path
  (``dev/compose.yaml`` after step 6).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional

from .postgres import env_file_path

DEFAULT_RESTART_SEC = 2
UNIT_PREFIX = "stevens-"


# ----------------------------- catalog ------------------------------------


@dataclass(frozen=True)
class ServiceUnit:
    """Definition of one Stevens service that should run under systemd.

    ``exec_cmd`` is the literal command that follows ``uv run --directory
    <repo>`` in ``ExecStart=``. Don't include ``uv run`` in it.

    ``after`` is the list of systemd unit names this service should start
    after (e.g. ``stevens-security.service``). The Postgres dependency is
    expressed as ``postgresql.service`` (the system-level unit installed by
    PGDG).
    """

    name: str
    description: str
    exec_cmd: str
    after: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    extra_env: tuple[tuple[str, str], ...] = ()


SECURITY_UNIT = "stevens-security.service"
POSTGRES_UNIT = "postgresql.service"

# The order here matches the dependency DAG: security first (no deps),
# then channel adapters (which talk to security via UDS), then the agents
# runtime (which talks to all the adapters).
DEFAULT_SERVICES: tuple[ServiceUnit, ...] = (
    ServiceUnit(
        name="stevens-security",
        description="Stevens Security Agent (Enkidu) — sole secret broker",
        exec_cmd="python -m stevens_security",
    ),
    ServiceUnit(
        name="stevens-gmail-adapter",
        description="Stevens — Gmail channel adapter",
        exec_cmd="uvicorn gmail_adapter.main:app --host 127.0.0.1 --port 8080",
        after=(SECURITY_UNIT, POSTGRES_UNIT),
    ),
    ServiceUnit(
        name="stevens-calendar-adapter",
        description="Stevens — Google Calendar channel adapter",
        exec_cmd="uvicorn calendar_adapter.main:app --host 127.0.0.1 --port 8083",
        after=(SECURITY_UNIT, POSTGRES_UNIT),
        extra_env=(
            ("STEVENS_CALLER_NAME", "calendar_adapter"),
        ),
    ),
    ServiceUnit(
        name="stevens-whatsapp-cloud-adapter",
        description="Stevens — WhatsApp Cloud channel adapter",
        exec_cmd="uvicorn whatsapp_cloud_adapter.main:app --host 127.0.0.1 --port 8082",
        after=(SECURITY_UNIT, POSTGRES_UNIT),
        extra_env=(
            ("STEVENS_CALLER_NAME", "whatsapp_cloud_adapter"),
        ),
    ),
    ServiceUnit(
        name="stevens-signal-adapter",
        description="Stevens — Signal channel adapter",
        exec_cmd="python -m signal_adapter",
        after=(SECURITY_UNIT, POSTGRES_UNIT),
        extra_env=(
            ("STEVENS_CALLER_NAME", "signal_adapter"),
        ),
    ),
    ServiceUnit(
        name="stevens-agents",
        description="Stevens — agents runtime (Mortals + supervisors)",
        exec_cmd="python -m agents.runtime",
        after=(SECURITY_UNIT, POSTGRES_UNIT),
    ),
)


# ----------------------------- paths --------------------------------------


def systemd_user_dir() -> Path:
    """``~/.config/systemd/user/`` (XDG-aware)."""
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "systemd" / "user"


# ----------------------------- generation ---------------------------------


def render_unit(service: ServiceUnit, *, repo_root: Path, env_file: Path) -> str:
    """Return the contents of one ``*.service`` file for ``service``.

    Pure: no I/O. Path arguments are baked into the unit file text.
    """
    after_line = ""
    if service.after:
        after_line = f"After={' '.join(service.after)}\n"
    requires_line = ""
    if service.requires:
        requires_line = f"Requires={' '.join(service.requires)}\n"

    extra_env_lines = "".join(
        f'Environment="{k}={v}"\n' for k, v in service.extra_env
    )

    return (
        f"[Unit]\n"
        f"Description={service.description}\n"
        f"{after_line}"
        f"{requires_line}"
        f"\n"
        f"[Service]\n"
        f"Type=simple\n"
        f"WorkingDirectory={repo_root}\n"
        f"EnvironmentFile=-{env_file}\n"
        f"{extra_env_lines}"
        f"ExecStart=uv run --directory {repo_root} {service.exec_cmd}\n"
        f"Restart=on-failure\n"
        f"RestartSec={DEFAULT_RESTART_SEC}\n"
        f"\n"
        f"[Install]\n"
        f"WantedBy=default.target\n"
    )


def _read_or_none(path: Path) -> Optional[str]:
    try:
        return path.read_text()
    except FileNotFoundError:
        return None


def write_units(
    *,
    repo_root: Path,
    target_dir: Optional[Path] = None,
    env_file: Optional[Path] = None,
    services: Iterable[ServiceUnit] = DEFAULT_SERVICES,
) -> List[tuple[Path, str]]:
    """Write all unit files into ``target_dir``.

    Returns a list of ``(path, action)`` where action is ``"created"``,
    ``"updated"``, or ``"unchanged"``. Idempotent: running twice with the
    same inputs reports every unit as ``unchanged``.

    The function does NOT call ``systemctl daemon-reload`` itself — that's
    a runtime concern handled by ``reload_user_daemon()`` so tests can write
    units against a tmp path without touching the live systemd instance.
    """
    if sys.platform.startswith("win"):
        raise NotImplementedError("Windows uses scheduled tasks, not systemd — not yet implemented")
    if sys.platform == "darwin":
        raise NotImplementedError("macOS uses launchd, not systemd — not yet implemented")

    tdir = target_dir if target_dir is not None else systemd_user_dir()
    efile = env_file if env_file is not None else env_file_path()
    tdir.mkdir(parents=True, exist_ok=True)

    actions: List[tuple[Path, str]] = []
    for s in services:
        unit_path = tdir / f"{s.name}.service"
        new_text = render_unit(s, repo_root=repo_root, env_file=efile)
        old_text = _read_or_none(unit_path)
        if old_text == new_text:
            actions.append((unit_path, "unchanged"))
            continue
        unit_path.write_text(new_text)
        actions.append((unit_path, "updated" if old_text is not None else "created"))
    return actions


# ----------------------------- runtime hooks ------------------------------


def is_lingering(user: Optional[str] = None) -> bool:
    """``True`` if ``loginctl enable-linger`` has already been done for ``user``.

    Returns ``False`` if ``loginctl`` isn't on PATH (treat as "we don't know,
    so the operator should run the enable command to be safe").
    """
    me = user or os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    if not me or shutil.which("loginctl") is None:
        return False
    try:
        proc = subprocess.run(
            ["loginctl", "show-user", me, "--property=Linger"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "Linger=yes" in proc.stdout


def enable_linger_command(user: Optional[str] = None) -> str:
    """Return the exact one-liner the operator should run to enable lingering.

    We don't run this ourselves — it requires sudo, and v0.10 keeps
    bootstrap sudo-free.
    """
    me = user or os.environ.get("USER") or os.environ.get("LOGNAME") or "$USER"
    return f"sudo loginctl enable-linger {me}"


def reload_user_daemon() -> bool:
    """``systemctl --user daemon-reload``. Returns True on success.

    Caller is expected to ignore failures in non-systemd environments
    (CI containers, dev sandboxes) — this function returns False in
    those cases rather than raising.
    """
    if shutil.which("systemctl") is None:
        return False
    try:
        rc = subprocess.run(
            ["systemctl", "--user", "daemon-reload"],
            capture_output=True,
            timeout=5,
            check=False,
        ).returncode
    except (OSError, subprocess.TimeoutExpired):
        return False
    return rc == 0


# ----------------------------- formatting ---------------------------------


def format_actions(actions: Iterable[tuple[Path, str]]) -> str:
    lines: List[str] = []
    for path, verb in actions:
        symbol = {"created": "+", "updated": "~", "unchanged": "·"}.get(verb, "?")
        lines.append(f"  {symbol} {path.name}: {verb}  ({path})")
    return "\n".join(lines)


# ----------------------------- CLI ----------------------------------------


def _default_repo_root() -> Path:
    """Repo root inferred from this file's location.

    Same trick as ``bootstrap.migrate``: works for editable installs,
    fails loudly when packaged from a wheel. Callers should pass
    ``--repo-root`` explicitly in non-editable installs.
    """
    return Path(__file__).resolve().parents[4]


def main(argv: Optional[Iterable[str]] = None) -> int:
    """``python -m stevens_security.bootstrap.systemd`` entrypoint.

    Default mode: print what the per-service units would look like (dry-run).
    ``--write``: actually write them into ``~/.config/systemd/user/`` and
    request a daemon-reload. Print the linger command if it's not already
    enabled.
    """
    import argparse

    p = argparse.ArgumentParser(
        prog="stevens_security.bootstrap.systemd",
        description="Generate systemd user-unit files for Stevens services.",
    )
    p.add_argument(
        "--write",
        action="store_true",
        help="actually write into ~/.config/systemd/user/ (default: dry-run)",
    )
    p.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="repo root (default: inferred from this file's location)",
    )
    p.add_argument(
        "--target-dir",
        type=Path,
        default=None,
        help="override systemd user dir (default: ~/.config/systemd/user/)",
    )
    args = p.parse_args(list(argv) if argv is not None else None)

    if sys.platform != "linux":
        print(
            f"systemd user units are Linux-only. "
            f"Detected platform: {sys.platform}.",
            file=sys.stderr,
        )
        return 2

    repo_root = args.repo_root or _default_repo_root()
    env_file = env_file_path()

    if not args.write:
        print("Dry-run — would write the following units:")
        for s in DEFAULT_SERVICES:
            unit_text = render_unit(s, repo_root=repo_root, env_file=env_file)
            print(f"\n# ----- {s.name}.service -----")
            print(unit_text.rstrip())
        print(
            "\n(re-run with --write to actually create them in ~/.config/systemd/user/)"
        )
        return 0

    actions = write_units(
        repo_root=repo_root,
        target_dir=args.target_dir,
    )
    print(format_actions(actions))

    reloaded = reload_user_daemon()
    if reloaded:
        print("\nsystemctl --user daemon-reload: ok")
    else:
        print(
            "\nsystemctl --user daemon-reload: skipped or failed "
            "(safe to ignore in non-systemd environments)"
        )

    if not is_lingering():
        cmd = enable_linger_command()
        print(
            f"\nFor units to start at boot without a login session, run once:\n"
            f"  {cmd}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
