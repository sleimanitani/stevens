"""Power-runtime integration — v0.11 step 7.2.

Bridges installed Power plugins (via ``shared.plugins.discovery``) to
the ``Supervisor``. For each Power's declared reactive modes:

- **webhook** → register a ``SupervisedProcess`` running
  ``uvicorn <handler> --host 127.0.0.1 --port <port>``.
- **listener** → register a ``SupervisedProcess`` running
  ``python -c "import asyncio, importlib; asyncio.run(getattr(importlib
  .import_module('mod'), 'attr')())"``.
- **polling** → register an in-process asyncio task that loops:
  sleep(interval) → call the polling command. No subprocess.
- **request-based** → no runtime artifact; the dispatcher handles
  on-demand calls, no setup needed at runtime-start.

Polling design choice: an in-process asyncio task per polling power
rather than reusing the Scheduler Automaton (step 3e.4). Reason:
the Scheduler fires bus events; polling powers want to actually *do
work* on the tick (call their fetch function). Two different
abstractions, both useful, neither replacing the other.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from shared.plugins.discovery import InstalledPlugin, discover
from shared.plugins.manifest import Manifest, Mode

from ..creatures.scheduler import parse_interval
from .supervisor import (
    BackoffPolicy,
    SupervisedProcess,
    Supervisor,
)


# ----------------------------- result types ------------------------------


@dataclass
class PowerRuntimeError:
    """A power that couldn't be wired into the runtime — discovery error,
    missing runtime block, etc. Recorded but non-fatal."""

    power_name: str
    reason: str


@dataclass
class PowerRuntimeRegistration:
    """One power's runtime registration outcome."""

    power_name: str
    process_names: list[str] = field(default_factory=list)   # supervised procs
    polling_tasks: list[str] = field(default_factory=list)   # async-task ids
    skipped_modes: list[str] = field(default_factory=list)   # request-based, etc.


# ----------------------------- the runtime -------------------------------


def _interval_parser(spec: str) -> int:
    """Module-level rebind so tests can monkeypatch this name."""
    return parse_interval(spec)


