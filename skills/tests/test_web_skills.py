"""Tests for the web_fetch and web_search skill wrappers (mocked client)."""

from __future__ import annotations

import json
from typing import Any, Dict

import pytest

from skills.tools.web.fetch import (
    _set_client_for_tests as _set_fetch_client_for_tests,
    build_tool as build_fetch_tool,
)
from skills.tools.web.search import (
    _set_client_for_tests as _set_search_client_for_tests,
    build_tool as build_search_tool,
)


def _set_client_for_tests(client):
    """Set the cached SecurityClient on both web tool modules."""
    _set_fetch_client_for_tests(client)
    _set_search_client_for_tests(client)


class FakeClient:
    def __init__(self, responses: Dict[str, Any]) -> None:
        self._responses = responses
        self.calls = []

    async def call(self, capability: str, params: Dict[str, Any] | None = None) -> Dict[str, Any]:
        self.calls.append((capability, params or {}))
        v = self._responses.get(capability)
        if isinstance(v, BaseException):
            raise v
        return v


def test_fetch_skill_returns_text_body():
    fc = FakeClient({"network.fetch": {
        "status": 200, "body": b"hello world",
        "final_url": "https://x.com/", "cache_hit": False,
        "headers": {"content-type": "text/plain"},
    }})
    _set_client_for_tests(fc)

    tool = build_fetch_tool()
    out = tool.invoke({"url": "https://x.com/"})
    decoded = json.loads(out)
    assert decoded["status"] == 200
    assert decoded["body"] == "hello world"
    assert decoded["content_type"] == "text/plain"


def test_fetch_skill_base64_for_binary():
    fc = FakeClient({"network.fetch": {
        "status": 200, "body": b"\x89PNG\r\n\x1a\n",
        "final_url": "https://x.com/img.png", "cache_hit": False,
    }})
    _set_client_for_tests(fc)
    tool = build_fetch_tool()
    out = tool.invoke({"url": "https://x.com/img.png"})
    decoded = json.loads(out)
    assert decoded["body"].startswith("base64:")


def test_fetch_skill_surfaces_capability_error():
    fc = FakeClient({"network.fetch": {"error": "url_rejected", "detail": "private"}})
    _set_client_for_tests(fc)
    tool = build_fetch_tool()
    out = tool.invoke({"url": "https://10.0.0.1/"})
    decoded = json.loads(out)
    assert decoded["error"] == "url_rejected"


def test_search_skill_returns_results():
    fc = FakeClient({"network.search": {
        "backend": "brave",
        "query": "x",
        "results": [{"title": "T", "url": "https://x.com/", "snippet": "s"}],
        "cache_hit": False,
    }})
    _set_client_for_tests(fc)
    tool = build_search_tool()
    out = tool.invoke({"query": "x", "max_results": 3})
    decoded = json.loads(out)
    assert decoded["backend"] == "brave"
    assert decoded["results"][0]["title"] == "T"
    # Confirm the call params propagate.
    assert fc.calls[-1][1]["max_results"] == 3
