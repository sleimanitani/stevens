"""Tests for the Security Agent UDS server shell.

These tests cover transport + dispatch only. Auth, policy, audit, and real
capability logic are verified in later steps.
"""

import asyncio

import pytest

from stevens_security.framing import read_frame, write_frame
from stevens_security.server import default_dispatch, start_server


async def _round_trip(socket_path: str, request: object) -> dict:
    reader, writer = await asyncio.open_unix_connection(socket_path)
    try:
        await write_frame(writer, request)
        return await read_frame(reader)
    finally:
        writer.close()
        await writer.wait_closed()


@pytest.mark.asyncio
async def test_unknown_capability_returns_notfound(tmp_path):
    socket_path = str(tmp_path / "sec.sock")
    server = await start_server(socket_path)
    try:
        resp = await _round_trip(
            socket_path,
            {
                "v": 1,
                "caller": "test_caller",
                "nonce": "abc",
                "ts": 0,
                "capability": "does.not.exist",
                "params": {},
                "sig": "",
            },
        )
    finally:
        server.close()
        await server.wait_closed()

    assert resp["ok"] is False
    assert resp["error_code"] == "NOTFOUND"
    assert "does.not.exist" in resp["message"]
    assert "trace_id" in resp and resp["trace_id"]


@pytest.mark.asyncio
async def test_custom_dispatcher_is_honored(tmp_path):
    socket_path = str(tmp_path / "sec.sock")

    async def custom(req):
        return {
            "ok": True,
            "result": {"got": req.get("capability")},
            "trace_id": "fixed",
        }

    server = await start_server(socket_path, dispatch=custom)
    try:
        resp = await _round_trip(socket_path, {"capability": "ping", "params": {}})
    finally:
        server.close()
        await server.wait_closed()

    assert resp == {"ok": True, "result": {"got": "ping"}, "trace_id": "fixed"}


@pytest.mark.asyncio
async def test_non_dict_request_returns_internal(tmp_path):
    socket_path = str(tmp_path / "sec.sock")
    server = await start_server(socket_path)
    try:
        resp = await _round_trip(socket_path, ["not", "a", "dict"])
    finally:
        server.close()
        await server.wait_closed()

    assert resp["ok"] is False
    assert resp["error_code"] == "INTERNAL"


@pytest.mark.asyncio
async def test_dispatcher_exception_returns_internal(tmp_path):
    socket_path = str(tmp_path / "sec.sock")

    async def raiser(req):
        raise RuntimeError("boom")

    server = await start_server(socket_path, dispatch=raiser)
    try:
        resp = await _round_trip(socket_path, {"capability": "whatever"})
    finally:
        server.close()
        await server.wait_closed()

    assert resp["ok"] is False
    assert resp["error_code"] == "INTERNAL"
    assert "boom" in resp["message"]


@pytest.mark.asyncio
async def test_start_server_replaces_stale_socket_file(tmp_path):
    socket_path = str(tmp_path / "sec.sock")
    # Simulate a leftover socket file from a prior crash.
    with open(socket_path, "w") as f:
        f.write("stale")
    server = await start_server(socket_path)
    try:
        resp = await _round_trip(socket_path, {"capability": "anything"})
    finally:
        server.close()
        await server.wait_closed()
    assert resp["error_code"] == "NOTFOUND"


@pytest.mark.asyncio
async def test_default_dispatch_directly():
    resp = await default_dispatch({"capability": "does.not.exist"})
    assert resp["ok"] is False
    assert resp["error_code"] == "NOTFOUND"
    assert "does.not.exist" in resp["message"]
