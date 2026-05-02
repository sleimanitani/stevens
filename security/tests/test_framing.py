"""Tests for length-prefixed msgpack framing."""

import asyncio
import struct

import pytest

from demiurge.framing import (
    MAX_PAYLOAD_BYTES,
    FramingError,
    decode,
    encode,
    read_frame,
    write_frame,
)


def test_roundtrip_simple():
    obj = {"hello": "world", "n": 42}
    decoded, rest = decode(encode(obj))
    assert decoded == obj
    assert rest == b""


def test_roundtrip_nested():
    obj = {"a": [1, 2, 3], "b": {"c": True, "d": None}}
    decoded, rest = decode(encode(obj))
    assert decoded == obj
    assert rest == b""


def test_roundtrip_bytes():
    obj = {"blob": b"\x00\x01\x02\x03"}
    decoded, _ = decode(encode(obj))
    assert decoded == obj


def test_decode_leaves_trailing_bytes():
    buf = encode({"a": 1}) + b"extra"
    decoded, rest = decode(buf)
    assert decoded == {"a": 1}
    assert rest == b"extra"


def test_decode_truncated_header_rejected():
    with pytest.raises(FramingError):
        decode(b"\x00\x00")


def test_decode_truncated_payload_rejected():
    frame = encode({"a": 1})
    with pytest.raises(FramingError):
        decode(frame[:-1])


def test_encode_oversize_rejected():
    big = {"blob": b"\x00" * (MAX_PAYLOAD_BYTES + 1)}
    with pytest.raises(FramingError):
        encode(big)


def test_decode_declared_oversize_rejected():
    # Declare a length larger than the max; actual buffer doesn't even matter.
    buf = struct.pack(">I", MAX_PAYLOAD_BYTES + 1) + b"\x00"
    with pytest.raises(FramingError):
        decode(buf)


@pytest.mark.asyncio
async def test_async_roundtrip_over_unix_socket(tmp_path):
    path = str(tmp_path / "s.sock")
    received: list = []

    async def handler(reader, writer):
        obj = await read_frame(reader)
        received.append(obj)
        await write_frame(writer, {"echoed": obj})
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_unix_server(handler, path=path)
    try:
        reader, writer = await asyncio.open_unix_connection(path)
        await write_frame(writer, {"ping": "pong"})
        resp = await read_frame(reader)
        writer.close()
        await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()

    assert received == [{"ping": "pong"}]
    assert resp == {"echoed": {"ping": "pong"}}


@pytest.mark.asyncio
async def test_async_read_frame_oversize_rejected(tmp_path):
    path = str(tmp_path / "s.sock")

    async def handler(reader, writer):
        # Send a length prefix declaring a 2 MiB payload — reader must refuse.
        writer.write(struct.pack(">I", MAX_PAYLOAD_BYTES + 1))
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_unix_server(handler, path=path)
    try:
        reader, writer = await asyncio.open_unix_connection(path)
        with pytest.raises(FramingError):
            await read_frame(reader)
        writer.close()
        await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()
