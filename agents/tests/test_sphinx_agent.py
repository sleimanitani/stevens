"""Tests for Sphinx — the PDF agent."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List

import pytest

from agents.pdf import agent as sphinx
from shared.events import (
    EmailReceivedEvent,
    PDFParseRequestedEvent,
    PDFParseResponseEvent,
)


class FakeBus:
    def __init__(self) -> None:
        self.published: List = []

    async def publish(self, event):
        self.published.append(event)


@pytest.fixture
def fake_bus(monkeypatch):
    fb = FakeBus()
    monkeypatch.setattr(sphinx.bus, "publish", fb.publish)
    yield fb


@pytest.mark.asyncio
async def test_request_publishes_response(fake_bus, monkeypatch, tmp_path: Path):
    def fake_dispatch(req):
        return {
            "text": "extracted text",
            "tables": [],
            "pages": 3,
            "used_ocr": False,
            "warnings": [],
            "strategy_used": "native_text",
            "decision_reason": "PDF has a text layer",
        }

    monkeypatch.setattr(sphinx, "_do_dispatch", fake_dispatch)

    req = PDFParseRequestedEvent(
        account_id="system", request_id="r-1",
        path=str(tmp_path / "x.pdf"), requested_by="researcher",
    )
    await sphinx.handle(req, config={})

    assert len(fake_bus.published) == 1
    resp = fake_bus.published[0]
    assert isinstance(resp, PDFParseResponseEvent)
    assert resp.text == "extracted text"
    assert resp.strategy_used == "native_text"
    assert resp.pages == 3


@pytest.mark.asyncio
async def test_dispatch_error_surfaces(fake_bus, monkeypatch, tmp_path: Path):
    def fake_dispatch(req):
        return {"error": "encrypted", "detail": "PDF is encrypted"}

    monkeypatch.setattr(sphinx, "_do_dispatch", fake_dispatch)

    req = PDFParseRequestedEvent(
        account_id="system", request_id="r-2",
        path=str(tmp_path / "x.pdf"), requested_by="x",
    )
    await sphinx.handle(req, config={})
    resp = fake_bus.published[0]
    assert resp.error == "encrypted"


@pytest.mark.asyncio
async def test_dispatch_crash_surfaces(fake_bus, monkeypatch, tmp_path: Path):
    def fake_dispatch(req):
        raise RuntimeError("boom")

    monkeypatch.setattr(sphinx, "_do_dispatch", fake_dispatch)

    req = PDFParseRequestedEvent(
        account_id="system", request_id="r-3",
        path=str(tmp_path / "x.pdf"), requested_by="x",
    )
    await sphinx.handle(req, config={})
    resp = fake_bus.published[0]
    assert resp.error == "dispatch_crashed"
    assert "RuntimeError" in resp.error_detail


@pytest.mark.asyncio
async def test_unrelated_event_ignored(fake_bus):
    irrelevant = EmailReceivedEvent(
        account_id="gmail.x", message_id="m", thread_id="t",
        **{"from": "x@y.z"}, raw_ref="r",
    )
    await sphinx.handle(irrelevant, config={})
    assert fake_bus.published == []
