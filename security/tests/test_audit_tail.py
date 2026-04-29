"""Tests for stevens_security.audit_tail."""

from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from stevens_security import audit_tail


def _write_log(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def test_print_tail_no_file(tmp_path: Path) -> None:
    out = io.StringIO()
    audit_tail.print_tail(tmp_path, n=10, out=out)
    assert "no audit lines yet" in out.getvalue()


def test_print_tail_returns_last_n(tmp_path: Path) -> None:
    today = audit_tail.today_utc()
    _write_log(
        tmp_path / f"{today}.jsonl",
        [
            {"ts": "2026-04-29T00:00:01Z", "trace_id": "1", "outcome": "ok",
             "latency_ms": 10, "caller": "a", "capability": "ping"},
            {"ts": "2026-04-29T00:00:02Z", "trace_id": "2", "outcome": "ok",
             "latency_ms": 12, "caller": "b", "capability": "ping"},
            {"ts": "2026-04-29T00:00:03Z", "trace_id": "3", "outcome": "deny",
             "latency_ms": 5, "caller": "c", "capability": "gmail.search",
             "account_id": "gmail.x", "error_code": "DENY"},
        ],
    )
    out = io.StringIO()
    audit_tail.print_tail(tmp_path, n=2, out=out)
    text = out.getvalue()
    # Only last 2 lines.
    assert "trace_id" not in text  # the formatted line doesn't include trace_id
    assert "a/ping" not in text
    assert "b/ping" in text
    assert "c/gmail.search" in text
    assert "DENY" in text  # err code shown


def test_print_tail_raw_mode_passthrough(tmp_path: Path) -> None:
    today = audit_tail.today_utc()
    rec = {"ts": "x", "trace_id": "1", "outcome": "ok", "latency_ms": 1}
    _write_log(tmp_path / f"{today}.jsonl", [rec])
    out = io.StringIO()
    audit_tail.print_tail(tmp_path, n=5, out=out, raw_mode=True)
    # In raw mode the output is the JSON line unmodified.
    assert json.loads(out.getvalue().strip()) == rec


def test_format_line_unparseable_is_safe() -> None:
    formatted = audit_tail.format_line("not-json")
    assert "unparseable" in formatted


def test_follow_picks_up_new_lines(tmp_path: Path) -> None:
    """The follow loop should emit new lines that arrive between iterations."""
    today = audit_tail.today_utc()
    log = tmp_path / f"{today}.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        json.dumps({"ts": "x", "trace_id": "1", "outcome": "ok", "latency_ms": 1, "caller": "a", "capability": "ping"})
        + "\n"
    )
    out = io.StringIO()

    # First iteration sees no NEW data (we start at end-of-file).
    # Then we append, second iteration emits.
    import threading

    def append_after_delay():
        import time
        time.sleep(0.05)
        with log.open("a") as f:
            f.write(
                json.dumps(
                    {"ts": "y", "trace_id": "2", "outcome": "ok",
                     "latency_ms": 2, "caller": "b", "capability": "ping"}
                )
                + "\n"
            )

    t = threading.Thread(target=append_after_delay)
    t.start()
    audit_tail.follow(tmp_path, out=out, sleep_s=0.1, _max_iterations=3)
    t.join()
    assert "b/ping" in out.getvalue()
