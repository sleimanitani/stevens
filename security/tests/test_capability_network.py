"""Tests for network.fetch + network.search capabilities (mocked transport)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

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
    """Minimal sealed-store stub that returns canned secrets by name."""

    def __init__(self, mapping: Dict[str, bytes]) -> None:
        self._mapping = mapping

    def get_by_name(self, name: str) -> bytes:
        if name not in self._mapping:
            raise KeyError(name)
        return self._mapping[name]


def _make_state(handler) -> WebState:
    transport = httpx.MockTransport(handler)
    return WebState(
        fetch_cache=TTLCache(),
        search_cache=TTLCache(),
        rate_limiter=DomainRateLimiter(rate_per_second=1000.0, burst=1000),
        web_client=WebClient(transport=transport),
    )


def _ctx(state: WebState, *, sealed_store=None) -> CapabilityContext:
    return CapabilityContext(
        sealed_store=sealed_store,
        extra={"web_state": state},
    )


# Bypass DNS resolution by passing IP-shaped public hosts.
def _public_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=b"public body", headers={"x-test": "ok"})


# --- network.fetch ---


@pytest.mark.asyncio
async def test_fetch_happy_path():
    state = _make_state(_public_handler)
    spec = default_registry.get("network.fetch")
    out = await spec.invoke(
        FakeAgent(),
        {"url": "https://93.184.216.34/some/path"},
        _ctx(state),
    )
    assert out["status"] == 200
    assert out["body"] == b"public body"
    assert out["cache_hit"] is False


@pytest.mark.asyncio
async def test_fetch_private_ip_rejected():
    state = _make_state(_public_handler)
    out = await default_registry.get("network.fetch").invoke(
        FakeAgent(), {"url": "https://10.0.0.1/"}, _ctx(state),
    )
    assert out["error"] == "url_rejected"
    assert "private" in out["detail"]


@pytest.mark.asyncio
async def test_fetch_loopback_rejected():
    state = _make_state(_public_handler)
    out = await default_registry.get("network.fetch").invoke(
        FakeAgent(), {"url": "https://127.0.0.1/"}, _ctx(state),
    )
    assert out["error"] == "url_rejected"


@pytest.mark.asyncio
async def test_fetch_cache_hit_avoids_transport():
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, content=b"once")

    state = _make_state(handler)
    spec = default_registry.get("network.fetch")
    url = "https://93.184.216.34/x"
    out1 = await spec.invoke(FakeAgent(), {"url": url}, _ctx(state))
    out2 = await spec.invoke(FakeAgent(), {"url": url}, _ctx(state))
    assert out1["cache_hit"] is False
    assert out2["cache_hit"] is True
    assert call_count == 1


@pytest.mark.asyncio
async def test_fetch_rate_limited():
    state = WebState(
        fetch_cache=TTLCache(),
        search_cache=TTLCache(),
        rate_limiter=DomainRateLimiter(rate_per_second=0.001, burst=1),
        web_client=WebClient(transport=httpx.MockTransport(_public_handler)),
    )
    spec = default_registry.get("network.fetch")
    out1 = await spec.invoke(FakeAgent(), {"url": "https://93.184.216.34/a"}, _ctx(state))
    out2 = await spec.invoke(FakeAgent(), {"url": "https://93.184.216.34/b"}, _ctx(state))
    # First call drains the bucket (and lands cached). Second call to a
    # *different* URL on the same domain hits rate limit.
    assert out1.get("status") == 200
    assert out2.get("error") == "rate_limited"


@pytest.mark.asyncio
async def test_fetch_4xx_not_cached():
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(404, content=b"not found")

    state = _make_state(handler)
    spec = default_registry.get("network.fetch")
    await spec.invoke(FakeAgent(), {"url": "https://93.184.216.34/x"}, _ctx(state))
    await spec.invoke(FakeAgent(), {"url": "https://93.184.216.34/x"}, _ctx(state))
    # Both calls hit the transport since 404s aren't cached.
    assert call_count == 2


@pytest.mark.asyncio
async def test_fetch_missing_url_param():
    state = _make_state(_public_handler)
    out = await default_registry.get("network.fetch").invoke(
        FakeAgent(), {}, _ctx(state),
    )
    assert out["error"] == "url_required"


# --- network.search ---


@pytest.mark.asyncio
async def test_search_happy_path():
    captured: List[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "web": {
                    "results": [
                        {"title": "T1", "url": "https://x.com/1", "description": "snip"},
                    ]
                }
            },
        )

    state = _make_state(_public_handler)
    # Search uses its own httpx client per backend, not the WebClient. Inject
    # the transport via a context manager: we override the brave factory's
    # transport via the get_backend factory's `transport` kwarg, which the
    # capability handler uses.
    # Easier: monkey-patch the brave backend's transport via the factory hook.
    from shared.outbound.search import _BACKENDS

    original = _BACKENDS["brave"]

    def factory_with_transport(*, api_key, transport=None):
        return original(api_key=api_key, transport=httpx.MockTransport(handler))

    _BACKENDS["brave"] = factory_with_transport
    try:
        sealed_store = FakeSealedStore({"web.brave.api_key": b"test-key"})
        out = await default_registry.get("network.search").invoke(
            FakeAgent(),
            {"query": "transformer attention"},
            _ctx(state, sealed_store=sealed_store),
        )
    finally:
        _BACKENDS["brave"] = original

    assert out["backend"] == "brave"
    assert len(out["results"]) == 1
    assert out["results"][0]["title"] == "T1"
    assert out["cache_hit"] is False


@pytest.mark.asyncio
async def test_search_missing_api_key():
    state = _make_state(_public_handler)
    sealed_store = FakeSealedStore({})  # empty
    out = await default_registry.get("network.search").invoke(
        FakeAgent(),
        {"query": "x"},
        _ctx(state, sealed_store=sealed_store),
    )
    assert out["error"] == "api_key_missing"


@pytest.mark.asyncio
async def test_search_empty_query():
    state = _make_state(_public_handler)
    out = await default_registry.get("network.search").invoke(
        FakeAgent(),
        {"query": "   "},
        _ctx(state, sealed_store=FakeSealedStore({"web.brave.api_key": b"x"})),
    )
    assert out["error"] == "query_required"


@pytest.mark.asyncio
async def test_search_cache_hit():
    """Two identical queries → second is cache hit."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(
            200, json={"web": {"results": [{"title": "T", "url": "https://x.com/", "description": "s"}]}},
        )

    state = _make_state(_public_handler)
    from shared.outbound.search import _BACKENDS

    original = _BACKENDS["brave"]
    _BACKENDS["brave"] = lambda *, api_key, transport=None: original(
        api_key=api_key, transport=httpx.MockTransport(handler),
    )
    try:
        ctx = _ctx(state, sealed_store=FakeSealedStore({"web.brave.api_key": b"k"}))
        out1 = await default_registry.get("network.search").invoke(
            FakeAgent(), {"query": "same query"}, ctx,
        )
        out2 = await default_registry.get("network.search").invoke(
            FakeAgent(), {"query": "same query"}, ctx,
        )
    finally:
        _BACKENDS["brave"] = original

    assert out1["cache_hit"] is False
    assert out2["cache_hit"] is True
    assert call_count == 1
