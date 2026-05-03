"""Tests for shared.creatures.feed — observation-feed writer + UUIDv7."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest

from shared.creatures.feed import (
    KIND_THINK,
    SCHEMA_VERSION,
    FeedEvent,
    ObservationFeed,
    feed_path_for,
    feed_root,
    parse_uuid7_timestamp,
    uuid7,
)


# ----------------------------- UUIDv7 ------------------------------------


def test_uuid7_is_a_uuid():
    u = uuid7()
    assert isinstance(u, UUID)


def test_uuid7_version_is_7():
    """Version field is bits 48-51 of the 128-bit value (network-order)."""
    u = uuid7()
    # uuid.version exposes this as an int.
    assert u.version == 7


def test_uuid7_variant_is_rfc4122():
    """Variant bits 64-65 must be 0b10."""
    u = uuid7()
    assert u.variant == "specified in RFC 4122"


def test_uuid7s_are_sortable_in_time_order():
    """Two UUIDv7s minted close in time should sort in mint order."""
    earliest = uuid7()
    time.sleep(0.005)  # 5ms — comfortably above the ms granularity
    later = uuid7()
    time.sleep(0.005)
    latest = uuid7()
    assert earliest < later < latest


def test_uuid7_embedded_timestamp_recovers_close_to_now():
    """UUIDv7 only stores ms precision; allow 1ms slop on either side."""
    from datetime import timedelta

    before = datetime.now(tz=timezone.utc)
    u = uuid7()
    after = datetime.now(tz=timezone.utc)
    recovered = parse_uuid7_timestamp(str(u))
    slop = timedelta(milliseconds=1)
    assert before - slop <= recovered <= after + slop


# ----------------------------- envelope ----------------------------------


def test_feed_event_to_json_line_round_trip():
    e = FeedEvent(
        creature_id="email_pm.personal",
        event_id="0190f23a-7c1d-7000-8abc-def012345678",
        ts="2026-05-03T17:42:01.123456Z",
        kind="think",
        correlation_id=None,
        data={"text": "thinking about Berwyn"},
    )
    line = e.to_json_line()
    assert line.endswith("\n")
    parsed = json.loads(line)
    assert parsed["schema_version"] == SCHEMA_VERSION
    assert parsed["creature_id"] == "email_pm.personal"
    assert parsed["event_id"] == "0190f23a-7c1d-7000-8abc-def012345678"
    assert parsed["kind"] == "think"
    assert parsed["data"] == {"text": "thinking about Berwyn"}
    assert parsed["correlation_id"] is None


def test_feed_event_key_order_is_stable():
    """Stable key order helps human-eyeball diffs of the file in tests."""
    e = FeedEvent(
        creature_id="x",
        event_id="00000000-0000-7000-8000-000000000000",
        ts="2026-05-03T00:00:00.000000Z",
        kind="think",
        data={"a": 1},
    )
    line = e.to_json_line().rstrip()
    # schema_version first, data last.
    assert line.startswith('{"schema_version":')
    assert line.endswith('"data":{"a":1}}')


# ----------------------------- paths -------------------------------------


def test_feed_root_default(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("DEMIURGE_CREATURE_STATE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert feed_root() == tmp_path / ".local" / "state" / "demiurge" / "creatures"


def test_feed_root_env_override(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("DEMIURGE_CREATURE_STATE", str(tmp_path / "x"))
    assert feed_root() == tmp_path / "x"


def test_feed_path_for_layout(tmp_path: Path):
    assert (
        feed_path_for("trip_planner.tokyo_2026", base=tmp_path)
        == tmp_path / "trip_planner.tokyo_2026" / "events.jsonl"
    )


# ----------------------------- writer ------------------------------------


def test_observation_feed_creates_file_on_init(tmp_path: Path):
    f = ObservationFeed("email_pm", base=tmp_path)
    assert f.path.exists()
    assert f.path == tmp_path / "email_pm" / "events.jsonl"


def test_observation_feed_file_mode_is_0640(tmp_path: Path):
    f = ObservationFeed("email_pm", base=tmp_path)
    mode = f.path.stat().st_mode & 0o777
    # umask may strip group/world bits; we only assert no world-write.
    # Operator-running-Demiurge has rw; no world rwx is the security floor.
    assert mode & 0o002 == 0  # no world-write
    assert mode & 0o001 == 0  # no world-execute


def test_observation_feed_append_writes_one_line(tmp_path: Path):
    f = ObservationFeed("email_pm", base=tmp_path)
    eid = f.append(KIND_THINK, {"text": "hello"})
    UUID(eid)  # parses
    contents = f.path.read_text()
    assert contents.count("\n") == 1


def test_observation_feed_append_sets_creature_id_and_kind(tmp_path: Path):
    f = ObservationFeed("email_pm", base=tmp_path)
    f.append(KIND_THINK, {"text": "hello"})
    line = f.path.read_text().strip()
    obj = json.loads(line)
    assert obj["creature_id"] == "email_pm"
    assert obj["kind"] == KIND_THINK
    assert obj["data"] == {"text": "hello"}


def test_observation_feed_correlation_id_passes_through(tmp_path: Path):
    f = ObservationFeed("email_pm", base=tmp_path)
    parent = "0190f23a-7c1d-7000-8abc-aaaaaaaaaaaa"
    f.append(KIND_THINK, {"text": "child"}, correlation_id=parent)
    obj = json.loads(f.path.read_text().strip())
    assert obj["correlation_id"] == parent


def test_observation_feed_read_all_round_trip(tmp_path: Path):
    f = ObservationFeed("email_pm", base=tmp_path)
    f.append(KIND_THINK, {"text": "first"})
    f.append(KIND_THINK, {"text": "second"})
    f.append(KIND_THINK, {"text": "third"})
    events = list(f.read_all())
    assert [e.data["text"] for e in events] == ["first", "second", "third"]
    assert all(e.creature_id == "email_pm" for e in events)


def test_observation_feed_event_ids_are_unique_and_sortable(tmp_path: Path):
    f = ObservationFeed("email_pm", base=tmp_path)
    eids = [f.append(KIND_THINK, {"text": str(i)}) for i in range(20)]
    assert len(set(eids)) == 20  # unique
    assert eids == sorted(eids)  # sortable in mint order


# ----------------------------- concurrency -------------------------------


def test_observation_feed_concurrent_appends_preserve_all_events(tmp_path: Path):
    """Parallel appends from many threads — no interleaving, no loss."""
    f = ObservationFeed("email_pm", base=tmp_path)
    n_threads = 10
    n_per_thread = 50
    barrier = threading.Barrier(n_threads)

    def worker(i: int):
        barrier.wait()
        for j in range(n_per_thread):
            f.append(KIND_THINK, {"thread": i, "n": j})

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    events = list(f.read_all())
    assert len(events) == n_threads * n_per_thread

    # Every line is a complete, parseable JSON object — no torn writes.
    seen = set()
    for e in events:
        seen.add((e.data["thread"], e.data["n"]))
    assert len(seen) == n_threads * n_per_thread

    # Every event_id is unique even across threads.
    eids = [e.event_id for e in events]
    assert len(set(eids)) == len(eids)
