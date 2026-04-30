"""Approval store — abstraction over the persistence layer.

In production this is Postgres-backed (queries against ``approval_requests``
and ``standing_approvals``). In tests it's the in-memory backend
``InMemoryApprovalStore`` so the CLI logic can be exercised without a DB.

The ``stevens approval`` CLI subcommands take an ``ApprovalStore`` instance,
which is constructed at boot (Postgres) or in tests (in-memory).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Protocol

from .matcher import StandingApproval
from .queue import ApprovalRequest


class StoreError(Exception):
    """Raised on store-state errors (decide on missing id, double-grant, etc.)."""


@dataclass(frozen=True)
class StandingGrant:
    capability: str
    caller: str
    predicates: Dict[str, Any] = field(default_factory=dict)
    expires_at: Optional[datetime] = None
    expires_session: Optional[str] = None
    rationale: Optional[str] = None


class ApprovalStore(Protocol):
    # Per-call queue.
    async def list_pending(self) -> List[ApprovalRequest]: ...
    async def get_request(self, request_id: str) -> Optional[ApprovalRequest]: ...
    async def decide_request(
        self, *, request_id: str, status: str,
        decided_by: str, notes: Optional[str] = None,
        promoted_standing_id: Optional[str] = None,
    ) -> ApprovalRequest: ...

    # Standing approvals.
    async def grant_standing(self, *, granted_by: str, grant: StandingGrant) -> StandingApproval: ...
    async def list_standing(self, *, include_revoked: bool = False) -> List[StandingApproval]: ...
    async def revoke_standing(self, *, standing_id: str, revoked_by: str) -> StandingApproval: ...


class InMemoryApprovalStore:
    """Test-only store. Holds both pending requests and standing approvals."""

    def __init__(self, *, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self._requests: Dict[str, ApprovalRequest] = {}
        self._standing: Dict[str, StandingApproval] = {}
        self._clock = clock

    # --- requests ---

    async def enqueue_request(self, *, request: ApprovalRequest) -> str:
        if request.id in self._requests:
            raise StoreError(f"duplicate request id: {request.id}")
        self._requests[request.id] = request
        return request.id

    async def list_pending(self) -> List[ApprovalRequest]:
        return [r for r in self._requests.values() if r.status == "pending"]

    async def get_request(self, request_id: str) -> Optional[ApprovalRequest]:
        return self._requests.get(request_id)

    async def decide_request(
        self,
        *,
        request_id: str,
        status: str,
        decided_by: str,
        notes: Optional[str] = None,
        promoted_standing_id: Optional[str] = None,
    ) -> ApprovalRequest:
        if status not in ("approved", "rejected", "expired", "failed"):
            raise StoreError(f"invalid status: {status!r}")
        row = self._requests.get(request_id)
        if row is None:
            raise StoreError(f"unknown request id: {request_id!r}")
        if row.status != "pending":
            raise StoreError(f"request {request_id!r} already decided ({row.status})")
        row.status = status
        row.decided_at = self._clock()
        row.decided_by = decided_by
        row.decision_notes = notes
        row.promoted_standing_id = promoted_standing_id
        return row

    # --- standing ---

    async def grant_standing(
        self, *, granted_by: str, grant: StandingGrant,
    ) -> StandingApproval:
        sa = StandingApproval(
            id=str(uuid.uuid4()),
            capability=grant.capability,
            caller=grant.caller,
            predicates=dict(grant.predicates),
            expires_at=grant.expires_at,
            expires_session=grant.expires_session,
            granted_at=self._clock(),
            granted_by=granted_by,
            rationale=grant.rationale,
        )
        self._standing[sa.id] = sa
        return sa

    async def list_standing(self, *, include_revoked: bool = False) -> List[StandingApproval]:
        if include_revoked:
            return list(self._standing.values())
        return [sa for sa in self._standing.values() if sa.revoked_at is None]

    async def revoke_standing(
        self, *, standing_id: str, revoked_by: str,
    ) -> StandingApproval:
        sa = self._standing.get(standing_id)
        if sa is None:
            raise StoreError(f"unknown standing approval: {standing_id!r}")
        if sa.revoked_at is not None:
            raise StoreError(f"already revoked: {standing_id!r}")
        # StandingApproval is frozen — replace with a new one.
        revoked = StandingApproval(
            id=sa.id,
            capability=sa.capability,
            caller=sa.caller,
            predicates=sa.predicates,
            expires_at=sa.expires_at,
            expires_session=sa.expires_session,
            granted_at=sa.granted_at,
            granted_by=sa.granted_by,
            rationale=sa.rationale,
            revoked_at=self._clock(),
        )
        self._standing[sa.id] = revoked
        return revoked


def parse_duration(s: str) -> Optional[timedelta]:
    """Parse a duration like ``30d`` / ``4h`` / ``forever`` / ``session``.

    Returns None for ``forever`` and ``session`` (caller handles those modes
    separately by setting expires_session instead of expires_at).
    """
    if s in ("forever", "session"):
        return None
    if s.endswith("d"):
        return timedelta(days=int(s[:-1]))
    if s.endswith("h"):
        return timedelta(hours=int(s[:-1]))
    if s.endswith("m"):
        return timedelta(minutes=int(s[:-1]))
    raise ValueError(f"unrecognized duration: {s!r}")
