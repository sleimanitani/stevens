"""Subprocess supervisor — v0.11 step 7.1.

Manages a set of named subprocesses with restart-on-failure backoff,
pause/resume, and per-process log capture. Async-driven: each
supervised process has its own watcher coroutine that:

1. Starts the process (Popen → asyncio).
2. Awaits its exit.
3. Decides whether to restart based on:
   - restart_policy (``"on-failure"`` / ``"always"`` / ``"no"``)
   - desired_state (``"running"`` / ``"paused"`` / ``"stopped"``)
   - backoff state (exponential delay; reset after long-enough success)
   - max_restarts cap (None = unlimited)
4. Sleeps the backoff delay if a restart is appropriate.
5. Loops.

The supervisor itself is one Python task that spawns / cancels watcher
tasks. Stopping the supervisor sends SIGTERM to every running process,
waits up to ``timeout`` seconds, then SIGKILLs anything that didn't exit.

Logs go to a per-process file at ``log_path``: stdout + stderr both
captured into one stream, opened append-mode each restart so the file
grows across restart cycles.

This module is the *substrate* of the runtime — it knows nothing about
manifests, blessings, observation feeds, or angels. It manages
processes. Higher layers (step 7.2+) translate forged-state into
``SupervisedProcess`` records and hand them in via ``add()``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional


# ----------------------------- exceptions -------------------------------


class ProcessNotFound(KeyError):
    """No process with that name registered with the supervisor."""


class SupervisorClosed(RuntimeError):
    """Operation attempted on a supervisor that's been stopped."""


# ----------------------------- types ------------------------------------


RestartPolicy = Literal["on-failure", "always", "no"]
DesiredState = Literal["running", "paused", "stopped"]


@dataclass
class BackoffPolicy:
    """Exponential backoff parameters for restart-on-failure.

    ``initial_delay`` is the first sleep after an exit. Each subsequent
    failed exit multiplies the delay by ``multiplier`` up to
    ``max_delay``. A successful run lasting at least ``reset_after``
    seconds resets the backoff to ``initial_delay``.

    ``max_restarts`` caps how many times we'll restart in a single
    "burst" (consecutive failures with reset_after never reached).
    None = unlimited; 0 = never restart.
    """

    initial_delay: float = 1.0
    max_delay: float = 60.0
    multiplier: float = 2.0
    reset_after: float = 60.0
    max_restarts: Optional[int] = None


@dataclass
class ProcessStatus:
    """Operator-readable snapshot of one supervised process."""

    name: str
    desired_state: DesiredState
    is_running: bool
    pid: Optional[int]
    last_started_at: Optional[float]
    last_exited_at: Optional[float]
    last_exit_code: Optional[int]
    restart_count: int
    log_path: Path


@dataclass
class SupervisedProcess:
    """One named subprocess the supervisor manages.

    ``cmd`` is a list of strings (passed to ``asyncio.create_subprocess_exec``).
    ``cwd`` is the working directory. ``env`` is the full environment;
    None means inherit the supervisor's environment.

    Internal state below ``log_path`` is mutated by the supervisor and
    must not be read concurrently — use ``Supervisor.status()`` for a
    coherent snapshot.
    """

    name: str
    cmd: list[str]
    cwd: Optional[Path] = None
    env: Optional[dict[str, str]] = None
    restart_policy: RestartPolicy = "on-failure"
    backoff: BackoffPolicy = field(default_factory=BackoffPolicy)
    log_path: Optional[Path] = None

    # Runtime state — mutated by the supervisor, read via Supervisor.status().
    process: Optional[asyncio.subprocess.Process] = None
    desired_state: DesiredState = "running"
    last_started_at: Optional[float] = None
    last_exited_at: Optional[float] = None
    last_exit_code: Optional[int] = None
    restart_count: int = 0
    consecutive_failures: int = 0
    current_backoff: float = 0.0


# ----------------------------- supervisor -------------------------------


