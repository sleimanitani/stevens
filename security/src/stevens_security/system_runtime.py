"""System-level runtime — plan store, inventory, subprocess runner.

The ``system.*`` capabilities depend on three things that are mocked in
tests and Postgres-backed in production:

- A ``PlanStore`` for ``install_plans`` rows.
- An ``Inventory`` for ``environment_packages`` rows (per-agent caller-scoped).
- A ``SubprocessRunner`` that takes an ``Executor`` and returns an
  ``ExecResult``.

The capability handlers reach all three via ``context.extra["system"]``,
which is a ``SystemRuntime`` instance. Tests construct in-memory variants;
production wires up the Postgres + subprocess implementations at startup.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Protocol

from .mechanisms.base import ExecResult, Executor


# --- plan store ---


@dataclass
class StoredPlan:
    id: str
    proposing_agent: str
    mechanism: str
    plan_body: Dict[str, Any]
    rollback_body: Dict[str, Any]
    rationale: Optional[str]
    proposed_at: datetime
    expires_at: datetime
    executed_at: Optional[datetime] = None
    execution_outcome: Optional[str] = None
    inventory_id: Optional[str] = None


class PlanStore(Protocol):
    async def insert(self, plan: StoredPlan) -> str: ...
    async def get(self, plan_id: str) -> Optional[StoredPlan]: ...
    async def mark_executed(self, plan_id: str, outcome: str, inventory_id: Optional[str]) -> None: ...


class InMemoryPlanStore:
    def __init__(self) -> None:
        self._rows: Dict[str, StoredPlan] = {}

    async def insert(self, plan: StoredPlan) -> str:
        self._rows[plan.id] = plan
        return plan.id

    async def get(self, plan_id: str) -> Optional[StoredPlan]:
        return self._rows.get(plan_id)

    async def mark_executed(self, plan_id: str, outcome: str, inventory_id: Optional[str]) -> None:
        if plan_id not in self._rows:
            raise KeyError(plan_id)
        plan = self._rows[plan_id]
        self._rows[plan_id] = StoredPlan(
            **{**plan.__dict__,
               "executed_at": datetime.now(timezone.utc),
               "execution_outcome": outcome,
               "inventory_id": inventory_id},
        )


# --- inventory ---


@dataclass
class InventoryRow:
    id: str
    caller: str
    name: str
    mechanism: str
    plan_id: str
    version: Optional[str] = None
    location: Optional[str] = None
    sha256: Optional[str] = None
    installed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    removed_at: Optional[datetime] = None
    health_status: str = "unknown"


class Inventory(Protocol):
    async def append(self, row: InventoryRow) -> str: ...
    async def list_for(self, caller: str, name: Optional[str] = None) -> List[InventoryRow]: ...
    async def list_global(self, name: Optional[str] = None) -> List[InventoryRow]: ...
    async def mark_removed(self, row_id: str) -> None: ...


class InMemoryInventory:
    def __init__(self) -> None:
        self._rows: Dict[str, InventoryRow] = {}

    async def append(self, row: InventoryRow) -> str:
        self._rows[row.id] = row
        return row.id

    async def list_for(self, caller: str, name: Optional[str] = None) -> List[InventoryRow]:
        return [
            r for r in self._rows.values()
            if r.caller == caller
            and r.removed_at is None
            and (name is None or r.name == name)
        ]

    async def list_global(self, name: Optional[str] = None) -> List[InventoryRow]:
        return [
            r for r in self._rows.values()
            if r.removed_at is None
            and (name is None or r.name == name)
        ]

    async def mark_removed(self, row_id: str) -> None:
        if row_id not in self._rows:
            raise KeyError(row_id)
        r = self._rows[row_id]
        r.removed_at = datetime.now(timezone.utc)
        r.health_status = "rolled_back"


# --- subprocess runner ---


SubprocessRunner = Callable[[Executor], Awaitable[ExecResult]]


async def real_subprocess_runner(executor: Executor) -> ExecResult:
    """Production runner — actually invokes subprocess. Run by Enkidu only."""
    loop = asyncio.get_running_loop()

    def _run() -> ExecResult:
        try:
            proc = subprocess.run(
                executor.argv,
                env=executor.env,
                shell=False,
                capture_output=True,
                timeout=executor.timeout_seconds,
            )
            return ExecResult(
                exit_code=proc.returncode,
                stdout=proc.stdout[:1024 * 1024],   # 1 MiB cap
                stderr=proc.stderr[:1024 * 1024],
            )
        except subprocess.TimeoutExpired as e:
            return ExecResult(
                exit_code=-1,
                stdout=(e.stdout or b"")[:1024 * 1024],
                stderr=(e.stderr or b"")[:1024 * 1024],
                timed_out=True,
            )

    return await loop.run_in_executor(None, _run)


# --- bundled runtime ---


@dataclass(frozen=True)
class SystemRuntime:
    plan_store: PlanStore
    inventory: Inventory
    run_subprocess: SubprocessRunner
    plan_ttl_seconds: int = 30 * 60     # 30 minutes per docs/protocols/privileged-execution.md


def make_default_runtime(*, run_subprocess: SubprocessRunner = real_subprocess_runner) -> SystemRuntime:
    return SystemRuntime(
        plan_store=InMemoryPlanStore(),
        inventory=InMemoryInventory(),
        run_subprocess=run_subprocess,
    )


def make_plan_id() -> str:
    return str(uuid.uuid4())


def make_inventory_id() -> str:
    return str(uuid.uuid4())
