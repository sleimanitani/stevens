"""Creature-runtime integration — v0.11 step 7.3.

Bridges spawned Creatures (from agents.yaml + their per-Creature env
profiles) to the ``Supervisor``. Symmetric to ``PowerRuntime`` but for
the Creature side: every spawned Mortal / Beast / Automaton becomes
one ``SupervisedProcess`` plus one audit-angel observer task.

For v0.11 the subprocess command is a placeholder
``python -m demiurge.runtime.creature_main <creature_id>``. The real
Mortal/Beast/Automaton main lands with step 9 (the email_pm migration).
For step 7.3 the placeholder lets us exercise:

- Subprocess registration with the right cmd shape per kind.
- Audit-angel observer task wiring (one per Creature; tails the feed
  via the existing ``AuditAngel`` from step 3e.3).
- Restart-on-failure behavior of Creature processes (because the
  placeholder mortal exits cleanly each tick — useful as a self-test).

Pause/resume isn't in this step: ``demiurge hire pause`` runs as a CLI
invocation (separate OS process) while the Supervisor runs as a long-
lived daemon (step 7.4). They need an IPC channel that doesn't exist
yet. v0.11.x or step 7.4 will add a UDS or marker-file IPC. For now
``cli_hire``'s pause/resume remain stubs.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from shared.creatures.context import AngelContext
from shared.creatures.feed import ObservationFeed

from ..audit import AuditWriter
from ..pantheon.hephaestus.audit_angel import AuditAngel
from .supervisor import (
    BackoffPolicy,
    SupervisedProcess,
    Supervisor,
)


# ----------------------------- result types ------------------------------


@dataclass
class CreatureRuntimeError:
    creature_id: str
    reason: str


@dataclass
class CreatureRuntimeRegistration:
    creature_id: str
    kind: str
    process_name: str
    angel_task_id: str


# ----------------------------- registry-shape parsing --------------------


_CREATURE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]*\.[a-z][a-z0-9_]*$")


def _spawned_creature_ids_from_agents_yaml(agents_yaml: Path) -> list[str]:
    """Re-implementation of the same helper in ``cli_hire``: pick names
    matching ``<manifest>.<instance>`` shape from agents.yaml.

    Duplicated rather than imported because cli_hire is a CLI module
    that imports this side, and a circular dep would fight the linter.
    Keep the regex in lockstep with ``cli_hire._CREATURE_ID_RE``.
    """
    if not agents_yaml.exists():
        return []
    data = yaml.safe_load(agents_yaml.read_text()) or {}
    if not isinstance(data, dict):
        return []
    agents = data.get("agents") or []
    if not isinstance(agents, list):
        return []
    return sorted(
        e["name"]
        for e in agents
        if isinstance(e, dict)
        and isinstance(e.get("name"), str)
        and _CREATURE_ID_RE.match(e["name"])
    )


# ----------------------------- placeholder cmd shape ---------------------


def _default_creature_cmd(creature_id: str) -> list[str]:
    """Placeholder subprocess command for v0.11.

    Real Mortal/Beast/Automaton mains land with step 9 (when we migrate
    email_pm to a plugin and ship a real ``creature_main`` module). For
    7.3 we wire an inline `python -c` heartbeat that simulates a long-
    running creature: prints a startup line, sleeps, exits — exercises
    the supervisor lifecycle without needing the real runtime.

    Operators can override per-creature via
    ``CreatureRuntime.add_creature(cmd_override=)``.
    """
    return [
        "python",
        "-c",
        (
            "import sys, time; "
            f"sys.stdout.write({creature_id!r} + ' booted\\n'); "
            "sys.stdout.flush(); "
            # Run for a long time so the supervisor sees a steady
            # process. A real Mortal main loops on bus events.
            "time.sleep(86400)"
        ),
    ]


# ----------------------------- the runtime -------------------------------


@dataclass
class CreatureRuntime:
    """Translates spawned Creatures into supervised processes + audit-
    angel observers.

    Holds a reference to a ``Supervisor`` (for subprocess management)
    and an ``AuditWriter`` (for the audit-angel projections). Also
    keeps the per-Creature audit-angel async tasks.
    """

    supervisor: Supervisor
    audit_writer: AuditWriter
    repo_root: Path = field(default_factory=Path.cwd)
    log_dir: Path = field(
        default_factory=lambda: Path("~/.local/state/demiurge/logs").expanduser()
    )
    feed_base: Optional[Path] = None
    audit_observe_interval: float = 5.0
    """Seconds between audit-angel observe() calls. v0.13's out-of-process
    angel will tail the file directly; for now we poll."""

    _angel_tasks: dict[str, asyncio.Task] = field(
        default_factory=dict, init=False, repr=False
    )
    _angels: dict[str, AuditAngel] = field(
        default_factory=dict, init=False, repr=False
    )
    _logger: logging.Logger = field(
        default_factory=lambda: logging.getLogger("demiurge.runtime.creature_runtime"),
        init=False,
        repr=False,
    )

    # ----------------------------- registration --------------------------

    def add_creature(
        self,
        *,
        creature_id: str,
        kind: str = "mortal",
        cmd_override: Optional[list[str]] = None,
        env: Optional[dict[str, str]] = None,
    ) -> CreatureRuntimeRegistration:
        """Wire one spawned Creature into the runtime.

        Side effects: registers a ``SupervisedProcess`` with the
        supervisor; constructs (but does not start) an audit-angel
        observer for the Creature's feed.

        Idempotent at the supervisor level (its ``add`` overwrites by
        name). The angel task is replaced if re-added.
        """
        process_name = f"demiurge-creature-{creature_id}"
        cmd = cmd_override or _default_creature_cmd(creature_id)

        self.supervisor.add(
            SupervisedProcess(
                name=process_name,
                cmd=cmd,
                cwd=self.repo_root,
                env=env,
                restart_policy="on-failure",
                backoff=BackoffPolicy(),
                log_path=self.log_dir / f"{process_name}.log",
            )
        )

        # Build the angel + its context. The angel is attached to the
        # Creature's existing feed (created by Hephaestus during forge).
        host_feed = ObservationFeed(creature_id, base=self.feed_base)
        angel_ctx = AngelContext(
            creature_id=f"enkidu.audit.{creature_id}",
            display_name=f"Enkidu Audit Angel — {creature_id}",
            audit=host_feed,
            logger=self._logger,
            god="enkidu",
            angel_name="audit",
            host_creature_id=creature_id,
            host_feed=host_feed,
        )
        self._angels[creature_id] = AuditAngel(
            ctx=angel_ctx, audit_writer=self.audit_writer
        )

        return CreatureRuntimeRegistration(
            creature_id=creature_id,
            kind=kind,
            process_name=process_name,
            angel_task_id=f"angel:{creature_id}",
        )

    # ----------------------------- discovery -----------------------------

    def discover_and_add_all(
        self,
        agents_yaml: Path,
    ) -> tuple[list[CreatureRuntimeRegistration], list[CreatureRuntimeError]]:
        """Read agents.yaml, find every spawned Creature, register each.

        Kind defaults to "mortal" — refining per actual kind requires
        consulting the plugin's manifest, which is the supervisor's
        responsibility at startup (step 7.4 wiring). For 7.3 we treat
        every spawned Creature uniformly; behavior differences (Mortal
        vs Beast vs Automaton) live in their respective subprocess
        mains, not here.
        """
        registrations: list[CreatureRuntimeRegistration] = []
        errors: list[CreatureRuntimeError] = []

        for cid in _spawned_creature_ids_from_agents_yaml(agents_yaml):
            try:
                registrations.append(self.add_creature(creature_id=cid))
            except Exception as e:  # noqa: BLE001
                errors.append(
                    CreatureRuntimeError(
                        creature_id=cid,
                        reason=f"{type(e).__name__}: {e}",
                    )
                )

        return registrations, errors

    # ----------------------------- angel observation ---------------------

    async def start_angels(self) -> None:
        """Start a per-Creature observer task for every registered angel.

        Each task loops: sleep(audit_observe_interval) → angel.observe().
        Exceptions inside ``observe()`` are caught + logged; next tick
        fires normally.
        """
        for creature_id, angel in self._angels.items():
            if creature_id in self._angel_tasks and not self._angel_tasks[creature_id].done():
                continue
            self._angel_tasks[creature_id] = asyncio.create_task(
                self._angel_loop(creature_id, angel),
                name=f"angel:{creature_id}",
            )

    async def stop_angels(self) -> None:
        """Cancel every observer task and wait for cleanup."""
        for task in self._angel_tasks.values():
            if not task.done():
                task.cancel()
        for task in self._angel_tasks.values():
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._angel_tasks.clear()

    async def _angel_loop(self, creature_id: str, angel: AuditAngel) -> None:
        while True:
            try:
                await asyncio.sleep(self.audit_observe_interval)
                await angel.observe()
            except asyncio.CancelledError:
                # Final flush before shutdown — best effort.
                try:
                    await angel.observe()
                except Exception:  # noqa: BLE001
                    pass
                raise
            except Exception as e:  # noqa: BLE001
                self._logger.warning(
                    "audit angel for %r raised %s: %s; continuing",
                    creature_id,
                    type(e).__name__,
                    e,
                )
