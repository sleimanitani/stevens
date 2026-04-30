"""Tests for Arachne — the web agent."""

from __future__ import annotations

import asyncio
import base64
from typing import Any, Dict, List

import pytest

from agents.web import agent as arachne
from shared.events import (
    EmailReceivedEvent,
    WebFetchRequestedEvent,
    WebFetchResponseEvent,
    WebSearchRequestedEvent,
    WebSearchResponseEvent,
)
from shared.security_client import TransportError


class FakeClient:
    def __init__(self, responses: Dict[str, Any]) -> None:
        self._responses = responses
        self.calls: List = []

    async def call(self, capability: str, params=None):
        self.calls.append((capability, params or {}))
        v = self._responses.get(capability)
        if isinstance(v, BaseException):
            raise v
        return v


class FakeBus:
    def __init__(self) -> None:
        self.published = []

    async def publish(self, event):
        self.published.append(event)


@pytest.fixture
def fake_bus(monkeypatch):
    fb = FakeBus()
    monkeypatch.setattr(arachne.bus, "publish", fb.publish)
    yield fb


@pytest.mark.asyncio
async def test_fetch_request_publishes_response(fake_bus):
    fc = FakeClient({"network.fetch": {
        "status": 200,
        "body": b"hello",
        "final_url": "https://x.com/",
        "cache_hit": False,
    }})
    arachne._set_client_for_tests(fc)

    req = WebFetchRequestedEvent(
        account_id="system", request_id="r-1",
        url="https://x.com/", requested_by="researcher",
    )
    await arachne.handle(req, config={})

    assert len(fake_bus.published) == 1
    resp = fake_bus.published[0]
    assert isinstance(resp, WebFetchResponseEvent)
    assert resp.request_id == "r-1"
    assert resp.status == 200
    assert resp.body_text == "hello"


@pytest.mark.asyncio
async def test_fetch_binary_response_base64(fake_bus):
    binary = b"\x89PNG\r\n\x1a\n"
    fc = FakeClient({"network.fetch": {
        "status": 200, "body": binary, "final_url": "https://x.com/i.png",
        "cache_hit": False,
    }})
    arachne._set_client_for_tests(fc)

    req = WebFetchRequestedEvent(
        account_id="system", request_id="r-2",
        url="https://x.com/i.png", requested_by="x",
    )
    await arachne.handle(req, config={})
    resp = fake_bus.published[0]
    assert resp.body_text is None
    assert resp.body_b64 == base64.b64encode(binary).decode("ascii")


@pytest.mark.asyncio
async def test_fetch_capability_error_surfaces(fake_bus):
    fc = FakeClient({"network.fetch": {"error": "url_rejected", "detail": "private"}})
    arachne._set_client_for_tests(fc)

    req = WebFetchRequestedEvent(
        account_id="system", request_id="r-3",
        url="https://10.0.0.1/", requested_by="x",
    )
    await arachne.handle(req, config={})
    resp = fake_bus.published[0]
    assert resp.error == "url_rejected"
    assert resp.error_detail == "private"


@pytest.mark.asyncio
async def test_fetch_transport_error_surfaces(fake_bus):
    fc = FakeClient({"network.fetch": TransportError("socket gone")})
    arachne._set_client_for_tests(fc)

    req = WebFetchRequestedEvent(
        account_id="system", request_id="r-4",
        url="https://x.com/", requested_by="x",
    )
    await arachne.handle(req, config={})
    resp = fake_bus.published[0]
    assert resp.error == "broker_error"
    assert "socket gone" in (resp.error_detail or "")


@pytest.mark.asyncio
async def test_search_request_publishes_response(fake_bus):
    fc = FakeClient({"network.search": {
        "backend": "brave",
        "results": [{"title": "T", "url": "https://x.com/", "snippet": "s"}],
        "cache_hit": False,
    }})
    arachne._set_client_for_tests(fc)

    req = WebSearchRequestedEvent(
        account_id="system", request_id="r-5",
        query="hello world", max_results=5, requested_by="researcher",
    )
    await arachne.handle(req, config={})

    resp = fake_bus.published[0]
    assert isinstance(resp, WebSearchResponseEvent)
    assert resp.request_id == "r-5"
    assert resp.backend == "brave"
    assert len(resp.results) == 1


@pytest.mark.asyncio
async def test_concurrent_workers_capped(fake_bus, monkeypatch):
    """Concurrency limit: with semaphore(2), at most 2 requests run in flight."""
    arachne._set_semaphore_for_tests(asyncio.Semaphore(2))

    in_flight = 0
    max_in_flight = 0
    started = asyncio.Event()
    permit = asyncio.Event()

    async def slow_call(capability, params=None):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        started.set()
        await permit.wait()
        in_flight -= 1
        return {"status": 200, "body": b"x", "final_url": params["url"], "cache_hit": False}

    class HoldClient:
        async def call(self, capability, params=None):
            return await slow_call(capability, params)

    arachne._set_client_for_tests(HoldClient())

    # Kick off 4 concurrent handle() calls.
    coros = [
        arachne.handle(
            WebFetchRequestedEvent(
                account_id="system", request_id=f"r-{i}",
                url=f"https://x.com/{i}", requested_by="x",
            ),
            config={},
        )
        for i in range(4)
    ]
    task = asyncio.gather(*coros)

    # Wait for the first batch to start, then check the cap.
    await started.wait()
    await asyncio.sleep(0.01)  # let other tasks attempt to start
    assert max_in_flight <= 2

    # Release.
    permit.set()
    await task
    arachne._set_semaphore_for_tests(None)


@pytest.mark.asyncio
async def test_unrelated_event_ignored(fake_bus):
    arachne._set_client_for_tests(FakeClient({}))
    irrelevant = EmailReceivedEvent(
        account_id="gmail.x", message_id="m", thread_id="t",
        **{"from": "x@y.z"}, raw_ref="r",
    )
    await arachne.handle(irrelevant, config={})
    assert fake_bus.published == []
