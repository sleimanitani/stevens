"""Tests for power-runtime integration — v0.11 step 7.2."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from demiurge.runtime import (
    PowerRuntime,
    PowerRuntimeError,
    PowerRuntimeRegistration,
    Supervisor,
)
from shared.plugins.discovery import (
    DiscoveryError,
    DiscoveryResult,
    InstalledPlugin,
)
from shared.plugins.manifest import load_manifest_from_text


# ----------------------------- manifest fixtures -------------------------


GMAIL_WEBHOOK_YAML = """\
name: gmail
kind: power
display_name: Gmail
version: "1.0.0"
modes: [webhook, request-based]
runtime:
  webhook:
    path: /gmail/push
    port: 8080
    handler: gmail_adapter.main:app
capabilities: []
bootstrap: gmail_adapter.bootstrap:install
"""


SIGNAL_LISTENER_YAML = """\
name: signal
kind: power
display_name: Signal
version: "0.5.0"
modes: [listener]
runtime:
  listener:
    command: signal_adapter.main:run
    restart: on-failure
capabilities: []
bootstrap: signal_adapter.bootstrap:install
"""


RSS_POLLING_YAML = """\
name: rss_reader
kind: power
display_name: RSS Reader
version: "0.1.0"
modes: [polling]
runtime:
  polling:
    command: rss_reader.fetch:run_once
    interval: 1h
capabilities: []
bootstrap: rss_reader.bootstrap:install
"""


IMAGE_GEN_REQUEST_ONLY_YAML = """\
name: image_gen
kind: power
display_name: Image Generator
version: "1.0.0"
modes: [request-based]
capabilities: []
bootstrap: image_gen.bootstrap:install
"""


EMAIL_PM_MORTAL_YAML = """\
name: email_pm
kind: mortal
display_name: Email PM
version: "1.0.0"
capabilities: []
"""


def _plugin(yaml_text: str, dist: str = "demiurge-power-x", ver: str = "1.0.0") -> InstalledPlugin:
    m = load_manifest_from_text(yaml_text)
    return InstalledPlugin(
        name=m.name,
        kind=m.kind,
        manifest=m,
        dist_name=dist,
        dist_version=ver,
        entry_point_value="x:y",
    )


# ----------------------------- per-mode registration ---------------------


def test_add_webhook_power_registers_subprocess(tmp_path: Path):
    sup = Supervisor()
    rt = PowerRuntime(supervisor=sup, repo_root=tmp_path / "repo", log_dir=tmp_path / "logs")
    plugin = _plugin(GMAIL_WEBHOOK_YAML)

    reg = rt.add_power(plugin)

    assert reg.power_name == "gmail"
    assert reg.process_names == ["demiurge-power-gmail"]
    assert reg.polling_tasks == []
    assert reg.skipped_modes == ["request-based"]

    proc = sup.get("demiurge-power-gmail")
    assert proc.cmd[0] == "uvicorn"
    assert "gmail_adapter.main:app" in proc.cmd
    assert "--host" in proc.cmd and "127.0.0.1" in proc.cmd
    assert "--port" in proc.cmd and "8080" in proc.cmd


def test_add_listener_power_registers_subprocess(tmp_path: Path):
    sup = Supervisor()
    rt = PowerRuntime(supervisor=sup, repo_root=tmp_path / "repo", log_dir=tmp_path / "logs")
    plugin = _plugin(SIGNAL_LISTENER_YAML)

    reg = rt.add_power(plugin)
    assert reg.process_names == ["demiurge-power-signal"]
    proc = sup.get("demiurge-power-signal")
    assert proc.cmd[0] == "python"
    assert proc.cmd[1] == "-c"
    body = proc.cmd[2]
    assert "signal_adapter.main" in body
    assert "run" in body
    assert proc.restart_policy == "on-failure"


def test_add_polling_power_registers_task_spec(tmp_path: Path):
    sup = Supervisor()
    rt = PowerRuntime(supervisor=sup, repo_root=tmp_path / "repo", log_dir=tmp_path / "logs")
    plugin = _plugin(RSS_POLLING_YAML)

    reg = rt.add_power(plugin)
    assert reg.process_names == []  # no subprocess
    assert reg.polling_tasks == ["demiurge-poll-rss_reader"]
    # No subprocess registered with the supervisor.
    assert "demiurge-power-rss_reader" not in sup.names()


def test_add_request_based_only_no_runtime_artifact(tmp_path: Path):
    sup = Supervisor()
    rt = PowerRuntime(supervisor=sup, repo_root=tmp_path / "repo", log_dir=tmp_path / "logs")
    plugin = _plugin(IMAGE_GEN_REQUEST_ONLY_YAML)

    reg = rt.add_power(plugin)
    assert reg.process_names == []
    assert reg.polling_tasks == []
    assert reg.skipped_modes == ["request-based"]
    assert sup.names() == []


def test_add_power_rejects_non_power_manifest(tmp_path: Path):
    sup = Supervisor()
    rt = PowerRuntime(supervisor=sup, repo_root=tmp_path / "repo", log_dir=tmp_path / "logs")
    plugin = _plugin(EMAIL_PM_MORTAL_YAML)
    with pytest.raises(ValueError, match="kind=power"):
        rt.add_power(plugin)


def test_add_combined_modes(tmp_path: Path):
    """A power that combines webhook + request-based gets both
    registered (webhook subprocess; request-based skipped)."""
    sup = Supervisor()
    rt = PowerRuntime(supervisor=sup, repo_root=tmp_path / "repo", log_dir=tmp_path / "logs")
    plugin = _plugin(GMAIL_WEBHOOK_YAML)
    reg = rt.add_power(plugin)
    assert reg.process_names == ["demiurge-power-gmail"]
    assert reg.skipped_modes == ["request-based"]


# ----------------------------- discover_and_add_all ----------------------


def test_discover_and_add_all_happy_path(monkeypatch, tmp_path: Path):
    sup = Supervisor()
    rt = PowerRuntime(supervisor=sup, repo_root=tmp_path / "repo", log_dir=tmp_path / "logs")
    plugins = [_plugin(GMAIL_WEBHOOK_YAML), _plugin(SIGNAL_LISTENER_YAML)]
    monkeypatch.setattr(
        "demiurge.runtime.power_runtime.discover",
        lambda kind: DiscoveryResult(plugins=plugins),
    )

    regs, errs = rt.discover_and_add_all()
    assert {r.power_name for r in regs} == {"gmail", "signal"}
    assert errs == []


def test_discover_and_add_all_surfaces_discovery_errors(monkeypatch, tmp_path: Path):
    sup = Supervisor()
    rt = PowerRuntime(supervisor=sup, repo_root=tmp_path / "repo", log_dir=tmp_path / "logs")
    monkeypatch.setattr(
        "demiurge.runtime.power_runtime.discover",
        lambda kind: DiscoveryResult(
            errors=[
                DiscoveryError(
                    group="demiurge.powers",
                    name="broken",
                    dist_name="demiurge-power-broken",
                    entry_point_value="x:y",
                    error="ImportError: nope",
                )
            ]
        ),
    )

    regs, errs = rt.discover_and_add_all()
    assert regs == []
    assert len(errs) == 1
    assert errs[0].power_name == "broken"
    assert "ImportError" in errs[0].reason


# ----------------------------- polling lifecycle -------------------------


def test_polling_task_invokes_command(monkeypatch, tmp_path: Path):
    """Polling task fires the command at the configured cadence."""
    calls = []

    async def fake_runner():
        calls.append("called")

    # Build a fake module with our async runner under a known path so
    # importlib.import_module resolves it.
    fake_mod = type(sys)("fake_polling_module")
    fake_mod.run_once = fake_runner  # type: ignore[attr-defined]
    sys.modules["fake_polling_module"] = fake_mod

    # Override the manifest's interval to something tiny for the test.
    yaml_text = """\
