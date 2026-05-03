"""Tests for AuditAngel — v0.11 step 3e.3."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import pytest

from demiurge.audit import AuditWriter
from demiurge.pantheon.hephaestus import AuditAngel, feed_event_to_audit_entry
from shared.creatures.context import AngelContext
from shared.creatures.feed import (
    KIND_LIFECYCLE,
    KIND_LLM_EXCHANGE,
    KIND_THINK,
    KIND_TOOL_CALL_END,
    KIND_TOOL_CALL_START,
    FeedEvent,
    ObservationFeed,
)


# ----------------------------- fixtures ----------------------------------


@pytest.fixture
def host_feed(tmp_path: Path) -> ObservationFeed:
    return ObservationFeed("email_pm.personal", base=tmp_path / "feeds")


@pytest.fixture
def audit_root(tmp_path: Path) -> Path:
    root = tmp_path / "audit"
    root.mkdir()
    return root


def _angel_ctx(host_feed: ObservationFeed) -> AngelContext:
    return AngelContext(
        creature_id=f"enkidu.audit.{host_feed.creature_id}",
        display_name=f"Enkidu Audit Angel — {host_feed.creature_id}",
        audit=host_feed,  # angel's own audit feed; in v0.11 we reuse the host's
        logger=logging.getLogger("test"),
        god="enkidu",
        angel_name="audit",
        host_creature_id=host_feed.creature_id,
        host_feed=host_feed,
    )


# ----------------------------- feed_event_to_audit_entry -----------------


def _start_event(event_id: str, ts: str, capability: str = "gmail.send", god: str = "enkidu", args: dict | None = None) -> FeedEvent:
    return FeedEvent(
        creature_id="email_pm.personal",
        event_id=event_id,
        ts=ts,
        kind=KIND_TOOL_CALL_START,
        data={
            "capability": capability,
            "god": god,
            "args": args or {"to": "alice@example.com", "body": "hi"},
        },
    )


def _end_event(event_id: str, ts: str, correlation_id: str, *, capability: str = "gmail.send", result=None, error=None) -> FeedEvent:
    data: dict = {"capability": capability, "god": "enkidu"}
    if error is not None:
        data["error"] = error
    else:
        data["result"] = result if result is not None else {"sent": True}
    return FeedEvent(
        creature_id="email_pm.personal",
        event_id=event_id,
        ts=ts,
        kind=KIND_TOOL_CALL_END,
        correlation_id=correlation_id,
        data=data,
    )


def test_projection_ok_outcome():
    start = _start_event(
        "0190f23a-7c1d-7000-8abc-aaaaaaaaaaaa",
        "2026-05-03T17:42:01.100000Z",
    )
    end = _end_event(
        "0190f23a-7c1d-7000-8abc-bbbbbbbbbbbb",
        "2026-05-03T17:42:01.450000Z",
        correlation_id=start.event_id,
        result={"sent": True, "id": "msg_123"},
    )
    entry = feed_event_to_audit_entry(start=start, end=end)
    assert entry.outcome == "ok"
    assert entry.caller == "email_pm.personal"
    assert entry.capability == "gmail.send"
    assert entry.trace_id == start.event_id
    assert entry.latency_ms == 350
    assert entry.error_code is None
    # Args become param_values (none of the keys are in the default
    # sensitive-keys set).
    assert entry.param_values == {"to": "alice@example.com", "body": "hi"}
    assert entry.param_hashes == {}
    assert entry.extra.get("god") == "enkidu"


def test_projection_error_outcome():
    start = _start_event(
        "0190f23a-7c1d-7000-8abc-aaaaaaaaaaaa",
        "2026-05-03T17:42:01.100000Z",
    )
    end = _end_event(
        "0190f23a-7c1d-7000-8abc-bbbbbbbbbbbb",
        "2026-05-03T17:42:01.450000Z",
        correlation_id=start.event_id,
        error="RuntimeError: upstream timeout",
    )
    entry = feed_event_to_audit_entry(start=start, end=end)
    assert entry.outcome == "internal"
    assert entry.error_code == "RuntimeError"


def test_projection_sensitive_args_get_hashed():
    start = _start_event(
        "0190f23a-7c1d-7000-8abc-aaaaaaaaaaaa",
        "2026-05-03T17:42:01.100000Z",
        capability="auth.login",
        args={"username": "alice", "password": "supersecret"},
    )
    end = _end_event(
        "0190f23a-7c1d-7000-8abc-bbbbbbbbbbbb",
        "2026-05-03T17:42:01.150000Z",
        correlation_id=start.event_id,
    )
    entry = feed_event_to_audit_entry(start=start, end=end)
    # Default sensitive keys include "password"
    assert "password" in entry.param_hashes
    assert "supersecret" not in str(entry.param_values)
    assert entry.param_values == {"username": "alice"}


def test_projection_custom_sensitive_keys():
    start = _start_event(
        "0190f23a-7c1d-7000-8abc-aaaaaaaaaaaa",
        "2026-05-03T17:42:01.100000Z",
        args={"to": "alice", "body": "important secret stuff"},
    )
    end = _end_event(
        "0190f23a-7c1d-7000-8abc-bbbbbbbbbbbb",
        "2026-05-03T17:42:01.150000Z",
        correlation_id=start.event_id,
    )
    entry = feed_event_to_audit_entry(
        start=start, end=end, sensitive_arg_keys={"body"}
    )
    assert "body" in entry.param_hashes
    assert "to" in entry.param_values


def test_projection_correlation_mismatch_raises():
    start = _start_event("aaa", "2026-05-03T17:42:01.100000Z")
    end = _end_event("bbb", "2026-05-03T17:42:01.150000Z", correlation_id="WRONG")
    with pytest.raises(ValueError, match="correlation_id"):
        feed_event_to_audit_entry(start=start, end=end)


def test_projection_account_id_extracted_when_string():
    start = _start_event(
        "aaa",
        "2026-05-03T17:42:01.100000Z",
        args={"to": "x", "account_id": "gmail.work"},
    )
    end = _end_event("bbb", "2026-05-03T17:42:01.150000Z", correlation_id="aaa")
    entry = feed_event_to_audit_entry(start=start, end=end)
    assert entry.account_id == "gmail.work"


# ----------------------------- AuditAngel.observe ------------------------


def test_audit_angel_projects_complete_call(host_feed: ObservationFeed, audit_root: Path):
    """End-to-end: write start+end to feed, observe, audit log gets one line."""
    writer = AuditWriter(audit_root)
    angel = AuditAngel(ctx=_angel_ctx(host_feed), audit_writer=writer)

    start_id = host_feed.append(
        KIND_TOOL_CALL_START,
        {"capability": "gmail.send", "god": "enkidu", "args": {"to": "x"}},
    )
    host_feed.append(
        KIND_TOOL_CALL_END,
        {"capability": "gmail.send", "god": "enkidu", "result": {"ok": True}},
        correlation_id=start_id,
    )

    written = asyncio.run(angel.observe())
    assert written == 1

    # Check the audit log file.
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    audit_file = audit_root / f"{today}.jsonl"
    lines = audit_file.read_text().strip().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["caller"] == "email_pm.personal"
    assert parsed["capability"] == "gmail.send"
    assert parsed["outcome"] == "ok"


def test_audit_angel_idempotent_within_observe(host_feed: ObservationFeed, audit_root: Path):
    """Re-running observe() doesn't double-project."""
    writer = AuditWriter(audit_root)
    angel = AuditAngel(ctx=_angel_ctx(host_feed), audit_writer=writer)

    start_id = host_feed.append(
        KIND_TOOL_CALL_START,
        {"capability": "gmail.send", "god": "enkidu", "args": {"to": "x"}},
    )
    host_feed.append(
        KIND_TOOL_CALL_END,
        {"capability": "gmail.send", "god": "enkidu", "result": {"ok": True}},
        correlation_id=start_id,
    )

    asyncio.run(angel.observe())
    second = asyncio.run(angel.observe())
    assert second == 0  # nothing new

    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    audit_file = audit_root / f"{today}.jsonl"
    lines = audit_file.read_text().strip().splitlines()
    assert len(lines) == 1


