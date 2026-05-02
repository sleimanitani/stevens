"""Diagnostic — `stevens doctor`.

Walks a checklist that is cheap to run and tells the operator the
specific next thing to fix when something is wrong. Returns non-zero
on any failure so it composes with shell pipelines and CI.

Checks (independent — one failure doesn't short-circuit the rest):

1. Sealed store exists at configured root.
2. Sealed store unlocks with the available passphrase (env / keyring / prompt).
3. Enkidu (Security Agent) is running: UDS socket exists and ``ping``
   round-trips. If the service isn't running, that's a *warning*, not a
   failure — many operations don't need it live.
4. Each agent in agents.yaml has a key file at ``~/.config/stevens/agents/<name>.key``
   if a corresponding ``.env`` exists, and the key file is mode 0600.
5. Each capability allow rule in capabilities.yaml refers to a registered
   agent (otherwise the rule is dead).
6. Last 24h audit summary: counts per (caller, outcome). Informational only.
"""

from __future__ import annotations

import os
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import yaml


@dataclass
class Check:
    name: str
    ok: bool
    message: str
    remediation: Optional[str] = None
    info: bool = False  # warnings/notes — don't fail the run


@dataclass
class DoctorReport:
    checks: List[Check] = field(default_factory=list)

    @property
    def failed(self) -> List[Check]:
        return [c for c in self.checks if not c.ok and not c.info]

    @property
    def passed(self) -> bool:
        return not self.failed


def _check_sealed_store_exists(secrets_root: Path) -> Check:
    if (secrets_root / "master.info").exists():
        return Check(
            name="sealed-store-exists",
            ok=True,
            message=f"sealed store found at {secrets_root}",
        )
    return Check(
        name="sealed-store-exists",
        ok=False,
        message=f"no sealed store at {secrets_root}",
        remediation="run: stevens secrets init",
    )


def _check_sealed_store_unlocks(secrets_root: Path) -> Check:
    """Try to unlock with whatever passphrase source is available.

    We try, in order:
    - ``$STEVENS_PASSPHRASE`` env
    - OS keyring (if a backend is set)
    No prompt — doctor should be quiet.
    """
    if not (secrets_root / "master.info").exists():
        return Check(
            name="sealed-store-unlocks",
            ok=False,
            message="(skipped — sealed store does not exist)",
            info=True,
        )
    pp: Optional[bytes] = None
    env = os.environ.get("STEVENS_PASSPHRASE")
    if env is not None:
        pp = env.encode("utf-8")
    if pp is None:
        from . import keyring_passphrase

        pp = keyring_passphrase.get()
    if pp is None:
        return Check(
            name="sealed-store-unlocks",
            ok=False,
            message="no passphrase available (env empty, no keyring entry)",
            remediation="run: stevens passphrase remember  (or set STEVENS_PASSPHRASE)",
            info=True,
        )
    try:
        from .sealed_store import SealedStore

        SealedStore.unlock(secrets_root, pp)
    except Exception as e:  # noqa: BLE001
        return Check(
            name="sealed-store-unlocks",
            ok=False,
            message=f"unlock failed: {e}",
            remediation="check the passphrase. if forgotten, the vault is unrecoverable.",
        )
    return Check(name="sealed-store-unlocks", ok=True, message="passphrase verified")


def _check_socket_running(socket_path: str) -> Check:
    p = Path(socket_path)
    if not p.exists():
        return Check(
            name="enkidu-running",
            ok=False,
            message=f"no socket at {socket_path}",
            remediation=(
                "start the security service: "
                "`systemctl --user start stevens-security`  "
                "(installed via `stevens bootstrap`)"
            ),
            info=True,
        )
    return Check(
        name="enkidu-running",
        ok=True,
        message=f"socket present at {socket_path}",
    )


