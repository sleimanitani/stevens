"""AuditAngel — projects observation-feed events into Enkidu's audit log.

v0.11 step 3e.3. The first real ``Angel`` implementation. Refactored
from the existing audit-log writer (``demiurge.audit``) so it lives
behind the standard Angel API, ready for v0.13 to promote to an
out-of-process process tailing the feed file.

Design contract:

- The audit angel reads `tool.call.start` + `tool.call.end` events from
  a Creature's observation feed.
- For each completed call (a start event correlated to an end event),
  it produces one ``AuditEntry`` and appends it to today's
  ``<audit_root>/YYYY-MM-DD.jsonl`` file via the existing ``AuditWriter``.
- Cursor: an in-process ``last_event_id`` so re-running ``observe()``
  doesn't double-project. v0.13 promotes this to a persisted cursor
  (since the angel is its own process and has to remember across
  restarts).
- Idempotent within a single observe() call: each (event_id) tuple maps
  to at most one audit line.

In v0.11 this is *additive* to the existing dispatch-side audit writer.
The dispatch path keeps writing entries via ``AuditWriter`` directly; the
audit angel is a parallel projection from the new observation feed. v0.13
flips the switch — drop the direct write, let the angel be the only
producer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from shared.creatures.base import Angel
from shared.creatures.context import AngelContext
from shared.creatures.feed import (
    KIND_TOOL_CALL_END,
    KIND_TOOL_CALL_START,
    FeedEvent,
)

from ...audit import AuditEntry, AuditWriter, hash_param


# ----------------------------- projection helper -------------------------


def feed_event_to_audit_entry(
    *,
    start: FeedEvent,
    end: FeedEvent,
    sensitive_arg_keys: Optional[set[str]] = None,
) -> AuditEntry:
    """Build one ``AuditEntry`` from a paired (start, end) feed event.

    ``end.correlation_id`` must equal ``start.event_id`` — caller
    asserts this. Args from the start event are copied into either
    ``param_values`` (non-sensitive) or ``param_hashes`` (sensitive,
    SHA-256). Sensitive-key set is configurable; defaults to a small
    list of obvious credential-shaped keys.

    Trace_id is the start event's UUIDv7 — sortable, embeds ms
    timestamp, lets future cross-system correlation tie back to the
    feed line that started this.
    """
    if end.correlation_id != start.event_id:
        raise ValueError(
            f"end.correlation_id {end.correlation_id!r} doesn't match "
            f"start.event_id {start.event_id!r}"
        )

    sensitive = set(sensitive_arg_keys or _DEFAULT_SENSITIVE_KEYS)

    # Latency: end.ts minus start.ts, in milliseconds.
    start_ts = datetime.fromisoformat(start.ts.rstrip("Z")).replace(tzinfo=timezone.utc)
    end_ts = datetime.fromisoformat(end.ts.rstrip("Z")).replace(tzinfo=timezone.utc)
    latency_ms = int((end_ts - start_ts).total_seconds() * 1000)

    capability = start.data.get("capability")
    god = start.data.get("god")

    # Args from start; result/error from end.
    args = start.data.get("args") or {}
    if not isinstance(args, dict):
        args = {"_args_repr": repr(args)}

    param_values: dict = {}
    param_hashes: dict = {}
    for key, value in args.items():
        if key in sensitive:
            param_hashes[key] = hash_param(value)
        else:
            param_values[key] = value

    end_data = end.data or {}
    error = end_data.get("error")
    outcome = "ok" if error is None else "internal"
    error_code: Optional[str] = None
    if error is not None and isinstance(error, str) and ":" in error:
        # Extract the exception type from "RuntimeError: oops"
        error_code = error.split(":", 1)[0].strip()

    extra: dict = {}
    if god is not None:
        extra["god"] = god
    if "result" in end_data and outcome == "ok":
        # Don't dump full result into audit (could be large/sensitive);
        # store a truncated repr so the audit row stays small.
        extra["result_summary"] = _truncate(end_data.get("result"))

    return AuditEntry(
        ts=start.ts,
        trace_id=start.event_id,
        outcome=outcome,
        latency_ms=latency_ms,
        caller=start.creature_id,
        capability=capability,
        account_id=args.get("account_id") if isinstance(args.get("account_id"), str) else None,
        error_code=error_code,
        param_hashes=param_hashes,
        param_values=param_values,
        extra=extra,
    )


_DEFAULT_SENSITIVE_KEYS = frozenset(
    {"password", "passphrase", "token", "api_key", "secret", "access_token", "refresh_token"}
)


def _truncate(value, *, max_len: int = 200) -> str:
    """Render an arbitrary value as a short string for the audit row."""
    s = repr(value)
    return s if len(s) <= max_len else s[:max_len] + "…"


# ----------------------------- the angel itself --------------------------


@dataclass
class _Cursor:
    last_event_id: Optional[str] = None
    seen_event_ids: set[str] = field(default_factory=set)


class AuditAngel(Angel):
    """In-process projection of a Creature's observation feed into the
    tamper-evident audit log.

    Constructed by Hephaestus during forge (or by Enkidu on attach).
    Calls to ``observe()`` are typically driven by the supervisor: when
    a Creature appends to its feed, the supervisor pings its attached
    audit angel. v0.13's out-of-process angel inverts this: the angel
    tails the file independently, no supervisor poke needed.
    """

    def __init__(
        self,
        ctx: AngelContext,
        *,
        audit_writer: AuditWriter,
        sensitive_arg_keys: Optional[set[str]] = None,
    ):
        super().__init__(ctx)
        self._writer = audit_writer
        self._sensitive_keys = set(sensitive_arg_keys) if sensitive_arg_keys else None
        self._cursor = _Cursor()
        self._pending_starts: dict[str, FeedEvent] = {}

    async def observe(self) -> int:
        """Tail the host's feed, project new tool.call.* pairs to audit log.

        Returns the count of new audit entries written.

        Behavior:
        - Reads every event in the host feed.
        - For each ``tool.call.start``, stash by event_id in the
          pending-starts map.
        - For each ``tool.call.end``, look up its correlation_id in
          pending-starts; if found and not already projected, write one
          AuditEntry and mark seen.
        - Orphans (start without end, end without start) are *not*
          flushed in this pass — they may complete on a later observe().
          v0.13's persistent cursor handles longer-running orphans.
        """
        written = 0

        for event in self._ctx.host_feed.read_all():
            if event.event_id in self._cursor.seen_event_ids:
                continue

            if event.kind == KIND_TOOL_CALL_START:
                self._pending_starts[event.event_id] = event
            elif event.kind == KIND_TOOL_CALL_END:
                start = self._pending_starts.pop(event.correlation_id or "", None)
                if start is None:
                    # Orphan end — start may have been consumed on a
                    # previous observe() pass and we dropped it. Or the
                    # start arrives later (unlikely on this single-writer
                    # feed). Skip; future pass may pick it up.
                    continue
                entry = feed_event_to_audit_entry(
                    start=start,
                    end=event,
                    sensitive_arg_keys=self._sensitive_keys,
                )
                await self._writer.log(entry)
                self._cursor.seen_event_ids.add(start.event_id)
                self._cursor.seen_event_ids.add(event.event_id)
                self._cursor.last_event_id = event.event_id
                written += 1
            else:
                # Other event kinds (think, llm.exchange, lifecycle) —
                # not the audit angel's concern. Mnemosyne's memory
                # angel projects those (v0.13).
                pass

        return written