def test_audit_angel_skips_non_tool_events(host_feed: ObservationFeed, audit_root: Path):
    """think / llm.exchange / lifecycle events are not the audit angel's concern."""
    writer = AuditWriter(audit_root)
    angel = AuditAngel(ctx=_angel_ctx(host_feed), audit_writer=writer)

    host_feed.append(KIND_THINK, {"text": "thinking"})
    host_feed.append(KIND_LLM_EXCHANGE, {"prompt": "...", "completion": "..."})
    host_feed.append(KIND_LIFECYCLE, {"event": "spawned"})

    written = asyncio.run(angel.observe())
    assert written == 0

    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    audit_file = audit_root / f"{today}.jsonl"
    assert not audit_file.exists() or audit_file.read_text() == ""


def test_audit_angel_handles_partial_pair(host_feed: ObservationFeed, audit_root: Path):
    """A start without an end → no projection until the end arrives."""
    writer = AuditWriter(audit_root)
    angel = AuditAngel(ctx=_angel_ctx(host_feed), audit_writer=writer)

    start_id = host_feed.append(
        KIND_TOOL_CALL_START,
        {"capability": "gmail.send", "god": "enkidu", "args": {"to": "x"}},
    )

    written = asyncio.run(angel.observe())
    assert written == 0  # nothing to project — only a start

    # Now the end lands.
    host_feed.append(
        KIND_TOOL_CALL_END,
        {"capability": "gmail.send", "god": "enkidu", "result": {"ok": True}},
        correlation_id=start_id,
    )
    written = asyncio.run(angel.observe())
    assert written == 1


