"""Per-Creature observation feed — the single source of synchronization.

v0.11 step 3a. Every Creature has one events.jsonl file at::

    ~/.local/state/demiurge/creatures/<creature_id>/events.jsonl

Mode 0640, owned by the demiurge process uid. Every observable event the
Creature does — capability call start/end, ``think()`` call, LLM exchange,
lifecycle event — is appended by the supervisor with a stable envelope.
Angels read the same feed and project their slice into their commissioning
god's substrate.

Single time source. Single ID namespace (UUIDv7 — sortable, embeds ms
timestamp). Single envelope schema. Cross-angel join is ``JOIN ON event_id``.

The feed writer is process-safe via ``fcntl.flock(LOCK_EX)`` around each
append. JSONL means one event per line; readers (angels) tail the file.

This module deliberately has no Creature-facing API. Creatures don't write
to the feed directly — universal tools and the dispatch layer do, with the
``CreatureContext.feed`` handle injected at forge time.
"""

from __future__ import annotations

import fcntl
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional
from uuid import UUID

SCHEMA_VERSION = 1
DEFAULT_BASE = "~/.local/state/demiurge/creatures"

# Standard event kinds. Stable; never rename. Add to the end of this list,
# don't repurpose existing strings — angels' projections key off these.
KIND_TOOL_CALL_START = "tool.call.start"
KIND_TOOL_CALL_END = "tool.call.end"
KIND_THINK = "think"
KIND_LLM_EXCHANGE = "llm.exchange"
KIND_LIFECYCLE = "lifecycle"
KIND_MORTAL_RETURN = "lifecycle.return"


# ----------------------------- UUIDv7 ------------------------------------


_uuid7_lock = threading.Lock()
_uuid7_last_ms: int = 0
_uuid7_counter: int = 0


def uuid7() -> UUID:
    """Generate a UUIDv7 (RFC 9562). Sortable; embeds 48-bit ms timestamp.

    Python 3.10's ``uuid`` module doesn't ship UUIDv7 (added in 3.13). This
    is a small hand-rolled implementation using RFC 9562 §6.2 Method 1
    (fixed-length dedicated counter) for strict monotonicity within a ms:

        bits  0-47  : unix_ts_ms (big-endian)
        bits 48-51  : version = 0x7
        bits 52-63  : counter (12 bits) — increments within the same ms
        bits 64-65  : variant = 0b10
        bits 66-127 : rand_b (62 random bits, CSPRNG)

    The counter resets to a small random offset whenever ms advances, so
    rapid-fire calls inside a single ms still produce strictly increasing
    UUIDs (up to 4093 per ms before wraparound — enough for any realistic
    workload). The thread lock keeps the counter coherent across threads.
    """
    global _uuid7_last_ms, _uuid7_counter

    with _uuid7_lock:
        ms = int(time.time() * 1000) & 0xFFFFFFFFFFFF  # 48 bits
        if ms == _uuid7_last_ms:
            _uuid7_counter += 1
            if _uuid7_counter >= 0x1000:
                # 12-bit counter exhausted; bump to next ms manually so we
                # remain monotonic. (Rare; only on absurd write storms.)
                ms = _uuid7_last_ms + 1
                _uuid7_counter = secrets.randbits(8)  # leave room to grow
        else:
            _uuid7_last_ms = ms
            # Start each new ms at a small random offset so consecutive
            # mss don't collide if the clock jumps backwards a microsecond.
            _uuid7_counter = secrets.randbits(8)
        counter = _uuid7_counter & 0xFFF
        rand_b = secrets.randbits(62)

    val = (
        (ms << 80)
        | (0x7 << 76)
        | (counter << 64)
        | (0x2 << 62)
        | rand_b
    )
    return UUID(int=val)


# ----------------------------- envelope ----------------------------------


@dataclass(frozen=True)
class FeedEvent:
    """One row in a Creature's observation feed.

    Versioned envelope. ``data`` is kind-specific and free-form; the
    envelope itself is stable.
    """

    creature_id: str
    event_id: str          # str(UUIDv7) — keep as string for JSON
    ts: str                # ISO8601 with µs, UTC, e.g. "2026-05-03T17:42:01.123456Z"
    kind: str
    data: dict[str, Any]
    correlation_id: Optional[str] = None
    schema_version: int = SCHEMA_VERSION

    def to_json_line(self) -> str:
        """Render as a single JSON object terminated by newline.

        Keys are written in a stable order so a quick ``grep`` on the file
        sees ``schema_version`` first (cheap shape check), then identity,
        then the kind, then the data. Stable order also helps human-eyeball
        diffs of the file in tests.
        """
        ordered = {
            "schema_version": self.schema_version,
            "creature_id": self.creature_id,
            "event_id": self.event_id,
            "ts": self.ts,
            "kind": self.kind,
            "correlation_id": self.correlation_id,
            "data": self.data,
        }
        return json.dumps(ordered, separators=(",", ":"), ensure_ascii=False) + "\n"