@dataclass
class PowerRuntime:
    """Translates installed Power plugins into supervised processes /
    polling tasks. Owned and used by the runtime supervisor's startup
    code (step 7.4).

    Holds a reference to a ``Supervisor`` (subprocess management) and an
    internal dict of polling tasks. ``add_power(plugin)`` does the
    registration; ``start_polling()`` starts the async polling tasks
    (the supervisor's ``start_all()`` covers the subprocess side).
    ``stop_polling()`` cancels the polling tasks.
    """

    supervisor: Supervisor
    repo_root: Path = field(default_factory=Path.cwd)
    log_dir: Path = field(
        default_factory=lambda: Path("~/.local/state/demiurge/logs").expanduser()
    )

    _polling_tasks: dict[str, asyncio.Task] = field(
        default_factory=dict, init=False, repr=False
    )
    _polling_specs: dict[str, dict] = field(
        default_factory=dict, init=False, repr=False
    )
    _logger: logging.Logger = field(
        default_factory=lambda: logging.getLogger("demiurge.runtime.power_runtime"),
        init=False,
        repr=False,
    )

    # ----------------------------- discovery -----------------------------

    def discover_and_add_all(
        self,
    ) -> tuple[list[PowerRuntimeRegistration], list[PowerRuntimeError]]:
        """Discover every installed power and register its runtime.

        Returns ``(registrations, errors)``. Discovery errors from the
        plugin loader are surfaced as PowerRuntimeError; manifest errors
        per-power are too.
        """
        registrations: list[PowerRuntimeRegistration] = []
        errors: list[PowerRuntimeError] = []

        result = discover("power")
        for err in result.errors:
            errors.append(
                PowerRuntimeError(
                    power_name=err.name,
                    reason=f"discovery: {err.error}",
                )
            )

        for plugin in result.plugins:
            try:
                reg = self.add_power(plugin)
                registrations.append(reg)
            except Exception as e:  # noqa: BLE001
                errors.append(
                    PowerRuntimeError(
                        power_name=plugin.name,
                        reason=f"{type(e).__name__}: {e}",
                    )
                )

        return registrations, errors

    # ----------------------------- per-power -----------------------------

    def add_power(self, plugin: InstalledPlugin) -> PowerRuntimeRegistration:
        """Wire one power into the runtime.

        Walks the manifest's ``modes`` and registers a supervised
        process / polling task per reactive mode. Idempotent at the
        Supervisor level (its ``add`` overwrites by name).
        """
        manifest = plugin.manifest
        if manifest.kind != "power":
            raise ValueError(
                f"add_power: expected kind=power, got {manifest.kind!r}"
            )

        reg = PowerRuntimeRegistration(power_name=manifest.name)
        for mode in manifest.modes or []:
            if mode == Mode.WEBHOOK:
                name = self._register_webhook(manifest)
                reg.process_names.append(name)
            elif mode == Mode.LISTENER:
                name = self._register_listener(manifest)
                reg.process_names.append(name)
            elif mode == Mode.POLLING:
                task_id = self._register_polling(manifest)
                reg.polling_tasks.append(task_id)
            elif mode == Mode.REQUEST_BASED:
                reg.skipped_modes.append(mode.value)
            else:
                # Unknown mode — should be unreachable given the manifest
                # validator, but be defensive.
                reg.skipped_modes.append(f"{mode.value} (unknown)")

        return reg

    # ----------------------------- subprocess shapes ---------------------

    def _register_webhook(self, manifest: Manifest) -> str:
        rt = manifest.runtime.webhook  # type: ignore[union-attr]
        assert rt is not None
        name = f"demiurge-power-{manifest.name}"
        cmd = [
            "uvicorn",
            rt.handler,
            "--host",
            "127.0.0.1",
            "--port",
            str(rt.port),
        ]
        self.supervisor.add(
            SupervisedProcess(
                name=name,
                cmd=cmd,
                cwd=self.repo_root,
                restart_policy="on-failure",
                backoff=BackoffPolicy(),
                log_path=self.log_dir / f"{name}.log",
            )
        )
        return name

    def _register_listener(self, manifest: Manifest) -> str:
        rt = manifest.runtime.listener  # type: ignore[union-attr]
        assert rt is not None
        name = f"demiurge-power-{manifest.name}"
        module, attr = rt.command.split(":", 1)
        # Use `python -c` so listener plugins don't have to ship a __main__.py.
        cmd = [
            "python",
            "-c",
            (
                "import asyncio, importlib; "
                f"m = importlib.import_module({module!r}); "
                f"asyncio.run(getattr(m, {attr!r})())"
            ),
        ]
        self.supervisor.add(
            SupervisedProcess(
                name=name,
                cmd=cmd,
                cwd=self.repo_root,
                restart_policy=rt.restart,
                backoff=BackoffPolicy(),
                log_path=self.log_dir / f"{name}.log",
            )
        )
        return name

    def _register_polling(self, manifest: Manifest) -> str:
        rt = manifest.runtime.polling  # type: ignore[union-attr]
        assert rt is not None
        interval_seconds = _interval_parser(rt.interval)
        task_id = f"demiurge-poll-{manifest.name}"
        self._polling_specs[task_id] = {
            "manifest_name": manifest.name,
            "command": rt.command,
            "interval_seconds": interval_seconds,
        }
        return task_id

    # ----------------------------- polling lifecycle ---------------------

    async def start_polling(self) -> None:
        """Start every registered polling task as an asyncio.Task.

        Tasks loop: sleep(interval) → call the command → repeat.
        Exceptions inside the command are logged but don't kill the
        task; the next tick fires normally.
        """
        for task_id, spec in self._polling_specs.items():
            if task_id in self._polling_tasks and not self._polling_tasks[task_id].done():
                continue  # already running
            self._polling_tasks[task_id] = asyncio.create_task(
                self._poll_loop(task_id, spec),
                name=task_id,
            )

    async def stop_polling(self) -> None:
        """Cancel every polling task and wait for them to settle."""
        for task in self._polling_tasks.values():
            if not task.done():
                task.cancel()
        for task in self._polling_tasks.values():
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._polling_tasks.clear()

    async def _poll_loop(self, task_id: str, spec: dict) -> None:
        """The body of one polling task. Runs until cancelled."""
        interval = spec["interval_seconds"]
        command = spec["command"]
        while True:
            try:
                await asyncio.sleep(interval)
                await self._invoke_polling_command(command)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self._logger.warning(
                    "polling task %r raised %s: %s; continuing",
                    task_id,
                    type(e).__name__,
                    e,
                )

    async def _invoke_polling_command(self, command: str) -> None:
        """Resolve ``module:attr`` and call. Async-aware.

        Plugins typically expose a coroutine ``async def run_once()``; we
        also accept a sync function (called directly).
        """
        module_path, attr = command.split(":", 1)
        module = importlib.import_module(module_path)
        fn = getattr(module, attr)
        if asyncio.iscoroutinefunction(fn):
            await fn()
        else:
            # Sync function; run in default executor to keep us off the
            # event loop's thread.
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, fn)
