"""Tests for the Anthropic compressor (mocked transport)."""

from __future__ import annotations

import httpx
import pytest

from shared.outbound.compress import CompressError, get_backend
from shared.outbound.compress.anthropic import _strip_html


def _make(handler):
    return get_backend("anthropic")(api_key=b"sk-test", transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_compress_happy_path():
    captured = []

    def handler(req):
        captured.append(req)
        return httpx.Response(200, json={
            "content": [{"type": "text", "text": "extracted relevant bits"}]
        })

    out = await _make(handler).compress(
        text="<html>lots of stuff including the answer</html>",
        query="what's the answer",
    )
    assert out.backend == "anthropic"
    assert out.compressed_text == "extracted relevant bits"
    assert out.original_chars > out.compressed_chars
    assert 0 < out.ratio < 1.0
    # api key + version header
    assert captured[0].headers["x-api-key"] == "sk-test"
    assert captured[0].headers["anthropic-version"] == "2023-06-01"


@pytest.mark.asyncio
async def test_compress_401_clear():
    def handler(req):
        return httpx.Response(401)

    with pytest.raises(CompressError, match="api key"):
        await _make(handler).compress(text="x", query="y")


@pytest.mark.asyncio
async def test_compress_empty_text():
    def handler(req):
        return httpx.Response(200, json={"content": []})

    with pytest.raises(CompressError, match="non-empty"):
        await _make(handler).compress(text="", query="y")


@pytest.mark.asyncio
async def test_compress_malformed_json():
    def handler(req):
        return httpx.Response(200, content=b"not json")

    with pytest.raises(CompressError, match="malformed"):
        await _make(handler).compress(text="x", query="y")


def test_strip_html_basic():
    assert _strip_html("<p>hello <b>world</b></p>") == "hello world"


def test_strip_html_drops_script_and_style():
    text = "<html><script>alert(1)</script><style>x{}</style><p>real</p></html>"
    assert "alert" not in _strip_html(text)
    assert "real" in _strip_html(text)


def test_strip_html_unescapes_entities():
    assert _strip_html("<p>R&amp;D</p>") == "R&D"


def test_strip_html_passthrough_plain():
    assert _strip_html("plain text") == "plain text"
