"""Postgres-backed ApprovalStore.

Implements the same Protocol as ``InMemoryApprovalStore`` (see ``store.py``).
Used by Enkidu in production; tests still use the in-memory variant.

Schema lives in migrations 005 (standing_approvals) and 006 (approval_requests).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from psycopg.types.json import Jsonb

from shared.db import connection

from .matcher import StandingApproval
from .queue import ApprovalRequest
from .store import StandingGrant, StoreError


def _row_to_approval_request(row: tuple) -> ApprovalRequest:
    (
        id_, capability, caller, params_summary, full_envelope,
        rationale, status, decided_at, decided_by, decision_notes,
        promoted_standing_id, blocked_trace_id, created_at,
    ) = row
    return ApprovalRequest(
        id=str(id_),
        capability=capability,
        caller=caller,
        params_summary=params_summary,
        full_envelope=full_envelope if isinstance(full_envelope, dict) else json.loads(full_envelope),
        rationale=rationale,
        status=status,
        created_at=created_at,
        decided_at=decided_at,
        decided_by=decided_by,
        decision_notes=decision_notes,
        promoted_standing_id=str(promoted_standing_id) if promoted_standing_id else None,
        blocked_trace_id=str(blocked_trace_id) if blocked_trace_id else None,
    )


def _row_to_standing(row: tuple) -> StandingApproval:
    (
        id_, capability, caller, predicates, expires_at, expires_session,
        granted_at, granted_by, rationale, revoked_at, _revoked_by, _promoted_from,
    ) = row
    if isinstance(predicates, str):
        predicates = json.loads(predicates)
    return StandingApproval(
        id=str(id_),
        capability=capability,
        caller=caller,
        predicates=predicates or {},
        expires_at=expires_at,
        expires_session=expires_session,
        granted_at=granted_at,
        granted_by=granted_by,
        rationale=rationale,
        revoked_at=revoked_at,
    )


_REQUEST_COLS = (
    "proposal_id, capability, caller, params_summary, full_envelope, "
    "rationale, status, decided_at, decided_by, decision_notes, "
    "promoted_standing_id, blocked_trace_id, created_at"
)

# approval_requests' PK column is `id`, not `proposal_id` (that was the
# skill_proposals migration). Use `id` here.
_REQUEST_COLS = (
    "id, capability, caller, params_summary, full_envelope, "
    "rationale, status, decided_at, decided_by, decision_notes, "
    "promoted_standing_id, blocked_trace_id, created_at"
)

_STANDING_COLS = (
    "id, capability, caller, predicates, expires_at, expires_session, "
    "granted_at, granted_by, rationale, revoked_at, revoked_by, "
    "promoted_from_request"
)


class PostgresApprovalStore:
    """Postgres-backed implementation of the ``ApprovalStore`` Protocol.

    Async — wraps ``shared.db.connection()``. Caller-side concurrency is
    fine; per-request connection isolation is what psycopg's pool gives us.
    """

    # --- requests ---

    async def enqueue_request(self, *, request: ApprovalRequest) -> str:
        async with connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO approval_requests "
                    "(id, capability, caller, params_summary, full_envelope, "
                    " rationale, status, blocked_trace_id, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        request.id,
                        request.capability,
                        request.caller,
                        request.params_summary,
                        Jsonb(request.full_envelope),
                        request.rationale,
                        request.status,
                        request.blocked_trace_id,
                        request.created_at,
                    ),
                )
            await conn.commit()
        return request.id

    async def list_pending(self) -> List[ApprovalRequest]:
        async with connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"SELECT {_REQUEST_COLS} FROM approval_requests "
                    "WHERE status = 'pending' ORDER BY created_at ASC"
                )
                rows = await cur.fetchall()
        return [_row_to_approval_request(r) for r in rows]

    async def get_request(self, request_id: str) -> Optional[ApprovalRequest]:
        async with connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"SELECT {_REQUEST_COLS} FROM approval_requests WHERE id = %s",
                    (request_id,),
                )
                row = await cur.fetchone()
        return _row_to_approval_request(row) if row else None

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
        async with connection() as conn:
            async with conn.cursor() as cur:
                # Optimistic concurrency: only update if pending. If the row
                # is already decided, the row count is 0 and we raise.
                await cur.execute(
                    "UPDATE approval_requests SET status = %s, decided_at = now(), "
                    "decided_by = %s, decision_notes = %s, promoted_standing_id = %s "
                    "WHERE id = %s AND status = 'pending'",
                    (status, decided_by, notes, promoted_standing_id, request_id),
                )
                if cur.rowcount == 0:
                    # Diagnose: missing or already-decided?
                    await cur.execute(
                        "SELECT status FROM approval_requests WHERE id = %s",
                        (request_id,),
                    )
                    existing = await cur.fetchone()
                    if existing is None:
                        raise StoreError(f"unknown request id: {request_id!r}")
                    raise StoreError(
                        f"request {request_id!r} already decided ({existing[0]})"
                    )
            await conn.commit()
        # Fetch the updated row for return.
        result = await self.get_request(request_id)
        assert result is not None
        return result

    # --- standing ---

    async def grant_standing(
        self,
        *,
        granted_by: str,
        grant: StandingGrant,
    ) -> StandingApproval:
        new_id = str(uuid.uuid4())
        async with connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO standing_approvals "
                    "(id, capability, caller, predicates, expires_at, expires_session, "
                    " granted_at, granted_by, rationale) "
                    "VALUES (%s, %s, %s, %s, %s, %s, now(), %s, %s) "
                    "RETURNING granted_at",
                    (
                        new_id,
                        grant.capability,
                        grant.caller,
                        Jsonb(grant.predicates or {}),
                        grant.expires_at,
                        grant.expires_session,
                        granted_by,
                        grant.rationale,
                    ),
                )
                row = await cur.fetchone()
            await conn.commit()
        return StandingApproval(
            id=new_id,
            capability=grant.capability,
            caller=grant.caller,
            predicates=dict(grant.predicates or {}),
            expires_at=grant.expires_at,
            expires_session=grant.expires_session,
            granted_at=row[0] if row else datetime.now(timezone.utc),
            granted_by=granted_by,
            rationale=grant.rationale,
        )

    async def list_standing(
        self, *, include_revoked: bool = False,
    ) -> List[StandingApproval]:
        sql = f"SELECT {_STANDING_COLS} FROM standing_approvals"
        if not include_revoked:
            sql += " WHERE revoked_at IS NULL"
        sql += " ORDER BY granted_at ASC"
        async with connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql)
                rows = await cur.fetchall()
        return [_row_to_standing(r) for r in rows]

    async def revoke_standing(
        self, *, standing_id: str, revoked_by: str,
    ) -> StandingApproval:
        async with connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE standing_approvals "
                    "SET revoked_at = now(), revoked_by = %s "
                    "WHERE id = %s AND revoked_at IS NULL",
                    (revoked_by, standing_id),
                )
                if cur.rowcount == 0:
                    await cur.execute(
                        "SELECT revoked_at FROM standing_approvals WHERE id = %s",
                        (standing_id,),
                    )
                    existing = await cur.fetchone()
                    if existing is None:
                        raise StoreError(f"unknown standing approval: {standing_id!r}")
                    raise StoreError(f"already revoked: {standing_id!r}")
                await cur.execute(
                    f"SELECT {_STANDING_COLS} FROM standing_approvals WHERE id = %s",
                    (standing_id,),
                )
                row = await cur.fetchone()
            await conn.commit()
        return _row_to_standing(row)
