"""Per-call approval queue.

When a `requires_approval: true` capability call has no covering standing
approval, Enkidu writes a row here, returns BLOCKED, and waits for Sol's
decision via the `demiurge approval` CLI.

The queue is backed by Postgres (`approval_requests` table) but for unit
tests we provide an in-memory implementation conforming to the same
``ApprovalQueue`` Protocol. Real Enkidu uses the Postgres queue; tests use
the in-memory one.

The dispatcher does NOT directly write to the DB; it calls
``ApprovalQueue.enqueue(...)`` and treats the returned id as opaque.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Protocol


class QueueError(Exception):
    """Raised on queue-state errors (decide on missing id, double-decide, etc.)."""


@dataclass
class ApprovalRequest:
    id: str
    capability: str
    caller: str
    params_summary: str
    full_envelope: Dict[str, Any]   # the original signed envelope, replayable
    rationale: Optional[str] = None
    blocked_trace_id: Optional[str] = None
    status: str = "pending"          # pending | approved | rejected | expired | failed
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    decided_at: Optional[datetime] = None
    decided_by: Optional[str] = None
    decision_notes: Optional[str] = None
    promoted_standing_id: Optional[str] = None


@dataclass(frozen=True)
class DecisionResult:
    request: ApprovalRequest
    """The full request record after the decision was applied."""


class ApprovalQueue(Protocol):
    async def enqueue(self, *, request: ApprovalRequest) -> str: ...
    async def get(self, request_id: str) -> Optional[ApprovalRequest]: ...
    async def list_pending(self) -> List[ApprovalRequest]: ...
    async def decide(
        self, *, request_id: str, status: str,
        decided_by: str, decision_notes: Optional[str] = None,
        promoted_standing_id: Optional[str] = None,
    ) -> DecisionResult: ...


class InMemoryApprovalQueue:
    """Test-only queue. Real Enkidu uses the Postgres-backed implementation."""

    def __init__(self, *, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self._rows: Dict[str, ApprovalRequest] = {}
        self._clock = clock

    async def enqueue(self, *, request: ApprovalRequest) -> str:
        if request.id in self._rows:
            raise QueueError(f"duplicate request id: {request.id}")
        self._rows[request.id] = request
        return request.id

    async def get(self, request_id: str) -> Optional[ApprovalRequest]:
        return self._rows.get(request_id)

    async def list_pending(self) -> List[ApprovalRequest]:
        return [r for r in self._rows.values() if r.status == "pending"]

    async def decide(
        self,
        *,
        request_id: str,
        status: str,
        decided_by: str,
        decision_notes: Optional[str] = None,
        promoted_standing_id: Optional[str] = None,
    ) -> DecisionResult:
        if status not in ("approved", "rejected", "expired", "failed"):
            raise QueueError(f"invalid decision status: {status!r}")
        row = self._rows.get(request_id)
        if row is None:
            raise QueueError(f"unknown request id: {request_id!r}")
        if row.status != "pending":
            raise QueueError(f"request {request_id!r} already decided ({row.status})")
        row.status = status
        row.decided_at = self._clock()
        row.decided_by = decided_by
        row.decision_notes = decision_notes
        row.promoted_standing_id = promoted_standing_id
        return DecisionResult(request=row)


def make_request_id() -> str:
    return str(uuid.uuid4())