def _now_iso() -> str:
    """ISO8601 in UTC with microsecond precision and trailing Z."""
    return (
        datetime.now(timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%S.%f")
        + "Z"
    )


# ----------------------------- paths -------------------------------------


def feed_root(base: Optional[Path] = None) -> Path:
    """The root directory under which per-Creature feeds live."""
    if base is not None:
        return base
    env = os.environ.get("DEMIURGE_CREATURE_STATE")
    if env:
        return Path(env)
    return Path(DEFAULT_BASE).expanduser()


def feed_path_for(creature_id: str, *, base: Optional[Path] = None) -> Path:
    """``<base>/<creature_id>/events.jsonl`` — the feed file for one Creature."""
    return feed_root(base) / creature_id / "events.jsonl"


# ----------------------------- writer ------------------------------------


class ObservationFeed:
    """Append-only writer for one Creature's events.jsonl.

    Concurrency: each ``append()`` takes an exclusive ``flock`` on the open
    file descriptor for the duration of the write, so multiple processes
    writing to the same feed can't interleave a line. (In v0.11 only the
    supervisor writes, but we want the lock now so v0.13's out-of-process
    angels stay correct if they ever need to write.)

    Within a single process, an additional thread lock prevents asyncio
    schedulers from racing on the same file descriptor.

    File mode is forced to 0640 on creation: the supervisor (uid running
    Demiurge) can read+write; angels (group) can read; nothing else.
    """

    def __init__(self, creature_id: str, *, base: Optional[Path] = None):
        self._creature_id = creature_id
        self._path = feed_path_for(creature_id, base=base)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Touch with mode 0640 if missing. We don't chmod on every append —
        # umask can interfere; once-on-create is enough.
        if not self._path.exists():
            fd = os.open(str(self._path), os.O_CREAT | os.O_WRONLY, 0o640)
            os.close(fd)
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def creature_id(self) -> str:
        return self._creature_id

    def append(
        self,
        kind: str,
        data: dict[str, Any],
        *,
        correlation_id: Optional[str] = None,
    ) -> str:
        """Append one event. Returns the new event_id (str of a UUIDv7).

        Stamps ``ts`` and ``event_id`` *here*, at write time, so every angel
        reading the feed sees identical timestamps + IDs for the same event.
        """
        event = FeedEvent(
            creature_id=self._creature_id,
            event_id=str(uuid7()),
            ts=_now_iso(),
            kind=kind,
            correlation_id=correlation_id,
            data=data,
        )
        line = event.to_json_line().encode("utf-8")

        with self._lock:
            # O_APPEND → kernel guarantees position-at-end-of-file at write
            # time. flock LOCK_EX serializes across processes. Together
            # they make multi-writer-safe line-atomic appends.
            fd = os.open(str(self._path), os.O_WRONLY | os.O_APPEND)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                try:
                    os.write(fd, line)
                finally:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
        return event.event_id

    def read_all(self) -> Iterator[FeedEvent]:
        """Yield every event currently in the feed, in append order.

        Convenience for tests and for in-process angels that don't need
        streaming. Out-of-process angels (v0.13+) tail the file instead.
        """
        if not self._path.exists():
            return
        with self._path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                yield FeedEvent(
                    creature_id=obj["creature_id"],
                    event_id=obj["event_id"],
                    ts=obj["ts"],
                    kind=obj["kind"],
                    data=obj.get("data") or {},
                    correlation_id=obj.get("correlation_id"),
                    schema_version=obj.get("schema_version", SCHEMA_VERSION),
                )


# ----------------------------- helpers -----------------------------------


def parse_uuid7_timestamp(event_id: str) -> datetime:
    """Recover the embedded ms timestamp from a UUIDv7 string.

    Useful for index-free time-range scans: since UUIDv7s are sortable,
    you can binary-search a sorted feed by event_id alone.
    """
    val = UUID(event_id).int
    ms = (val >> 80) & 0xFFFFFFFFFFFF
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
