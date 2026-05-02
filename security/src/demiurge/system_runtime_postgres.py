"""Postgres-backed PlanStore + Inventory for the privileged-execution path.

Implements the same Protocols as the in-memory variants in
``system_runtime.py``. Used by Enkidu in production; tests use the
in-memory ones.

Schema lives in migrations 007 (install_plans) and 008 (environment_packages).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from psycopg.types.json import Jsonb

from shared.db import connection

from .system_runtime import InventoryRow, StoredPlan


def _row_to_plan(row: tuple) -> StoredPlan:
    (
        id_, proposing_agent, mechanism, plan_body, rollback_body,
        rationale, proposed_at, expires_at, executed_at,
        execution_outcome, inventory_id,
    ) = row
    return StoredPlan(
        id=str(id_),
        proposing_agent=proposing_agent,
        mechanism=mechanism,
        plan_body=plan_body if isinstance(plan_body, dict) else json.loads(plan_body),
        rollback_body=rollback_body if isinstance(rollback_body, dict) else json.loads(rollback_body),
        rationale=rationale,
        proposed_at=proposed_at,
        expires_at=expires_at,
        executed_at=executed_at,
        execution_outcome=execution_outcome,
        inventory_id=str(inventory_id) if inventory_id else None,
    )


def _row_to_inventory(row: tuple) -> InventoryRow:
    (
        id_, caller, name, version, mechanism, location, sha256,
        plan_id, installed_at, removed_at, health_status,
    ) = row
    return InventoryRow(
        id=str(id_),
        caller=caller,
        name=name,
        version=version,
        mechanism=mechanism,
        location=location,
        sha256=sha256,
        plan_id=str(plan_id) if plan_id else "external",
        installed_at=installed_at,
        removed_at=removed_at,
        health_status=health_status,
    )


_PLAN_COLS = (
    "id, proposing_agent, mechanism, plan_body, rollback_body, "
    "rationale, proposed_at, expires_at, executed_at, "
    "execution_outcome, inventory_id"
)

_INVENTORY_COLS = (
    "id, caller, name, version, mechanism, location, sha256, "
    "plan_id, installed_at, removed_at, health_status"
)


class PostgresPlanStore:
    """``PlanStore`` Protocol against ``install_plans`` table."""

    async def insert(self, plan: StoredPlan) -> str:
        async with connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO install_plans "
                    "(id, proposing_agent, mechanism, plan_body, rollback_body, "
                    " rationale, proposed_at, expires_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        plan.id, plan.proposing_agent, plan.mechanism,
                        Jsonb(plan.plan_body), Jsonb(plan.rollback_body),
                        plan.rationale, plan.proposed_at, plan.expires_at,
                    ),
                )
            await conn.commit()
        return plan.id

    async def get(self, plan_id: str) -> Optional[StoredPlan]:
        async with connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"SELECT {_PLAN_COLS} FROM install_plans WHERE id = %s",
                    (plan_id,),
                )
                row = await cur.fetchone()
        return _row_to_plan(row) if row else None

    async def mark_executed(
        self, plan_id: str, outcome: str, inventory_id: Optional[str],
    ) -> None:
        async with connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE install_plans "
                    "SET executed_at = now(), execution_outcome = %s, inventory_id = %s "
                    "WHERE id = %s",
                    (outcome, inventory_id, plan_id),
                )
                if cur.rowcount == 0:
                    raise KeyError(plan_id)
            await conn.commit()


class PostgresInventory:
    """``Inventory`` Protocol against ``environment_packages`` table.

    ``list_for(caller)`` is the agent-scoped read; ``list_global()`` is the
    operator-scoped read used by ``demiurge dep list``.
    """

    async def append(self, row: InventoryRow) -> str:
        plan_id_value = row.plan_id if row.plan_id != "external" else None
        async with connection() as conn:
            async with conn.cursor() as cur:
                # ``plan_id`` is NOT NULL in the migration, so for external
                # rows we generate a placeholder UUID. Long-term: relax the
                # NOT NULL or split into two tables. For v0.3.2 we synthesize.
                if plan_id_value is None:
                    import uuid as _uuid
                    plan_id_value = str(_uuid.UUID(int=0))
                await cur.execute(
                    "INSERT INTO environment_packages "
                    "(id, caller, name, version, mechanism, location, sha256, "
                    " plan_id, installed_at, health_status) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        row.id, row.caller, row.name, row.version, row.mechanism,
                        row.location, row.sha256, plan_id_value,
                        row.installed_at, row.health_status,
                    ),
                )
            await conn.commit()
        return row.id

    async def list_for(
        self, caller: str, name: Optional[str] = None,
    ) -> List[InventoryRow]:
        sql = (
            f"SELECT {_INVENTORY_COLS} FROM environment_packages "
            "WHERE caller = %s AND removed_at IS NULL"
        )
        params: list = [caller]
        if name is not None:
            sql += " AND name = %s"
            params.append(name)
        sql += " ORDER BY installed_at DESC"
        async with connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, tuple(params))
                rows = await cur.fetchall()
        return [_row_to_inventory(r) for r in rows]

    async def list_global(
        self, name: Optional[str] = None,
    ) -> List[InventoryRow]:
        sql = (
            f"SELECT {_INVENTORY_COLS} FROM environment_packages "
            "WHERE removed_at IS NULL"
        )
        params: list = []
        if name is not None:
            sql += " AND name = %s"
            params.append(name)
        sql += " ORDER BY installed_at DESC"
        async with connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, tuple(params))
                rows = await cur.fetchall()
        return [_row_to_inventory(r) for r in rows]

    async def mark_removed(self, row_id: str) -> None:
        async with connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE environment_packages "
                    "SET removed_at = now(), health_status = 'rolled_back' "
                    "WHERE id = %s AND removed_at IS NULL",
                    (row_id,),
                )
                if cur.rowcount == 0:
                    raise KeyError(row_id)
            await conn.commit()