class Supervisor:
    """Manages a set of named subprocesses with restart-on-failure backoff.

    Lifecycle::

        sup = Supervisor()
        sup.add(SupervisedProcess(name="gmail", cmd=["uvicorn", "..."], ...))
        sup.add(SupervisedProcess(name="agents", cmd=["python", "-m", "..."]))
        await sup.start_all()
        # ... runs until ...
        await sup.stop_all()

    Or for tests + dynamic management::

        sup.add(...)         # registers; doesn't start
        await sup.start("gmail")    # starts one
        await sup.pause("gmail")    # SIGTERM, no auto-restart
        await sup.resume("gmail")   # restart
        await sup.stop("gmail")     # final stop, removes from registry on stop_all

    The supervisor doesn't enforce a single-instance constraint per
    name — re-adding overwrites the registry entry. Tests rely on
    that for stateful scenarios.
    """

    def __init__(self, *, logger: Optional[logging.Logger] = None):
        self._procs: dict[str, SupervisedProcess] = {}
        self._watchers: dict[str, asyncio.Task] = {}
        self._closed = False
        self._lock = asyncio.Lock()
        self._logger = logger or logging.getLogger("demiurge.runtime.supervisor")

    # ----------------------------- registry -----------------------------

    def add(self, proc: SupervisedProcess) -> None:
        """Register a process. Doesn't start it — call ``start`` /
        ``start_all`` for that.

        If a process with the same name is already registered, the new
        entry replaces the old. The old watcher (if any) is left
        alone — ``stop`` it first if needed.
        """
        if self._closed:
            raise SupervisorClosed("supervisor is stopped; can't add new processes")
        self._procs[proc.name] = proc

    def remove(self, name: str) -> None:
        """Drop a process from the registry. Doesn't stop it — call
        ``stop`` first if it's running."""
        self._procs.pop(name, None)

    def names(self) -> list[str]:
        return sorted(self._procs.keys())

    def get(self, name: str) -> SupervisedProcess:
        try:
            return self._procs[name]
        except KeyError:
            raise ProcessNotFound(name) from None

    # ----------------------------- status -------------------------------

    def status(self) -> list[ProcessStatus]:
        """Snapshot of every registered process. Stable name order."""
        return [
            ProcessStatus(
                name=p.name,
                desired_state=p.desired_state,
                is_running=p.process is not None and p.process.returncode is None,
                pid=p.process.pid if p.process is not None else None,
                last_started_at=p.last_started_at,
                last_exited_at=p.last_exited_at,
                last_exit_code=p.last_exit_code,
                restart_count=p.restart_count,
                log_path=p.log_path or Path("/dev/null"),
            )
            for p in (self._procs[n] for n in sorted(self._procs.keys()))
        ]

    # ----------------------------- per-process control ------------------

    async def start(self, name: str) -> None:
        """Start a process by name (or re-start one that's not currently running).

        Idempotent for a process whose desired_state is already
        ``running`` and that has a live watcher.
        """
        if self._closed:
            raise SupervisorClosed("supervisor is stopped")
        proc = self.get(name)
        proc.desired_state = "running"
        if name in self._watchers and not self._watchers[name].done():
            # Already supervised — the watcher's loop will respect the
            # new desired_state automatically.
            return
        self._watchers[name] = asyncio.create_task(
            self._watch(proc), name=f"watch:{name}"
        )

    async def start_all(self) -> None:
        """Start every registered process whose desired_state is ``running``."""
        for name in self.names():
            proc = self._procs[name]
            if proc.desired_state == "running":
                await self.start(name)

    async def pause(self, name: str, *, timeout: float = 10.0) -> None:
        """Send SIGTERM and don't auto-restart.

        Watcher remains live; resume() will spawn a fresh process.
        """
        proc = self.get(name)
        proc.desired_state = "paused"
        await self._signal_and_wait(proc, signal.SIGTERM, timeout=timeout)

    async def resume(self, name: str) -> None:
        """Set desired_state back to running. The watcher loop will
        respawn on the next iteration."""
        proc = self.get(name)
        proc.desired_state = "running"
        if name not in self._watchers or self._watchers[name].done():
            await self.start(name)

    async def stop(self, name: str, *, timeout: float = 10.0) -> None:
        """Stop a process and tear down its watcher."""
        proc = self.get(name)
        proc.desired_state = "stopped"
        await self._signal_and_wait(proc, signal.SIGTERM, timeout=timeout)
        watcher = self._watchers.pop(name, None)
        if watcher is not None and not watcher.done():
            watcher.cancel()
            try:
                await watcher
            except (asyncio.CancelledError, Exception):
                pass

    # ----------------------------- bulk lifecycle -----------------------

    async def stop_all(self, *, timeout: float = 10.0) -> None:
        """Stop every running process and tear down every watcher.

        After stop_all the supervisor is closed — further add() / start()
        calls raise SupervisorClosed. (Use a fresh Supervisor instance
        for a new lifecycle; this matches systemd's "service stopped =
        new ExecStart for next start" model.)
        """
        for name in list(self._procs.keys()):
            try:
                await self.stop(name, timeout=timeout)
            except ProcessNotFound:
                pass
        self._closed = True

    # ----------------------------- the watcher --------------------------

    async def _watch(self, proc: SupervisedProcess) -> None:
        """Per-process watcher loop. Started/cancelled by start/stop.

        Loop body:
        1. If desired_state != "running", exit.
        2. Spawn the process (or skip if max_restarts exhausted).
        3. await proc exit.
        4. Update last_exited_at + last_exit_code + counters.
        5. Decide whether to restart based on policy + desired_state.
        6. If restarting, sleep the backoff delay.

        Cancellation kills the underlying process via SIGTERM (best-effort).
        """
        while True:
            if proc.desired_state != "running":
                return

            if proc.backoff.max_restarts is not None and (
                proc.consecutive_failures >= proc.backoff.max_restarts
            ):
                self._logger.warning(
                    "process %r hit max_restarts=%d; not restarting",
                    proc.name,
                    proc.backoff.max_restarts,
                )
                return

            try:
                await self._spawn(proc)
            except FileNotFoundError as e:
                # Bad cmd — can't even spawn. Treat as a permanent failure;
                # don't loop on it.
                self._logger.error(
                    "process %r failed to spawn (FileNotFoundError: %s); "
                    "not retrying", proc.name, e,
                )
                proc.last_exited_at = time.time()
                proc.last_exit_code = -1
                return
            except Exception as e:  # noqa: BLE001
                self._logger.error(
                    "process %r failed to spawn (%s: %s)",
                    proc.name,
                    type(e).__name__,
                    e,
                )
                proc.last_exited_at = time.time()
                proc.last_exit_code = -1
                # Treat as a transient failure — apply backoff and retry.
                proc.consecutive_failures += 1
                await self._apply_backoff(proc)
                continue

            try:
                exit_code = await proc.process.wait()
            except asyncio.CancelledError:
                # Watcher cancelled — kill the process and exit cleanly.
                if proc.process is not None and proc.process.returncode is None:
                    try:
                        proc.process.terminate()
                    except ProcessLookupError:
                        pass
                raise

            proc.last_exited_at = time.time()
            proc.last_exit_code = exit_code

            # Did this run last long enough to count as "healthy"? If so,
            # reset the failure counter + backoff. We treat anything ≥
            # reset_after as a clean run regardless of exit code — a
            # service that's "always" restarted on clean exit but stays
            # up for an hour each time isn't crash-looping.
            long_run = (
                proc.last_started_at is not None
                and (proc.last_exited_at - proc.last_started_at)
                >= proc.backoff.reset_after
            )
            if long_run:
                proc.consecutive_failures = 0
                proc.current_backoff = 0.0

            if not self._should_restart(proc, exit_code):
                return

            proc.restart_count += 1
            if not long_run:
                proc.consecutive_failures += 1
            await self._apply_backoff(proc)

    def _should_restart(self, proc: SupervisedProcess, exit_code: int) -> bool:
        """Decide whether to restart per restart_policy + desired_state."""
        if proc.desired_state != "running":
            return False
        if proc.restart_policy == "no":
            return False
        if proc.restart_policy == "always":
            return True
        # "on-failure" — non-zero exit
        return exit_code != 0

    async def _apply_backoff(self, proc: SupervisedProcess) -> None:
        """Compute and sleep the backoff delay before next spawn."""
        if proc.current_backoff == 0:
            proc.current_backoff = proc.backoff.initial_delay
        else:
            proc.current_backoff = min(
                proc.current_backoff * proc.backoff.multiplier,
                proc.backoff.max_delay,
            )
        await asyncio.sleep(proc.current_backoff)

    # ----------------------------- spawn / signal -----------------------

    async def _spawn(self, proc: SupervisedProcess) -> None:
        """Start the subprocess, wire up logging, stamp last_started_at."""
        log_fd: Any = asyncio.subprocess.DEVNULL
        if proc.log_path is not None:
            proc.log_path.parent.mkdir(parents=True, exist_ok=True)
            log_fd = proc.log_path.open("ab")

        env = proc.env if proc.env is not None else os.environ.copy()

        proc.process = await asyncio.create_subprocess_exec(
            *proc.cmd,
            cwd=str(proc.cwd) if proc.cwd is not None else None,
            env=env,
            stdout=log_fd,
            stderr=asyncio.subprocess.STDOUT,
            stdin=asyncio.subprocess.DEVNULL,
            start_new_session=True,  # own process group; clean signals
        )
        proc.last_started_at = time.time()
        # Close our handle to the log file; the subprocess inherits its
        # own copy. Without this, every restart would leak an FD.
        if hasattr(log_fd, "close"):
            log_fd.close()

    async def _signal_and_wait(
        self, proc: SupervisedProcess, sig: int, *, timeout: float
    ) -> None:
        """Send a signal and wait up to ``timeout``, then SIGKILL anything left."""
        if proc.process is None or proc.process.returncode is not None:
            return
        try:
            proc.process.send_signal(sig)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.process.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            self._logger.warning(
                "process %r didn't exit within %.1fs of SIG%d; sending SIGKILL",
                proc.name,
                timeout,
                sig,
            )
            try:
                proc.process.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.process.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                self._logger.error(
                    "process %r survived SIGKILL — supervisor losing track",
                    proc.name,
                )
