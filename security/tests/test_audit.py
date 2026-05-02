"""Tests for the append-only audit writer."""

import asyncio
import json
from datetime import datetime, timezone

import pytest

from demiurge.audit import AuditEntry, AuditWriter, hash_param


def make_entry(**kwargs) -> AuditEntry:
    base = dict(
        ts="2026-04-22T12:00:00+00:00",
        trace_id="trace-1",
        outcome="ok",
        latency_ms=3,
    )
    base.update(kwargs)
    return AuditEntry(**base)


# --- hash_param ---


def test_hash_param_stable_across_calls():
    assert hash_param({"a": 1, "b": 2}) == hash_param({"a": 1, "b": 2})


def test_hash_param_stable_across_key_order():
    assert hash_param({"a": 1, "b": 2}) == hash_param({"b": 2, "a": 1})


def test_hash_param_differs_for_different_inputs():
    assert hash_param({"a": 1}) != hash_param({"a": 2})
    assert hash_param("secret") != hash_param("public")


def test_hash_param_accepts_none_and_bytes_and_floats():
    # Audit hashes are pragmatic — they accept anything reasonable.
    assert len(hash_param(None)) == 64
    assert len(hash_param(b"\x00\x01")) == 64
    assert len(hash_param(1.5)) == 64
    assert len(hash_param(["a", 1, None, True, {"nested": "ok"}])) == 64


def test_hash_param_nested_stable():
    a = {"outer": {"x": [1, 2, {"k": "v"}], "y": "z"}}
    b = {"outer": {"y": "z", "x": [1, 2, {"k": "v"}]}}
    assert hash_param(a) == hash_param(b)


# --- AuditWriter ---


@pytest.mark.asyncio
async def test_single_write_produces_jsonl(tmp_path):
    writer = AuditWriter(tmp_path)
    await writer.log(make_entry())
    files = sorted(tmp_path.iterdir())
    assert len(files) == 1
    line = files[0].read_text().strip()
    obj = json.loads(line)
    assert obj["trace_id"] == "trace-1"
    assert obj["outcome"] == "ok"
    assert obj["latency_ms"] == 3


@pytest.mark.asyncio
async def test_multiple_writes_same_day_single_file(tmp_path):
    writer = AuditWriter(tmp_path)
    await writer.log(make_entry(trace_id="a"))
    await writer.log(make_entry(trace_id="b"))
    await writer.log(make_entry(trace_id="c"))
    files = list(tmp_path.iterdir())
    assert len(files) == 1
    lines = files[0].read_text().strip().split("\n")
    assert len(lines) == 3
    traces = [json.loads(line)["trace_id"] for line in lines]
    assert traces == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_daily_rollover(tmp_path):
    current = [datetime(2026, 4, 22, 23, 59, 30, tzinfo=timezone.utc)]

    def clock():
        return current[0]

    writer = AuditWriter(tmp_path, clock=clock)
    await writer.log(make_entry(trace_id="before"))
    current[0] = datetime(2026, 4, 23, 0, 0, 30, tzinfo=timezone.utc)
    await writer.log(make_entry(trace_id="after"))

    files = sorted(p.name for p in tmp_path.iterdir())
    assert files == ["2026-04-22.jsonl", "2026-04-23.jsonl"]


@pytest.mark.asyncio
async def test_root_dir_created_if_missing(tmp_path):
    target = tmp_path / "nested" / "deeper" / "audit"
    AuditWriter(target)
    assert target.exists() and target.is_dir()


@pytest.mark.asyncio
async def test_file_permissions_600(tmp_path):
    writer = AuditWriter(tmp_path)
    await writer.log(make_entry())
    files = list(tmp_path.iterdir())
    assert len(files) == 1
    mode = files[0].stat().st_mode & 0o777
    assert mode == 0o600


@pytest.mark.asyncio
async def test_concurrent_writes_produce_valid_json_lines(tmp_path):
    writer = AuditWriter(tmp_path)
    n = 100

    async def one(i: int):
        await writer.log(
            make_entry(
                trace_id=f"t-{i}",
                param_hashes={"p": hash_param({"i": i})},
            )
        )

    await asyncio.gather(*(one(i) for i in range(n)))

    files = list(tmp_path.iterdir())
    assert len(files) == 1
    lines = files[0].read_text().strip().split("\n")
    assert len(lines) == n
    # Every line must be valid JSON with expected shape.
    for line in lines:
        obj = json.loads(line)
        assert obj["outcome"] == "ok"
        assert obj["trace_id"].startswith("t-")
    traces = {json.loads(line)["trace_id"] for line in lines}
    assert len(traces) == n  # no lost/duplicate writes


@pytest.mark.asyncio
async def test_sensitive_param_only_appears_as_hash(tmp_path):
    fixed = datetime(2026, 4, 22, 12, 0, 0, tzinfo=timezone.utc)
    writer = AuditWriter(tmp_path, clock=lambda: fixed)
    secret = "sk-live-abcdef1234567890"
    secret_hash = hash_param(secret)
    entry = make_entry(
        caller="email_pm",
        capability="anthropic.complete",
        param_hashes={"api_key": secret_hash},
        # Clear values only include things we know are safe:
        param_values={"model": "qwen3-30b"},
    )
    await writer.log(entry)
    raw = (tmp_path / "2026-04-22.jsonl").read_text()
    # Defensive: the secret string itself must not appear anywhere in the log.
    assert secret not in raw
    assert secret_hash in raw
    obj = json.loads(raw.strip())
    assert obj["param_values"] == {"model": "qwen3-30b"}
    assert obj["param_hashes"] == {"api_key": secret_hash}


@pytest.mark.asyncio
async def test_naive_datetime_clock_treated_as_utc(tmp_path):
    # A clock that returns a naive datetime should be coerced to UTC, not
    # interpreted as local time — behavior guarantees reproducible file names.
    current = datetime(2026, 4, 22, 12, 0, 0)  # naive
    writer = AuditWriter(tmp_path, clock=lambda: current)
    await writer.log(make_entry())
    assert (tmp_path / "2026-04-22.jsonl").exists()
