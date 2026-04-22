"""Length-prefixed msgpack framing for the Security Agent RPC.

Frame layout on the wire: ``[uint32 big-endian payload length][msgpack payload]``.
Max payload per frame: 1 MiB in v1.

This module is pure transport — it knows nothing about request shape, auth,
policy, or audit. Those layers sit above.
"""

from __future__ import annotations

import asyncio
import struct
from typing import Any, Tuple

import msgpack

MAX_PAYLOAD_BYTES = 1 << 20  # 1 MiB
_LENGTH_PREFIX = struct.Struct(">I")


class FramingError(Exception):
    """Raised when a frame is malformed or exceeds the payload limit."""


def encode(obj: Any) -> bytes:
    """Serialize ``obj`` into a length-prefixed msgpack frame."""
    payload = msgpack.packb(obj, use_bin_type=True)
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise FramingError(
            f"payload {len(payload)} bytes exceeds max {MAX_PAYLOAD_BYTES}"
        )
    return _LENGTH_PREFIX.pack(len(payload)) + payload


def decode(buf: bytes) -> Tuple[Any, bytes]:
    """Parse one frame out of ``buf``. Returns ``(obj, leftover_bytes)``.

    Raises :class:`FramingError` if the buffer is too short, the declared
    length is oversized, or the payload is shorter than declared.
    """
    if len(buf) < _LENGTH_PREFIX.size:
        raise FramingError("buffer shorter than 4-byte length prefix")
    (length,) = _LENGTH_PREFIX.unpack_from(buf, 0)
    if length > MAX_PAYLOAD_BYTES:
        raise FramingError(
            f"declared length {length} exceeds max {MAX_PAYLOAD_BYTES}"
        )
    end = _LENGTH_PREFIX.size + length
    if len(buf) < end:
        raise FramingError(
            f"buffer has {len(buf) - _LENGTH_PREFIX.size} payload bytes, need {length}"
        )
    payload = buf[_LENGTH_PREFIX.size : end]
    obj = msgpack.unpackb(payload, raw=False)
    return obj, buf[end:]


async def read_frame(reader: asyncio.StreamReader) -> Any:
    """Read exactly one frame from an ``asyncio.StreamReader``."""
    header = await reader.readexactly(_LENGTH_PREFIX.size)
    (length,) = _LENGTH_PREFIX.unpack(header)
    if length > MAX_PAYLOAD_BYTES:
        raise FramingError(
            f"declared length {length} exceeds max {MAX_PAYLOAD_BYTES}"
        )
    payload = await reader.readexactly(length)
    return msgpack.unpackb(payload, raw=False)


async def write_frame(writer: asyncio.StreamWriter, obj: Any) -> None:
    """Write one frame to an ``asyncio.StreamWriter`` and drain."""
    writer.write(encode(obj))
    await writer.drain()
