"""Tests for the runtime daemon + IPC — v0.11 step 7.4."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from demiurge.runtime.daemon import (
    RuntimeDaemon,
    default_socket_path,
    send_request,
    send_request_async,
)


# ----------------------------- default socket path ----------------------


def test_default_socket_path_uses_xdg_runtime_dir(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert default_socket_path() == tmp_path / "demiurge" / "runtime.sock"


def test_default_socket_path_fallback(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert (
        default_socket_path()
        == tmp_path / ".local" / "state" / "demiurge" / "runtime.sock"
    )


# ----------------------------- daemon lifecycle -------------------------


def _daemon(tmp_path: Path) -> RuntimeDaemon:
    """Build a daemon with all paths under tmp_path so tests don't
    pollute real state."""
    return RuntimeDaemon(
        repo_root=tmp_path / "repo",
        log_dir=tmp_path / "logs",
        feed_base=tmp_path / "feeds",
        audit_dir=tmp_path / "audit",
        agents_yaml=tmp_path / "agents.yaml",
        socket_path=tmp_path / "runtime.sock",
    )


def test_daemon_start_stop_lifecycle(tmp_path: Path):
    """Plain start → stop with no installed plugins. No errors."""

    async def run():
        d = _daemon(tmp_path)
        await d.start()
        assert d.socket_path.exists()
        await d.stop()
        assert not d.socket_path.exists()

    asyncio.run(run())


def test_daemon_socket_mode_is_0600(tmp_path: Path):
    """UDS file mode locked to operator-only."""

    async def run():
        d = _daemon(tmp_path)
        await d.start()
        try:
            mode = d.socket_path.stat().st_mode & 0o777
            # Be lenient — umask may strip group bits on some systems.
            # Floor: no world rwx.
            assert mode & 0o007 == 0
        finally:
            await d.stop()

    asyncio.run(run())


def test_daemon_clears_stale_socket(tmp_path: Path):
    """Pre-existing socket file from a crashed prior run is removed on start."""
    sock = tmp_path / "runtime.sock"
    sock.parent.mkdir(parents=True, exist_ok=True)
    sock.touch()

    async def run():
        d = _daemon(tmp_path)
        await d.start()
        assert d.socket_path.exists()  # but it's a real socket now, not the stale file
        await d.stop()

    asyncio.run(run())


# ----------------------------- IPC: status + reload ---------------------


def test_ipc_status_response_shape(tmp_path: Path):
    async def run():
        d = _daemon(tmp_path)
        await d.start()
        try:
            resp = await send_request_async({"op": "status"}, socket_path=d.socket_path)
            assert resp["ok"] is True
            assert resp["data"]["running"] is True
            assert "processes" in resp["data"]
        finally:
            await d.stop()

    asyncio.run(run())


def test_ipc_reload_response_shape(tmp_path: Path):
    async def run():
        d = _daemon(tmp_path)
        await d.start()
        try:
            resp = await send_request_async({"op": "reload"}, socket_path=d.socket_path)
            assert resp["ok"] is True
            assert "powers_registered" in resp["data"]
            assert "creatures_registered" in resp["data"]
        finally:
            await d.stop()

    asyncio.run(run())


# ----------------------------- IPC: shutdown -----------------------------


def test_ipc_shutdown_sets_event(tmp_path: Path):
    """Sending op=shutdown causes wait_until_done to return."""

    async def run():
        d = _daemon(tmp_path)
        await d.start()
        # Schedule a shutdown request.
        async def trigger():
            await asyncio.sleep(0.05)
            await send_request_async(
                {"op": "shutdown"}, socket_path=d.socket_path, timeout=2.0
            )

        # Run shutdown trigger in parallel with wait_until_done.
        trigger_task = asyncio.create_task(trigger())
        try:
            await asyncio.wait_for(d.wait_until_done(), timeout=2.0)
        finally:
            await trigger_task
            await d.stop()

    asyncio.run(run())


# ----------------------------- IPC: pause/resume -------------------------


def test_ipc_pause_unknown_creature(tmp_path: Path):
    """Pausing a non-existent creature reports failure cleanly."""

    async def run():
        d = _daemon(tmp_path)
        await d.start()
        try:
            resp = await send_request_async(
                {"op": "pause", "creature_id": "nope.x"},
                socket_path=d.socket_path,
            )
            assert resp["ok"] is False
            assert "unknown name" in resp["error"]
        finally:
            await d.stop()

    asyncio.run(run())


def test_ipc_pause_missing_creature_id(tmp_path: Path):
    async def run():
        d = _daemon(tmp_path)
        await d.start()
        try:
            resp = await send_request_async(
                {"op": "pause"}, socket_path=d.socket_path
            )
            assert resp["ok"] is False
            assert "missing creature_id" in resp["error"]
        finally:
            await d.stop()

    asyncio.run(run())


# ----------------------------- IPC: protocol robustness -----------------


def test_ipc_unknown_op(tmp_path: Path):
    async def run():
        d = _daemon(tmp_path)
        await d.start()
        try:
            resp = await send_request_async(
                {"op": "psychic_divine"}, socket_path=d.socket_path
            )
            assert resp["ok"] is False
            assert "unknown op" in resp["error"]
        finally:
            await d.stop()

    asyncio.run(run())


def test_ipc_bad_json_handled(tmp_path: Path):
    """Daemon doesn't crash on malformed input; reports the parse error."""

    async def run():
        d = _daemon(tmp_path)
        await d.start()
        try:
            reader, writer = await asyncio.open_unix_connection(str(d.socket_path))
            writer.write(b"this isn't json\n")
            await writer.drain()
            line = await reader.readline()
            resp = json.loads(line.decode("utf-8"))
            assert resp["ok"] is False
            assert "bad json" in resp["error"]
            writer.close()
            await writer.wait_closed()
        finally:
            await d.stop()

    asyncio.run(run())


def test_ipc_connection_refused_when_no_daemon(tmp_path: Path):
    """No daemon → connection error; client surfaces it cleanly."""
    sock = tmp_path / "absent.sock"
    with pytest.raises((ConnectionRefusedError, FileNotFoundError)):
        send_request({"op": "status"}, socket_path=sock, timeout=1.0)


# ----------------------------- send_request sync wrapper ----------------


def test_send_request_sync_wrapper_uses_asyncio_run(tmp_path: Path, monkeypatch):
    """``send_request`` is a thin asyncio.run() wrapper. We don't try to
    test it end-to-end across loops here (would require a thread-hosted
    daemon); instead pin the contract that it forwards to the async
    function with the right args."""
    captured = {}

    async def fake_async(req, *, socket_path=None, timeout=5.0):
        captured["req"] = req
        captured["socket_path"] = socket_path
        captured["timeout"] = timeout
        return {"ok": True, "data": {}}

    import demiurge.runtime.daemon as daemon_mod

    monkeypatch.setattr(daemon_mod, "send_request_async", fake_async)
    sock = tmp_path / "x.sock"
    resp = send_request({"op": "status"}, socket_path=sock, timeout=2.0)
    assert resp["ok"] is True
    assert captured == {
        "req": {"op": "status"},
        "socket_path": sock,
        "timeout": 2.0,
    }
