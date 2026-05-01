"""Tests for the additional search backends — Tavily, Exa, Firecrawl."""

from __future__ import annotations

import httpx
import pytest

from shared.outbound.search import SearchError, get_backend


def _make(name, handler):
    transport = httpx.MockTransport(handler)
    factory = get_backend(name)
    return factory(api_key=b"test-key", transport=transport)


# --- tavily ---


@pytest.mark.asyncio
async def test_tavily_normalizes():
    def handler(req):
        return httpx.Response(200, json={"results": [
            {"title": "T", "url": "https://x.com/", "content": "snip"},
        ]})

    out = await _make("tavily", handler).search("anything")
    assert out.backend == "tavily" and len(out.results) == 1
    assert out.results[0].title == "T"


@pytest.mark.asyncio
async def test_tavily_401_clear():
    def handler(req):
        return httpx.Response(401)

    with pytest.raises(SearchError, match="api key"):
        await _make("tavily", handler).search("x")


@pytest.mark.asyncio
async def test_tavily_malformed_json():
    def handler(req):
        return httpx.Response(200, content=b"not json")

    with pytest.raises(SearchError, match="malformed"):
        await _make("tavily", handler).search("x")


# --- exa ---


@pytest.mark.asyncio
async def test_exa_normalizes_and_uses_text_field():
    def handler(req):
        return httpx.Response(200, json={"results": [
            {"title": "T", "url": "https://x.com/", "text": "neural snippet"},
        ]})

    out = await _make("exa", handler).search("query")
    assert out.results[0].snippet == "neural snippet"


@pytest.mark.asyncio
async def test_exa_falls_back_to_highlight():
    def handler(req):
        return httpx.Response(200, json={"results": [
            {"title": "T", "url": "https://x.com/", "highlight": "fallback snippet"},
        ]})

    out = await _make("exa", handler).search("query")
    assert out.results[0].snippet == "fallback snippet"


@pytest.mark.asyncio
async def test_exa_401_clear():
    def handler(req):
        return httpx.Response(401)

    with pytest.raises(SearchError, match="api key"):
        await _make("exa", handler).search("x")


# --- firecrawl ---


@pytest.mark.asyncio
async def test_firecrawl_normalizes():
    def handler(req):
        return httpx.Response(200, json={"data": [
            {"title": "T", "url": "https://x.com/", "description": "snip"},
        ]})

    out = await _make("firecrawl", handler).search("x")
    assert out.results[0].title == "T"


@pytest.mark.asyncio
async def test_firecrawl_401_clear():
    def handler(req):
        return httpx.Response(401)

    with pytest.raises(SearchError, match="api key"):
        await _make("firecrawl", handler).search("x")


@pytest.mark.asyncio
async def test_firecrawl_429_clear():
    def handler(req):
        return httpx.Response(429)

    with pytest.raises(SearchError, match="429"):
        await _make("firecrawl", handler).search("x")