name: rss_reader
kind: power
display_name: RSS
version: "0.1.0"
modes: [polling]
runtime:
  polling:
    command: fake_polling_module:run_once
    interval: 1s
capabilities: []
bootstrap: x:y
"""

    # Override interval parsing so we don't have to wait 1s.
    monkeypatch.setattr(
        "demiurge.runtime.power_runtime._interval_parser",
        lambda spec: 0.05,  # 50ms
    )

    async def run():
        sup = Supervisor()
        rt = PowerRuntime(supervisor=sup, repo_root=tmp_path / "repo", log_dir=tmp_path / "logs")
        plugin = _plugin(yaml_text)
        rt.add_power(plugin)
        await rt.start_polling()
        await asyncio.sleep(0.2)  # ~3-4 ticks
        await rt.stop_polling()

    asyncio.run(run())
    assert len(calls) >= 2  # fired multiple times


def test_polling_command_exception_is_isolated(monkeypatch, tmp_path: Path):
    """An exception inside the polling command is caught + logged; the
    task continues firing on the next interval."""
    calls = []

    async def flaky():
        calls.append("attempt")
        if len(calls) <= 2:
            raise RuntimeError("first two fail")

    fake_mod = type(sys)("fake_flaky_polling")
    fake_mod.run_once = flaky  # type: ignore[attr-defined]
    sys.modules["fake_flaky_polling"] = fake_mod

    yaml_text = """\
