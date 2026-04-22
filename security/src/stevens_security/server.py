"""UDS server shell for the Security Agent.

Listens on a Unix domain socket, reads a length-prefixed msgpack request,
dispatches to a capability handler, writes a msgpack response, closes the
connection. One request per connection in v1 — connections are short-lived.

Auth, policy, audit, and real capability logic are layered on top in later
steps (plans/v0.1-sec.md steps 3–6). This module is transport + dispatch only.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Any, Awaitable, Callable, Dict

from .framing import FramingError, read_frame, write_frame

log = logging.getLogger(__name__)

Dispatcher = Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]


async def default_dispatch(req: Dict[str, Any]) -> Dict[str, Any]:
    """Default handler — every capability is unknown at this point."""
    return {
        "ok": False,
        "error_code": "NOTFOUND",
        "message": f"unknown capability: {req.get('capability')!r}",
        "trace_id": str(uuid.uuid4()),
    }


def _error(code: str, message: str) -> Dict[str, Any]:
    return {
        "ok": False,
        "error_code": code,
        "message": message,
        "trace_id": str(uuid.uuid4()),
    }


async def _handle_connection(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    dispatch: Dispatcher,
) -> None:
    try:
        try:
            req = await read_frame(reader)
        except (FramingError, asyncio.IncompleteReadError) as e:
            log.warning("framing error: %s", e)
            resp = _error("INTERNAL", f"framing error: {e}")
        else:
            if not isinstance(req, dict):
                resp = _error("INTERNAL", "request is not a map")
            else:
                try:
                    resp = await dispatch(req)
                except Exception as e:  # noqa: BLE001
                    log.exception("dispatch failed")
                    resp = _error("INTERNAL", f"{type(e).__name__}: {e}")
        try:
            await write_frame(writer, resp)
        except Exception:  # noqa: BLE001
            log.exception("failed to write response")
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass


async def start_server(
    socket_path: str,
    dispatch: Dispatcher = default_dispatch,
) -> asyncio.AbstractServer:
    """Bind a UDS server at ``socket_path``. Returns the running server.

    Removes a leftover socket file from a previous crash before binding.
    Sets ``0o660`` on the socket; step 7 assigns the right group so
    authorized callers can connect.
    """
    if os.path.exists(socket_path):
        os.unlink(socket_path)
    server = await asyncio.start_unix_server(
        lambda r, w: _handle_connection(r, w, dispatch),
        path=socket_path,
    )
    os.chmod(socket_path, 0o660)
    log.info("security agent listening on %s", socket_path)
    return server
