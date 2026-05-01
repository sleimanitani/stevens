"""Tests for the network.compress capability (mocked transport)."""

from __future__ import annotations

from typing import Dict

import httpx
import pytest

from stevens_security.capabilities import network as network_caps  # noqa: F401 — registers
from stevens_security.capabilities.network import WebState
from stevens_security.capabilities.registry import default_registry
from stevens_security.context import CapabilityContext
from stevens_security.outbound.web_state import DomainRateLimiter, TTLCache

from shared.outbound.web import WebClient


class FakeAgent:
    name = "researcher"


class FakeSealedStore:
    def __init__(self, mapping: Dict[str, bytes]) -> None:
        self._m = mapping

    def get_by_name(self, name: str) -> bytes:
        if name not in self._m:
            raise KeyError(name)
        return self._m[name]


def _state():
    return WebState(
        fetch_cache=TTLCache(),
        search_cache=TTLCache(),
        rate_limiter=DomainRateLimiter(),
        web_client=WebClient(),
    )


def _ctx(*, sealed_store=None) -> CapabilityContext:
    return CapabilityContext(
        sealed_store=sealed_store, extra={"web_state": _state()},
    )


@pytest.mark.asyncio
async def test_compress_happy_path():
    def handler(req):
        return httpx.Response(200, json={
            "content": [{"type": "text", "text": "extracted"}]
        })

    from shared.outbound.compress import _BACKENDS

    original = _BACKENDS["anthropic"]
    _BACKENDS["anthropic"] = lambda *, api_key, transport=None: original(
        api_key=api_key, transport=httpx.MockTransport(handler),
    )
    try:
        out = await default_registry.get("network.compress").invoke(
            FakeAgent(),
            {"text": "<p>raw page</p>", "query": "what's here"},
            _ctx(sealed_store=FakeSealedStore({"compress.anthropic.api_key": b"sk-x"})),
        )
    finally:
        _BACKENDS["anthropic"] = original
    assert out["compressed_text"] == "extracted"
    assert out["backend"] == "anthropic"
    assert "ratio" in out


@pytest.mark.asyncio
async def test_compress_missing_api_key():
    out = await default_registry.get("network.compress").invoke(
        FakeAgent(),
        {"text": "x", "query": "y"},
        _ctx(sealed_store=FakeSealedStore({})),
    )
    assert out["error"] == "api_key_missing"


@pytest.mark.asyncio
async def test_compress_empty_text():
    out = await default_registry.get("network.compress").invoke(
        FakeAgent(),
        {"text": "", "query": "y"},
        _ctx(sealed_store=FakeSealedStore({"compress.anthropic.api_key": b"x"})),
    )
    assert out["error"] == "text_required"


@pytest.mark.asyncio
async def test_compress_empty_query():
    out = await default_registry.get("network.compress").invoke(
        FakeAgent(),
        {"text": "x", "query": "  "},
        _ctx(sealed_store=FakeSealedStore({"compress.anthropic.api_key": b"x"})),
    )
    assert out["error"] == "query_required"


@pytest.mark.asyncio
async def test_compress_unknown_backend():
    out = await default_registry.get("network.compress").invoke(
        FakeAgent(),
        {"text": "x", "query": "y", "backend": "magic"},
        _ctx(sealed_store=FakeSealedStore({"compress.magic.api_key": b"x"})),
    )
    assert out["error"] == "unknown_backend"
