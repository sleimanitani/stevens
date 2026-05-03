"""Runtime daemon — v0.11 step 7.4.

Composes ``Supervisor`` + ``PowerRuntime`` + ``CreatureRuntime`` into
a single long-lived process. Listens on a UDS socket for IPC requests
from CLI invocations (``demiurge runtime status``, ``demiurge hire
pause``, etc.). Handles SIGTERM/SIGINT for clean shutdown — every
supervised subprocess gets torn down before the daemon exits.

UDS protocol: newline-delimited JSON request/response over a unix-
socket stream. One request per connection; daemon reads one JSON
line, dispatches, writes one JSON line, closes. Keeps the protocol
simple and idempotent — CLIs reconnect per command.

Request shape::

    {"op": "status"}
    {"op": "pause",  "creature_id": "email_pm.personal"}
    {"op": "resume", "creature_id": "email_pm.personal"}
    {"op": "shutdown"}
    {"op": "reload"}              # rediscover plugins

Response shape::

    {"ok": true,  "data": {...}}
    {"ok": false, "error": "...message..."}

The daemon is the *operator-facing* entry point of v0.11's runtime —
``python -m demiurge.runtime`` (or via the systemd user unit) starts
it; ``demiurge runtime stop`` sends a shutdown request.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..audit import AuditWriter
from .creature_runtime import CreatureRuntime
from .power_runtime import PowerRuntime
from .supervisor import Supervisor


# ----------------------------- defaults ---------------------------------


def default_socket_path() -> Path:
    """``$XDG_RUNTIME_DIR/demiurge/runtime.sock`` (or fallback)."""
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        return Path(runtime_dir) / "demiurge" / "runtime.sock"
    # Fallback for systems without XDG_RUNTIME_DIR (e.g. macOS dev boxes
    # without lingering systemd-user). Falls under the user's state dir.
    return Path("~/.local/state/demiurge/runtime.sock").expanduser()


def default_agents_yaml() -> Path:
    return Path(
        os.environ.get("DEMIURGE_SECURITY_AGENTS", "security/policy/agents.yaml")
    )


def default_audit_dir() -> Path:
    return Path(
        os.environ.get("DEMIURGE_SECURITY_AUDIT_DIR", "/var/lib/demiurge/audit")
    ).expanduser()


# ----------------------------- daemon -----------------------------------


@dataclass
class RuntimeDaemon:
    """Long-lived runtime supervisor.

    Lifecycle:

    1. ``await daemon.start()`` — discover plugins, register with
       Supervisor, start power processes + polling tasks + creature
       processes + audit-angel observer tasks, open the UDS server.
    2. (runs forever) ``await daemon.wait_until_done()`` — waits for
       SIGTERM/SIGINT or an IPC ``shutdown`` request.
    3. ``await daemon.stop()`` — close UDS server, stop angel + polling
       tasks, stop_all on the supervisor.

    Or use the ``main()`` convenience that wires steps 1-3 together.
    """

    repo_root: Path = field(default_factory=Path.cwd)
    log_dir: Path = field(
        default_factory=lambda: Path("~/.local/state/demiurge/logs").expanduser()
    )
    feed_base: Optional[Path] = None
    audit_dir: Path = field(default_factory=default_audit_dir)
    agents_yaml: Path = field(default_factory=default_agents_yaml)
    socket_path: Path = field(default_factory=default_socket_path)

    _sup: Optional[Supervisor] = field(default=None, init=False)
    _power: Optional[PowerRuntime] = field(default=None, init=False)
    _creature: Optional[CreatureRuntime] = field(default=None, init=False)
    _ipc_server: Optional[asyncio.AbstractServer] = field(default=None, init=False)
    _shutdown_event: Optional[asyncio.Event] = field(default=None, init=False)
    _logger: logging.Logger = field(
        default_factory=lambda: logging.getLogger("demiurge.runtime.daemon"),
        init=False,
    )

    # ----------------------------- startup -------------------------------

    async def start(self) -> None:
        """Build everything and start the supervisor + IPC server."""
        self._sup = Supervisor()
        self._power = PowerRuntime(
            supervisor=self._sup,
            repo_root=self.repo_root,
            log_dir=self.log_dir,
        )
        self.audit_dir.mkdir(parents=True, exist_ok=True)
        self._creature = CreatureRuntime(
            supervisor=self._sup,
            audit_writer=AuditWriter(self.audit_dir),
            repo_root=self.repo_root,
            log_dir=self.log_dir,
            feed_base=self.feed_base,
        )

        # Discover + register everything.
        power_regs, power_errs = self._power.discover_and_add_all()
        for err in power_errs:
            self._logger.warning(
                "power %r failed to register: %s", err.power_name, err.reason
            )
        self._logger.info(
            "registered %d power(s); %d errors", len(power_regs), len(power_errs)
        )

        creature_regs, creature_errs = self._creature.discover_and_add_all(
            self.agents_yaml
        )
        for err in creature_errs:
            self._logger.warning(
                "creature %r failed to register: %s",
                err.creature_id,
                err.reason,
            )
        self._logger.info(
            "registered %d creature(s); %d errors",
            len(creature_regs),
            len(creature_errs),
        )

        # Start everything.
        await self._sup.start_all()
        await self._power.start_polling()
        await self._creature.start_angels()

        # Open the UDS server.
        await self._open_ipc_server()

        self._shutdown_event = asyncio.Event()
        self._install_signal_handlers()

    async def wait_until_done(self) -> None:
        """Block until shutdown is requested (signal or IPC)."""
        if self._shutdown_event is None:
            raise RuntimeError("daemon not started")
        await self._shutdown_event.wait()

    async def stop(self) -> None:
        """Tear down in reverse-start order. Idempotent."""
        if self._ipc_server is not None:
            self._ipc_server.close()
            try:
                await self._ipc_server.wait_closed()
            except Exception:  # noqa: BLE001
                pass
            self._ipc_server = None
            try:
                self.socket_path.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass

        if self._creature is not None:
            await self._creature.stop_angels()
        if self._power is not None:
            await self._power.stop_polling()
        if self._sup is not None:
            await self._sup.stop_all(timeout=10.0)

    # ----------------------------- IPC server ----------------------------

    async def _open_ipc_server(self) -> None:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        # Remove a stale socket from a prior crashed run.
        if self.socket_path.exists() or self.socket_path.is_symlink():
            try:
                self.socket_path.unlink()
            except OSError:
                pass
        self._ipc_server = await asyncio.start_unix_server(
            self._handle_ipc_connection, path=str(self.socket_path)
        )
        # Mode 0600 — only the owning operator can talk to the daemon.
        try:
            os.chmod(self.socket_path, 0o600)
        except OSError as e:
            self._logger.warning(
                "could not chmod IPC socket %s: %s", self.socket_path, e
            )

    async def _handle_ipc_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            line = await reader.readline()
            if not line:
                return
            try:
                request = json.loads(line.decode("utf-8"))
            except json.JSONDecodeError as e:
                response = {"ok": False, "error": f"bad json: {e}"}
            else:
                response = await self._dispatch(request)
            writer.write((json.dumps(response) + "\n").encode("utf-8"))
            await writer.drain()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass

    async def _dispatch(self, request: dict) -> dict:
        op = request.get("op")
        try:
            if op == "status":
                return {"ok": True, "data": self._status_payload()}
            if op == "pause":
                cid = request.get("creature_id")
                if not isinstance(cid, str):
                    return {"ok": False, "error": "missing creature_id"}
                proc_name = f"demiurge-creature-{cid}"
                await self._sup.pause(proc_name)  # type: ignore[union-attr]
                return {"ok": True, "data": {"creature_id": cid, "paused": True}}
            if op == "resume":
                cid = request.get("creature_id")
                if not isinstance(cid, str):
                    return {"ok": False, "error": "missing creature_id"}
                proc_name = f"demiurge-creature-{cid}"
                await self._sup.resume(proc_name)  # type: ignore[union-attr]
                return {"ok": True, "data": {"creature_id": cid, "resumed": True}}
            if op == "shutdown":
                if self._shutdown_event is not None:
                    self._shutdown_event.set()
                return {"ok": True, "data": {"shutdown": "requested"}}
            if op == "reload":
                # Re-discover and add new plugins. Doesn't remove ones
                # that were uninstalled — that's a Hades concern.
                power_regs, _ = self._power.discover_and_add_all()  # type: ignore[union-attr]
                creature_regs, _ = self._creature.discover_and_add_all(  # type: ignore[union-attr]
                    self.agents_yaml
                )
                await self._sup.start_all()  # type: ignore[union-attr]
                await self._power.start_polling()  # type: ignore[union-attr]
                await self._creature.start_angels()  # type: ignore[union-attr]
                return {
                    "ok": True,
                    "data": {
                        "powers_registered": len(power_regs),
                        "creatures_registered": len(creature_regs),
                    },
                }
            return {"ok": False, "error": f"unknown op {op!r}"}
        except KeyError as e:
            return {"ok": False, "error": f"unknown name: {e}"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    def _status_payload(self) -> dict[str, Any]:
        if self._sup is None:
            return {"running": False}
        statuses = self._sup.status()
        return {
            "running": True,
            "socket_path": str(self.socket_path),
            "processes": [
                {
                    "name": s.name,
                    "desired_state": s.desired_state,
                    "is_running": s.is_running,
                    "pid": s.pid,
                    "last_started_at": s.last_started_at,
                    "last_exited_at": s.last_exited_at,
                    "last_exit_code": s.last_exit_code,
                    "restart_count": s.restart_count,
                    "log_path": str(s.log_path),
                }
                for s in statuses
            ],
        }

    # ----------------------------- signals -------------------------------

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(
                    sig, lambda s=sig: self._on_signal(s)
                )
            except NotImplementedError:
                # Windows doesn't support add_signal_handler on the
                # default loop. We're Linux-only so this is just defensive.
                pass

    def _on_signal(self, sig: int) -> None:
        self._logger.info("received SIG%d; requesting shutdown", sig)
        if self._shutdown_event is not None:
            self._shutdown_event.set()


# ----------------------------- main entry point -------------------------


async def amain() -> int:
    """Async main: start, wait for shutdown, stop."""
    logging.basicConfig(
        level=os.environ.get("DEMIURGE_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    daemon = RuntimeDaemon()
    await daemon.start()
    try:
        await daemon.wait_until_done()
    finally:
        await daemon.stop()
    return 0


def main() -> int:
    """Sync wrapper for `python -m demiurge.runtime`."""
    return asyncio.run(amain())


# ----------------------------- IPC client (used by CLI) -----------------


async def send_request_async(
    request: dict, *, socket_path: Optional[Path] = None, timeout: float = 5.0
) -> dict:
    """Send a single JSON-line request to the daemon. Returns the response.

    Raises ``ConnectionRefusedError`` (or wrapping it in ``RuntimeError``)
    if the daemon isn't running. Raises ``asyncio.TimeoutError`` on
    timeout.
    """
    sock = socket_path or default_socket_path()
    reader, writer = await asyncio.wait_for(
        asyncio.open_unix_connection(str(sock)), timeout=timeout
    )
    try:
        writer.write((json.dumps(request) + "\n").encode("utf-8"))
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=timeout)
        return json.loads(line.decode("utf-8"))
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass


def send_request(request: dict, *, socket_path: Optional[Path] = None, timeout: float = 5.0) -> dict:
    """Sync wrapper for the CLI. Runs the async send in a fresh loop."""
    return asyncio.run(send_request_async(request, socket_path=socket_path, timeout=timeout))