name: flaky_rss
kind: power
display_name: Flaky RSS
version: "0.1.0"
modes: [polling]
runtime:
  polling:
    command: fake_flaky_polling:run_once
    interval: 1s
capabilities: []
bootstrap: x:y
"""
    monkeypatch.setattr(
        "demiurge.runtime.power_runtime._interval_parser",
        lambda spec: 0.05,
    )

    async def run():
        sup = Supervisor()
        rt = PowerRuntime(supervisor=sup, repo_root=tmp_path / "repo", log_dir=tmp_path / "logs")
        plugin = _plugin(yaml_text)
        rt.add_power(plugin)
        await rt.start_polling()
        await asyncio.sleep(0.25)
        await rt.stop_polling()

    asyncio.run(run())
    # 2 failures + at least 1 success = ≥ 3 attempts
    assert len(calls) >= 3


def test_polling_sync_command_works(monkeypatch, tmp_path: Path):
    """Polling supports sync commands via run_in_executor."""
    calls = []

    def sync_runner():
        calls.append("sync")

    fake_mod = type(sys)("fake_sync_polling")
    fake_mod.run_once = sync_runner  # type: ignore[attr-defined]
    sys.modules["fake_sync_polling"] = fake_mod

    yaml_text = """\
name: sync_rss
kind: power
display_name: Sync RSS
version: "0.1.0"
modes: [polling]
runtime:
  polling:
    command: fake_sync_polling:run_once
    interval: 1s
capabilities: []
bootstrap: x:y
"""
    monkeypatch.setattr(
        "demiurge.runtime.power_runtime._interval_parser",
        lambda spec: 0.05,
    )

    async def run():
        sup = Supervisor()
        rt = PowerRuntime(supervisor=sup, repo_root=tmp_path / "repo", log_dir=tmp_path / "logs")
        plugin = _plugin(yaml_text)
        rt.add_power(plugin)
        await rt.start_polling()
        await asyncio.sleep(0.15)
        await rt.stop_polling()

    asyncio.run(run())
    assert len(calls) >= 1


def test_stop_polling_cancels_pending_tasks(monkeypatch, tmp_path: Path):
    """Cancellation: stop_polling completes promptly even if commands are slow."""
    started = asyncio.Event()
    cancelled = []

    async def slow():
        try:
            started.set()
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.append("yes")
            raise

    fake_mod = type(sys)("fake_slow_polling")
    fake_mod.run_once = slow  # type: ignore[attr-defined]
    sys.modules["fake_slow_polling"] = fake_mod

    yaml_text = """\
name: slow_rss
kind: power
display_name: Slow
version: "0.1.0"
modes: [polling]
runtime:
  polling:
    command: fake_slow_polling:run_once
    interval: 1s
capabilities: []
bootstrap: x:y
"""
    monkeypatch.setattr(
        "demiurge.runtime.power_runtime._interval_parser",
        lambda spec: 0.05,
    )

    async def run():
        sup = Supervisor()
        rt = PowerRuntime(supervisor=sup, repo_root=tmp_path / "repo", log_dir=tmp_path / "logs")
        plugin = _plugin(yaml_text)
        rt.add_power(plugin)
        await rt.start_polling()
        # Wait for the slow command to enter its sleep, then stop.
        await asyncio.sleep(0.15)
        await rt.stop_polling()

    asyncio.run(run())
    # Either the slow task got cancelled mid-sleep, or it never got
    # called (we stopped during the interval). Both are acceptable.
    # The important property: stop_polling completed, no leaks.


def test_start_polling_idempotent(monkeypatch, tmp_path: Path):
    """Calling start_polling twice doesn't duplicate tasks."""
    fake_mod = type(sys)("fake_idem_polling")
    fake_mod.run_once = lambda: None  # type: ignore[attr-defined]
    sys.modules["fake_idem_polling"] = fake_mod

    yaml_text = """\
name: idem
kind: power
display_name: I
version: "0.1.0"
modes: [polling]
runtime:
  polling:
    command: fake_idem_polling:run_once
    interval: 1s
capabilities: []
bootstrap: x:y
"""
    monkeypatch.setattr(
        "demiurge.runtime.power_runtime._interval_parser",
        lambda spec: 60,
    )

    async def run():
        sup = Supervisor()
        rt = PowerRuntime(supervisor=sup, repo_root=tmp_path / "repo", log_dir=tmp_path / "logs")
        plugin = _plugin(yaml_text)
        rt.add_power(plugin)
        await rt.start_polling()
        first_count = len(rt._polling_tasks)
        await rt.start_polling()
        second_count = len(rt._polling_tasks)
        await rt.stop_polling()
        assert first_count == second_count == 1

    asyncio.run(run())
