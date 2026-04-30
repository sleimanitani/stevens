"""Tests for the system.* capabilities (privileged-execution protocol).

All tests run with mocked subprocess — no real apt is invoked.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import pytest

from stevens_security.capabilities import system as system_caps  # noqa: F401 — registers
from stevens_security.capabilities.registry import default_registry
from stevens_security.context import CapabilityContext
from stevens_security.mechanisms.base import ExecResult, Executor
from stevens_security.system_runtime import (
    InMemoryInventory,
    InMemoryPlanStore,
    SystemRuntime,
)


class FakeAgent:
    def __init__(self, name="installer") -> None:
        self.name = name


class _FakeSubprocess:
    """Records calls + returns canned results keyed on argv prefix."""

    def __init__(self) -> None:
        self.calls: List[Executor] = []
        self.responses: List[ExecResult] = []
        self.default = ExecResult(exit_code=0, stdout=b"", stderr=b"")

    def push(self, result: ExecResult) -> None:
        self.responses.append(result)

    async def __call__(self, executor: Executor) -> ExecResult:
        self.calls.append(executor)
        if self.responses:
            return self.responses.pop(0)
        return self.default


def _make_runtime():
    sub = _FakeSubprocess()
    rt = SystemRuntime(
        plan_store=InMemoryPlanStore(),
        inventory=InMemoryInventory(),
        run_subprocess=sub,
    )
    return rt, sub


def _make_context(rt: SystemRuntime, *, clock_value=None) -> CapabilityContext:
    extra = {"system": rt}
    if clock_value is not None:
        return CapabilityContext(extra=extra, clock=lambda: clock_value)
    return CapabilityContext(extra=extra)


def _good_plan_params():
    return {
        "mechanism": "apt",
        "plan_body": {
            "operation": "install",
            "packages": ["tesseract-ocr"],
            "source": {
                "repo": "deb.debian.org/debian",
                "suite": "bookworm",
                "component": "main",
            },
            "flags": ["--no-install-recommends"],
        },
        "rollback_body": {
            "operation": "purge",
            "packages": ["tesseract-ocr"],
            "flags": [],
        },
        "rationale": "OCR fallback for read_pdf",
    }


# --- read_environment ---


@pytest.mark.asyncio
async def test_read_environment_dpkg_status():
    rt, sub = _make_runtime()
    sub.push(ExecResult(0, b"install ok installed|5.3.0-2", b""))
    spec = default_registry.get("system.read_environment")
    out = await spec.invoke(
        FakeAgent(),
        {"fields": [{"name": "dpkg_status", "package": "tesseract-ocr"}]},
        _make_context(rt),
    )
    assert out["dpkg_status"]["tesseract-ocr"]["installed"] is True
    assert out["dpkg_status"]["tesseract-ocr"]["version"] == "5.3.0-2"


@pytest.mark.asyncio
async def test_read_environment_dpkg_not_installed():
    rt, sub = _make_runtime()
    sub.push(ExecResult(1, b"", b"package not found"))
    spec = default_registry.get("system.read_environment")
    out = await spec.invoke(
        FakeAgent(),
        {"fields": [{"name": "dpkg_status", "package": "tesseract-ocr"}]},
        _make_context(rt),
    )
    assert out["dpkg_status"]["tesseract-ocr"]["installed"] is False


@pytest.mark.asyncio
async def test_read_environment_unknown_field():
    rt, sub = _make_runtime()
    spec = default_registry.get("system.read_environment")
    with pytest.raises(RuntimeError, match="unknown read_environment field"):
        await spec.invoke(
            FakeAgent(), {"fields": [{"name": "magic"}]}, _make_context(rt),
        )


# --- plan_install ---


@pytest.mark.asyncio
async def test_plan_install_validates_and_stores():
    rt, _ = _make_runtime()
    spec = default_registry.get("system.plan_install")
    out = await spec.invoke(FakeAgent(), _good_plan_params(), _make_context(rt))
    assert out["validated"] is True
    plan_id = out["plan_id"]
    stored = await rt.plan_store.get(plan_id)
    assert stored is not None
    assert stored.mechanism == "apt"


@pytest.mark.asyncio
async def test_plan_install_rejects_invalid():
    rt, _ = _make_runtime()
    bad = _good_plan_params()
    bad["plan_body"]["packages"] = ["EvilName!!!"]
    spec = default_registry.get("system.plan_install")
    out = await spec.invoke(FakeAgent(), bad, _make_context(rt))
    assert out["validated"] is False
    assert out["errors"]


# --- execute_privileged ---


@pytest.mark.asyncio
async def test_execute_privileged_happy_path():
    rt, sub = _make_runtime()
    spec = default_registry.get("system.plan_install")
    plan_resp = await spec.invoke(FakeAgent(), _good_plan_params(), _make_context(rt))
    plan_id = plan_resp["plan_id"]

    # Subprocess: install (exit 0), then probe (dpkg-query) reports installed.
    sub.push(ExecResult(0, b"", b""))  # apt-get install
    sub.push(ExecResult(0, b"tesseract-ocr install ok installed\n", b""))  # dpkg-query

    exec_spec = default_registry.get("system.execute_privileged")
    out = await exec_spec.invoke(
        FakeAgent(), {"plan_id": plan_id, "rationale": "needed"}, _make_context(rt),
    )
    assert out["outcome"] == "ok"
    assert out["health_check_result"] == "passed"
    inv = await rt.inventory.list_for("installer")
    assert len(inv) == 1
    assert "tesseract-ocr" in inv[0].name


@pytest.mark.asyncio
async def test_execute_privileged_health_fail_triggers_rollback():
    rt, sub = _make_runtime()
    plan_resp = await default_registry.get("system.plan_install").invoke(
        FakeAgent(), _good_plan_params(), _make_context(rt),
    )
    plan_id = plan_resp["plan_id"]
    # apt install exits 0 but dpkg-query says not installed.
    sub.push(ExecResult(0, b"", b""))
    sub.push(ExecResult(1, b"", b"no such package"))
    # Rollback subprocess invocation.
    sub.push(ExecResult(0, b"", b""))

    out = await default_registry.get("system.execute_privileged").invoke(
        FakeAgent(), {"plan_id": plan_id, "rationale": "x"}, _make_context(rt),
    )
    assert out["outcome"] == "health_failed"
    # Inventory should NOT have the new install row.
    inv = await rt.inventory.list_for("installer")
    assert inv == []


@pytest.mark.asyncio
async def test_execute_privileged_unknown_plan():
    rt, _ = _make_runtime()
    out = await default_registry.get("system.execute_privileged").invoke(
        FakeAgent(),
        {"plan_id": "00000000-0000-0000-0000-000000000000"},
        _make_context(rt),
    )
    assert out["outcome"] == "failed"
    assert "not found" in out["error"]


@pytest.mark.asyncio
async def test_execute_privileged_expired_plan():
    rt, sub = _make_runtime()
    fixed_now = datetime(2026, 4, 30, 12, 0, 0, tzinfo=timezone.utc)
    spec_plan = default_registry.get("system.plan_install")
    plan_resp = await spec_plan.invoke(
        FakeAgent(), _good_plan_params(), _make_context(rt, clock_value=fixed_now),
    )
    plan_id = plan_resp["plan_id"]
    # Now invoke execute past the plan's TTL.
    later = fixed_now + timedelta(hours=1)
    out = await default_registry.get("system.execute_privileged").invoke(
        FakeAgent(),
        {"plan_id": plan_id},
        _make_context(rt, clock_value=later),
    )
    assert out["outcome"] == "failed"
    assert "expired" in out["error"]


@pytest.mark.asyncio
async def test_execute_privileged_other_agents_plan_rejected():
    rt, sub = _make_runtime()
    plan_resp = await default_registry.get("system.plan_install").invoke(
        FakeAgent("agent_a"), _good_plan_params(), _make_context(rt),
    )
    out = await default_registry.get("system.execute_privileged").invoke(
        FakeAgent("agent_b"),
        {"plan_id": plan_resp["plan_id"]},
        _make_context(rt),
    )
    assert out["outcome"] == "rejected"


# --- write_inventory ---


@pytest.mark.asyncio
async def test_write_inventory_caller_bound():
    rt, _ = _make_runtime()
    out = await default_registry.get("system.write_inventory").invoke(
        FakeAgent("agent_a"),
        {"name": "ffmpeg", "mechanism": "apt"},
        _make_context(rt),
    )
    assert "inventory_id" in out
    rows_a = await rt.inventory.list_for("agent_a")
    rows_b = await rt.inventory.list_for("agent_b")
    assert len(rows_a) == 1 and rows_a[0].name == "ffmpeg"
    assert rows_b == []
