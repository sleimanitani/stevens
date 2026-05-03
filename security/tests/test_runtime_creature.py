"""Tests for creature-runtime integration — v0.11 step 7.3."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from demiurge.audit import AuditWriter
from demiurge.runtime import (
    CreatureRuntime,
    CreatureRuntimeError,
    CreatureRuntimeRegistration,
    Supervisor,
)
from shared.creatures.feed import (
    KIND_TOOL_CALL_END,
    KIND_TOOL_CALL_START,
    ObservationFeed,
)


# ----------------------------- fixtures ----------------------------------


@pytest.fixture
def workspace(tmp_path: Path) -> dict[str, Path]:
    ws = {
        "agents_yaml": tmp_path / "agents.yaml",
        "feed_base": tmp_path / "feeds",
        "audit_root": tmp_path / "audit",
        "log_dir": tmp_path / "logs",
        "repo_root": tmp_path / "repo",
    }
    ws["audit_root"].mkdir(parents=True)
    ws["repo_root"].mkdir(parents=True)
    return ws


def _runtime(ws: dict[str, Path], **kw) -> CreatureRuntime:
    return CreatureRuntime(
        supervisor=Supervisor(),
        audit_writer=AuditWriter(ws["audit_root"]),
        repo_root=ws["repo_root"],
        log_dir=ws["log_dir"],
        feed_base=ws["feed_base"],
        **kw,
    )


def _write_agents_yaml(path: Path, names: list[str]) -> None:
    path.write_text(
        yaml.safe_dump(
            {"agents": [{"name": n, "pubkey_b64": "stub"} for n in names]}
        )
    )


# ----------------------------- registration ------------------------------


def test_add_creature_registers_supervised_process(workspace):
    rt = _runtime(workspace)
    reg = rt.add_creature(creature_id="email_pm.personal", kind="mortal")
    assert reg.kind == "mortal"
    assert reg.process_name == "demiurge-creature-email_pm.personal"
    assert reg.angel_task_id == "angel:email_pm.personal"
    proc = rt.supervisor.get(reg.process_name)
    assert proc.cmd[0] == "python"
    assert "email_pm.personal" in proc.cmd[2]


def test_add_creature_with_cmd_override(workspace):
    rt = _runtime(workspace)
    rt.add_creature(
        creature_id="custom.thing",
        cmd_override=["sleep", "10"],
    )
    proc = rt.supervisor.get("demiurge-creature-custom.thing")
    assert proc.cmd == ["sleep", "10"]


def test_add_creature_with_env(workspace):
    rt = _runtime(workspace)
    rt.add_creature(
        creature_id="env.test",
        cmd_override=["sleep", "10"],
        env={"DEMIURGE_CALLER_NAME": "env.test"},
    )
    proc = rt.supervisor.get("demiurge-creature-env.test")
    assert proc.env == {"DEMIURGE_CALLER_NAME": "env.test"}


def test_add_creature_creates_angel(workspace):
    rt = _runtime(workspace)
    rt.add_creature(creature_id="email_pm.personal")
    assert "email_pm.personal" in rt._angels
    angel = rt._angels["email_pm.personal"]
    assert angel.context.god == "enkidu"
    assert angel.context.angel_name == "audit"
    assert angel.context.host_creature_id == "email_pm.personal"


# ----------------------------- discover_and_add_all ----------------------


def test_discover_and_add_all_picks_creature_shaped_names(workspace):
    rt = _runtime(workspace)
    _write_agents_yaml(
        workspace["agents_yaml"],
        ["enkidu", "email_pm.personal", "trip_planner.tokyo_2026", "operator"],
    )
    regs, errs = rt.discover_and_add_all(workspace["agents_yaml"])
    assert errs == []
    assert {r.creature_id for r in regs} == {
        "email_pm.personal",
        "trip_planner.tokyo_2026",
    }


def test_discover_and_add_all_handles_missing_yaml(workspace):
    rt = _runtime(workspace)
    regs, errs = rt.discover_and_add_all(
        workspace["agents_yaml"]  # doesn't exist
    )
    assert regs == []
    assert errs == []


def test_discover_and_add_all_empty_yaml(workspace):
    rt = _runtime(workspace)
    workspace["agents_yaml"].write_text(yaml.safe_dump({"agents": []}))
    regs, errs = rt.discover_and_add_all(workspace["agents_yaml"])
    assert regs == []
    assert errs == []


# ----------------------------- audit-angel observation -------------------


def test_start_angels_observes_feed_events(workspace):
    """End-to-end: register a Creature, write events to its feed,
    start angels, verify audit log gets the projected entries."""
    creature_id = "email_pm.personal"
    feed = ObservationFeed(creature_id, base=workspace["feed_base"])

    rt = _runtime(workspace, audit_observe_interval=0.05)
    rt.add_creature(creature_id=creature_id, cmd_override=["sleep", "60"])

    # Write a complete tool.call pair to the feed BEFORE angels start.
    start_id = feed.append(
        KIND_TOOL_CALL_START,
        {"capability": "gmail.send", "god": "enkidu", "args": {"to": "x"}},
    )
    feed.append(
        KIND_TOOL_CALL_END,
        {"capability": "gmail.send", "god": "enkidu", "result": {"ok": True}},
        correlation_id=start_id,
    )

    async def run():
        await rt.start_angels()
        # Wait for at least one observe tick.
        await asyncio.sleep(0.2)
        await rt.stop_angels()

    asyncio.run(run())

    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    audit_file = workspace["audit_root"] / f"{today}.jsonl"
    assert audit_file.exists()
    line = audit_file.read_text().strip().splitlines()[0]
    parsed = json.loads(line)
    assert parsed["caller"] == creature_id
    assert parsed["capability"] == "gmail.send"
    assert parsed["outcome"] == "ok"


def test_start_angels_idempotent(workspace):
    rt = _runtime(workspace, audit_observe_interval=60)
    rt.add_creature(creature_id="x.y", cmd_override=["sleep", "60"])

    async def run():
        await rt.start_angels()
        first = len(rt._angel_tasks)
        await rt.start_angels()
        second = len(rt._angel_tasks)
        await rt.stop_angels()
        return first, second

    first, second = asyncio.run(run())
    assert first == second == 1


def test_stop_angels_cancels_tasks(workspace):
    rt = _runtime(workspace, audit_observe_interval=60)
    rt.add_creature(creature_id="x.y", cmd_override=["sleep", "60"])

    async def run():
        await rt.start_angels()
        await asyncio.sleep(0.05)
        await rt.stop_angels()
        return rt._angel_tasks

    tasks = asyncio.run(run())
    # stop_angels clears the registry.
    assert tasks == {}


def test_angel_observe_exception_is_isolated(workspace, monkeypatch):
    """An exception inside angel.observe() is logged but doesn't kill the task."""
    creature_id = "flaky.thing"
    feed = ObservationFeed(creature_id, base=workspace["feed_base"])
    rt = _runtime(workspace, audit_observe_interval=0.05)
    rt.add_creature(creature_id=creature_id, cmd_override=["sleep", "60"])

    angel = rt._angels[creature_id]
    call_count = {"n": 0}
    original_observe = angel.observe

    async def flaky_observe():
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("first call boom")
        return await original_observe()

    monkeypatch.setattr(angel, "observe", flaky_observe)

    async def run():
        await rt.start_angels()
        await asyncio.sleep(0.2)
        await rt.stop_angels()

    asyncio.run(run())
    assert call_count["n"] >= 2  # task survived the first exception


# ----------------------------- supervisor integration --------------------


def test_creature_subprocess_runs_under_supervisor(workspace):
    """The placeholder cmd actually starts and runs."""
    rt = _runtime(workspace, audit_observe_interval=60)
    rt.add_creature(
        creature_id="boot.test",
        cmd_override=["sh", "-c", "echo BOOTED && sleep 60"],
    )

    log = workspace["log_dir"] / "demiurge-creature-boot.test.log"

    async def run():
        await rt.supervisor.start_all()
        await asyncio.sleep(0.2)
        # Verify it's running.
        st = next(s for s in rt.supervisor.status() if s.name == "demiurge-creature-boot.test")
        assert st.is_running
        await rt.supervisor.stop_all(timeout=2.0)

    asyncio.run(run())
    assert log.exists()
    assert "BOOTED" in log.read_text()