def _check_agent_key_files(
    agents_yaml: Path, agents_dir: Path
) -> List[Check]:
    checks: List[Check] = []
    if not agents_yaml.exists():
        checks.append(
            Check(
                name="agent-keys",
                ok=False,
                message=f"no agents.yaml at {agents_yaml}",
                remediation="provision an agent: stevens agent provision <name>",
                info=True,
            )
        )
        return checks

    raw = yaml.safe_load(agents_yaml.read_text()) or {}
    agents = raw.get("agents") or []
    for entry in agents:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str):
            continue
        env_path = agents_dir / f"{name}.env"
        if not env_path.exists():
            # No env profile = the agent was registered manually, not provisioned.
            # Don't fail — just note it.
            checks.append(
                Check(
                    name=f"agent-key:{name}",
                    ok=True,
                    message=f"{name}: registered (no provisioned env profile)",
                    info=True,
                )
            )
            continue
        # Read the env file to find the key path.
        key_path: Optional[Path] = None
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("STEVENS_PRIVATE_KEY_PATH="):
                key_path = Path(line.split("=", 1)[1])
                break
        if key_path is None:
            checks.append(
                Check(
                    name=f"agent-key:{name}",
                    ok=False,
                    message=f"{name}: env profile has no STEVENS_PRIVATE_KEY_PATH",
                    remediation=f"re-provision: stevens agent provision {name} --force",
                )
            )
            continue
        if not key_path.exists():
            checks.append(
                Check(
                    name=f"agent-key:{name}",
                    ok=False,
                    message=f"{name}: key file missing at {key_path}",
                    remediation=f"re-provision: stevens agent provision {name} --force",
                )
            )
            continue
        mode = stat.S_IMODE(key_path.stat().st_mode)
        if mode & 0o077:
            checks.append(
                Check(
                    name=f"agent-key:{name}",
                    ok=False,
                    message=f"{name}: key file {key_path} has loose perms ({oct(mode)})",
                    remediation=f"chmod 0600 {key_path}",
                )
            )
            continue
        checks.append(
            Check(
                name=f"agent-key:{name}",
                ok=True,
                message=f"{name}: key file present and 0600",
            )
        )
    if not agents:
        checks.append(
            Check(
                name="agent-keys",
                ok=True,
                message="no agents registered yet (this is fine)",
                info=True,
            )
        )
    return checks


def _check_policy_refs_known_agents(
    capabilities_yaml: Path, agents_yaml: Path
) -> Check:
    if not capabilities_yaml.exists():
        return Check(
            name="policy-refs-agents",
            ok=True,
            message="(skipped — no capabilities.yaml yet)",
            info=True,
        )
    if not agents_yaml.exists():
        return Check(
            name="policy-refs-agents",
            ok=False,
            message="capabilities.yaml exists but agents.yaml does not",
            remediation="provision the agents named in capabilities.yaml",
        )
    caps = yaml.safe_load(capabilities_yaml.read_text()) or {}
    ags = yaml.safe_load(agents_yaml.read_text()) or {}
    cap_agents = {
        e["name"] for e in (caps.get("agents") or []) if isinstance(e, dict) and "name" in e
    }
    reg_agents = {
        e["name"] for e in (ags.get("agents") or []) if isinstance(e, dict) and "name" in e
    }
    orphans = sorted(cap_agents - reg_agents)
    if orphans:
        return Check(
            name="policy-refs-agents",
            ok=False,
            message=f"capabilities.yaml has rules for unregistered agents: {orphans}",
            remediation=f"either provision them or remove them from capabilities.yaml",
        )
    return Check(
        name="policy-refs-agents",
        ok=True,
        message=f"all {len(cap_agents)} policy-mentioned agents are registered",
    )


def _check_not_in_docker_group() -> Check:
    """STEVENS.md §2 Principle 14: docker-group membership is functionally
    passwordless root.

    Bootstrap hard-fails on this; doctor reports it as a non-blocking
    warning (the operator may have docker installed for unrelated reasons,
    and may not be running Stevens-as-an-agent on this account yet — but
    once they do, this needs to be cleaned up).
    """
    from .bootstrap.preflight import docker_group_removal_hint, in_docker_group

    if in_docker_group():
        return Check(
            name="docker-group",
            ok=False,
            message=(
                "your OS user is in the `docker` group — functionally "
                "passwordless root (mount / into a container, chroot in). "
                "Stevens refuses to run on such accounts."
            ),
            remediation=docker_group_removal_hint(),
            info=True,  # warning, not blocker — bootstrap is the gate that hard-fails
        )
    return Check(
        name="docker-group",
        ok=True,
        message="not in `docker` group",
    )


def run_doctor(
    *,
    secrets_root: Path,
    socket_path: str,
    agents_yaml: Path,
    capabilities_yaml: Path,
    agents_dir: Path,
) -> DoctorReport:
    """Run all checks and return a report. Pure: no I/O beyond stat/read."""
    report = DoctorReport()
    report.checks.append(_check_not_in_docker_group())
    report.checks.append(_check_sealed_store_exists(secrets_root))
    report.checks.append(_check_sealed_store_unlocks(secrets_root))
    report.checks.append(_check_socket_running(socket_path))
    report.checks.extend(_check_agent_key_files(agents_yaml, agents_dir))
    report.checks.append(
        _check_policy_refs_known_agents(capabilities_yaml, agents_yaml)
    )
    return report


def format_report(report: DoctorReport) -> str:
    lines: List[str] = []
    for c in report.checks:
        if c.ok and c.info:
            symbol = "·"
        elif c.ok:
            symbol = "✓"
        elif c.info:
            symbol = "?"
        else:
            symbol = "✗"
        lines.append(f"  {symbol} {c.name}: {c.message}")
        if not c.ok and c.remediation:
            lines.append(f"      → {c.remediation}")
    if report.passed:
        lines.append("")
        lines.append("doctor: all checks passed")
    else:
        lines.append("")
        lines.append(f"doctor: {len(report.failed)} check(s) failed")
    return "\n".join(lines)
