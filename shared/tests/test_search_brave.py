"""Tests for the Brave search backend (mocked transport)."""

from __future__ import annotations

import json
from typing import List

import httpx
import pytest

from shared.outbound.search import (
    SearchError,
    get_backend,
    select_backend_name,
)


def _make_backend(handler):
    transport = httpx.MockTransport(handler)
    factory = get_backend("brave")
    return factory(api_key=b"test-key", transport=transport)


@pytest.mark.asyncio
async def test_brave_normalizes_results():
    captured: List[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        body = {
            "web": {
                "results": [
                    {
                        "title": "Attention Is All You Need",
                        "url": "https://arxiv.org/abs/1706.03762",
                        "description": "transformer architecture",
                    },
                    {
                        "title": "BERT",
                        "url": "https://arxiv.org/abs/1810.04805",
                        "description": "bidirectional encoders",
                    },
                ]
            }
        }
        return httpx.Response(200, json=body)

    backend = _make_backend(handler)
    out = await backend.search("transformer attention", max_results=5)
    assert out.backend == "brave"
    assert len(out.results) == 2
    assert out.results[0].title == "Attention Is All You Need"
    assert out.results[0].snippet == "transformer architecture"
    # Header carries the api key.
    assert captured[0].headers["X-Subscription-Token"] == "test-key"
    assert captured[0].url.params["q"] == "transformer attention"
    assert captured[0].url.params["count"] == "5"


@pytest.mark.asyncio
async def test_brave_429_surfaces_clear_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="rate limited")

    backend = _make_backend(handler)
    with pytest.raises(SearchError, match="429"):
        await backend.search("anything")


@pytest.mark.asyncio
async def test_brave_401_surfaces_clear_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    backend = _make_backend(handler)
    with pytest.raises(SearchError, match="api key"):
        await backend.search("anything")


@pytest.mark.asyncio
async def test_brave_malformed_json():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    backend = _make_backend(handler)
    with pytest.raises(SearchError, match="malformed"):
        await backend.search("x")


@pytest.mark.asyncio
async def test_brave_empty_query_rejected():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"web": {"results": []}})

    backend = _make_backend(handler)
    with pytest.raises(SearchError, match="non-empty"):
        await backend.search("   ")


@pytest.mark.asyncio
async def test_brave_clamps_max_results():
    captured: List[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"web": {"results": []}})

    backend = _make_backend(handler)
    await backend.search("x", max_results=999)
    # Brave caps at 20 in our backend; verify the param.
    assert captured[0].url.params["count"] == "20"


def test_select_backend_default(monkeypatch):
    monkeypatch.delenv("DEMIURGE_SEARCH_BACKEND", raising=False)
    assert select_backend_name() == "brave"


def test_select_backend_env_override(monkeypatch):
    monkeypatch.setenv("DEMIURGE_SEARCH_BACKEND", "tavily")
    assert select_backend_name() == "tavily"


def test_unknown_backend_lookup_raises():
    with pytest.raises(SearchError, match="unknown search backend"):
        get_backend("does_not_exist")