def test_audit_angel_projects_multiple_calls(host_feed: ObservationFeed, audit_root: Path):
    writer = AuditWriter(audit_root)
    angel = AuditAngel(ctx=_angel_ctx(host_feed), audit_writer=writer)

    for i in range(5):
        start_id = host_feed.append(
            KIND_TOOL_CALL_START,
            {"capability": "gmail.send", "god": "enkidu", "args": {"to": f"u{i}"}},
        )
        host_feed.append(
            KIND_TOOL_CALL_END,
            {"capability": "gmail.send", "god": "enkidu", "result": {"ok": True}},
            correlation_id=start_id,
        )

    written = asyncio.run(angel.observe())
    assert written == 5

    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    audit_file = audit_root / f"{today}.jsonl"
    lines = audit_file.read_text().strip().splitlines()
    assert len(lines) == 5


def test_audit_angel_projects_error_outcome(host_feed: ObservationFeed, audit_root: Path):
    writer = AuditWriter(audit_root)
    angel = AuditAngel(ctx=_angel_ctx(host_feed), audit_writer=writer)

    start_id = host_feed.append(
        KIND_TOOL_CALL_START,
        {"capability": "gmail.send", "god": "enkidu", "args": {"to": "x"}},
    )
    host_feed.append(
        KIND_TOOL_CALL_END,
        {
            "capability": "gmail.send",
            "god": "enkidu",
            "error": "ConnectionError: refused",
        },
        correlation_id=start_id,
    )

    written = asyncio.run(angel.observe())
    assert written == 1

    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    audit_file = audit_root / f"{today}.jsonl"
    parsed = json.loads(audit_file.read_text().strip())
    assert parsed["outcome"] == "internal"
    assert parsed["error_code"] == "ConnectionError"


def test_audit_angel_orphan_end_skipped(host_feed: ObservationFeed, audit_root: Path):
    """An end event whose correlation_id has no matching start → skipped silently."""
    writer = AuditWriter(audit_root)
    angel = AuditAngel(ctx=_angel_ctx(host_feed), audit_writer=writer)

    host_feed.append(
        KIND_TOOL_CALL_END,
        {"capability": "gmail.send", "god": "enkidu", "result": {"ok": True}},
        correlation_id="0190f23a-7c1d-7000-8abc-MISSING",
    )

    written = asyncio.run(angel.observe())
    assert written == 0


def test_audit_angel_implements_angel_abc():
    """AuditAngel is a real Angel subclass."""
    from shared.creatures.base import Angel

    assert issubclass(AuditAngel, Angel)
