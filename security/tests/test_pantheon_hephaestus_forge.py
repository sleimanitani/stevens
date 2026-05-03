"""Tests for demiurge.pantheon.hephaestus.forge — v0.11 step 3d."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from demiurge.pantheon.hephaestus import (
    ForgeAction,
    ForgeError,
    ForgeResult,
    forge_power,
)
from shared.plugins.manifest import Manifest, load_manifest_from_text


# ----------------------------- manifest fixtures -------------------------


GMAIL_WEBHOOK = """\
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
capabilities:
  - gmail.send
  - gmail.read
bootstrap: gmail_adapter.bootstrap:install
"""


SIGNAL_LISTENER = """\
name: signal
kind: power
display_name: Signal
version: "0.5.0"
modes: [listener]
runtime:
  listener:
    command: signal_adapter.main:run
    restart: on-failure
capabilities:
  - signal.send
bootstrap: signal_adapter.bootstrap:install
"""


IMAGE_GEN_REQUEST_ONLY = """\
name: image_gen
kind: power
display_name: Image Generator
version: "1.0.0"
modes: [request-based]
capabilities:
  - image.generate
bootstrap: image_gen.bootstrap:install
"""


RSS_POLLING_ONLY = """\
name: rss_reader
kind: power
display_name: RSS Reader
version: "0.1.0"
modes: [polling]
runtime:
  polling:
    command: rss_reader.fetch:run_once
    interval: 1h
capabilities:
  - rss.subscribe
bootstrap: rss_reader.bootstrap:install
"""


EMAIL_PM_MORTAL = """\
name: email_pm
kind: mortal
display_name: Email PM
version: "1.0.0"
capabilities:
  - gmail.draft
powers:
  - gmail
"""


# ----------------------------- kind-validation ---------------------------


def test_forge_power_rejects_mortal_manifest(tmp_path: Path):
    m = load_manifest_from_text(EMAIL_PM_MORTAL)
    with pytest.raises(ForgeError, match="kind='power'"):
        asyncio.run(
            forge_power(
                m,
                repo_root=tmp_path,
                target_dir=tmp_path / "units",
                env_file=tmp_path / "env",
                skip_bootstrap_hook=True,
            )
        )


# ----------------------------- webhook power -----------------------------


def test_forge_power_webhook_writes_systemd_unit(tmp_path: Path):
    m = load_manifest_from_text(GMAIL_WEBHOOK)
    result = asyncio.run(
        forge_power(
            m,
            repo_root=tmp_path / "repo",
            target_dir=tmp_path / "units",
            env_file=tmp_path / "env",
            skip_bootstrap_hook=True,
        )
    )
    assert result.kind == "power"
    assert result.creature_id == "gmail"
    assert len(result.systemd_units) == 1
    action = result.systemd_units[0]
    assert action.verb == "created"
    assert action.path.name == "demiurge-power-gmail.service"

    text = action.path.read_text()
    assert "uvicorn gmail_adapter.main:app" in text
    assert "--host 127.0.0.1" in text
    assert "--port 8080" in text
    assert "After=demiurge-security.service" in text
    assert "WorkingDirectory=" in text


def test_forge_power_webhook_idempotent(tmp_path: Path):
    """Re-forging the same manifest is a no-op (unchanged action)."""
    m = load_manifest_from_text(GMAIL_WEBHOOK)
    kw = dict(
        repo_root=tmp_path / "repo",
        target_dir=tmp_path / "units",
        env_file=tmp_path / "env",
        skip_bootstrap_hook=True,
    )
    asyncio.run(forge_power(m, **kw))
    second = asyncio.run(forge_power(m, **kw))
    assert all(a.verb == "unchanged" for a in second.systemd_units)


def test_forge_power_webhook_differential_reports_updated(tmp_path: Path):
    """Manifest edit since last forge → updated + restart hint."""
    m1 = load_manifest_from_text(GMAIL_WEBHOOK)
    repo1 = tmp_path / "repo_v1"
    repo2 = tmp_path / "repo_v2"
    target = tmp_path / "units"

    asyncio.run(
        forge_power(
            m1,
            repo_root=repo1,
            target_dir=target,
            env_file=tmp_path / "env",
            skip_bootstrap_hook=True,
        )
    )
    result = asyncio.run(
        forge_power(
            m1,
            repo_root=repo2,  # different working directory → unit content changes
            target_dir=target,
            env_file=tmp_path / "env",
            skip_bootstrap_hook=True,
        )
    )
    assert result.systemd_units[0].verb == "updated"
    assert any("restart" in n for n in result.notes)


# ----------------------------- listener power ----------------------------


def test_forge_power_listener_writes_systemd_unit(tmp_path: Path):
    m = load_manifest_from_text(SIGNAL_LISTENER)
    result = asyncio.run(
        forge_power(
            m,
            repo_root=tmp_path / "repo",
            target_dir=tmp_path / "units",
            env_file=tmp_path / "env",
            skip_bootstrap_hook=True,
        )
    )
    assert len(result.systemd_units) == 1
    text = result.systemd_units[0].path.read_text()
    # Listener invocation goes through python -c so we don't require
    # the plugin to ship a __main__.py.
    assert "import asyncio" in text
    assert "signal_adapter.main" in text
    assert "run" in text


# ----------------------------- request-based-only ------------------------


def test_forge_power_request_based_only_writes_no_units(tmp_path: Path):
    m = load_manifest_from_text(IMAGE_GEN_REQUEST_ONLY)
    result = asyncio.run(
        forge_power(
            m,
            repo_root=tmp_path / "repo",
            target_dir=tmp_path / "units",
            env_file=tmp_path / "env",
            skip_bootstrap_hook=True,
        )
    )
    assert result.systemd_units == []
    assert result.creature_id == "image_gen"
    assert "image.generate" in result.capabilities
    # No notes about runtime artifacts since none were needed.


# ----------------------------- polling power -----------------------------


def test_forge_power_polling_emits_deferred_note(tmp_path: Path):
    m = load_manifest_from_text(RSS_POLLING_ONLY)
    result = asyncio.run(
        forge_power(
            m,
            repo_root=tmp_path / "repo",
            target_dir=tmp_path / "units",
            env_file=tmp_path / "env",
            skip_bootstrap_hook=True,
        )
    )
    # No unit yet for polling — deferred until 3e/3d.1
    assert result.systemd_units == []
    assert any("polling" in n for n in result.notes)
    assert any("deferred" in n for n in result.notes)


# ----------------------------- bootstrap hook ----------------------------


def test_forge_power_bootstrap_hook_skipped_when_dry_run(tmp_path: Path):
    m = load_manifest_from_text(IMAGE_GEN_REQUEST_ONLY)
    result = asyncio.run(
        forge_power(
            m,
            repo_root=tmp_path / "repo",
            target_dir=tmp_path / "units",
            env_file=tmp_path / "env",
            skip_bootstrap_hook=True,
        )
    )
    assert result.bootstrap_executed is False
    assert any("dry-run" in n for n in result.notes)


def test_forge_power_bootstrap_hook_module_not_importable(tmp_path: Path):
    """A bootstrap hook for a not-yet-installed plugin: best-effort skip."""
    m = load_manifest_from_text(IMAGE_GEN_REQUEST_ONLY)  # imagegen not installed
    result = asyncio.run(
        forge_power(
            m,
            repo_root=tmp_path / "repo",
            target_dir=tmp_path / "units",
            env_file=tmp_path / "env",
            skip_bootstrap_hook=False,
        )
    )
    assert result.bootstrap_executed is False
    assert any("not importable" in n for n in result.notes)


def test_forge_power_bootstrap_hook_runs_when_importable(tmp_path: Path, monkeypatch):
    """Best-effort import + call works when the module is on path."""
    pkg_dir = tmp_path / "fake_power_with_hook"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    hook_file = pkg_dir / "bootstrap.py"
    hook_file.write_text(
        "calls = []\n"
        "def install(manifest):\n"
        "    calls.append(manifest.name)\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop("fake_power_with_hook", None)
    sys.modules.pop("fake_power_with_hook.bootstrap", None)

    m_text = """\
name: fake_thing
kind: power
display_name: Fake Thing
version: "1.0.0"
modes: [request-based]
capabilities: []
bootstrap: fake_power_with_hook.bootstrap:install
"""
    m = load_manifest_from_text(m_text)
    result = asyncio.run(
        forge_power(
            m,
            repo_root=tmp_path / "repo",
            target_dir=tmp_path / "units",
            env_file=tmp_path / "env",
            skip_bootstrap_hook=False,
        )
    )
    assert result.bootstrap_executed is True
    # Verify the hook actually ran:
    import importlib

    mod = importlib.import_module("fake_power_with_hook.bootstrap")
    assert mod.calls == ["fake_thing"]


def test_forge_power_bootstrap_hook_async_runs(tmp_path: Path, monkeypatch):
    """Async bootstrap hooks are awaited."""
    pkg_dir = tmp_path / "fake_power_async"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    (pkg_dir / "bootstrap.py").write_text(
        "calls = []\n"
        "async def install(manifest):\n"
        "    calls.append(('async', manifest.name))\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop("fake_power_async", None)
    sys.modules.pop("fake_power_async.bootstrap", None)

    m_text = """\
name: fake_async
kind: power
display_name: Fake Async
version: "1.0.0"
modes: [request-based]
capabilities: []
bootstrap: fake_power_async.bootstrap:install
"""
    m = load_manifest_from_text(m_text)
    result = asyncio.run(
        forge_power(
            m,
            repo_root=tmp_path / "repo",
            target_dir=tmp_path / "units",
            env_file=tmp_path / "env",
            skip_bootstrap_hook=False,
        )
    )
    assert result.bootstrap_executed is True
    import importlib

    mod = importlib.import_module("fake_power_async.bootstrap")
    assert mod.calls == [("async", "fake_async")]


def test_forge_power_bootstrap_hook_raises_recorded(tmp_path: Path, monkeypatch):
    """A hook that raises is recorded as a note; forge doesn't fail."""
    pkg_dir = tmp_path / "fake_power_raises"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    (pkg_dir / "bootstrap.py").write_text(
        "def install(manifest):\n"
        "    raise RuntimeError('bootstrap exploded')\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop("fake_power_raises", None)
    sys.modules.pop("fake_power_raises.bootstrap", None)

    m_text = """\
name: fake_raises
kind: power
display_name: Fake Raises
version: "1.0.0"
modes: [request-based]
capabilities: []
bootstrap: fake_power_raises.bootstrap:install
"""
    m = load_manifest_from_text(m_text)
    result = asyncio.run(
        forge_power(
            m,
            repo_root=tmp_path / "repo",
            target_dir=tmp_path / "units",
            env_file=tmp_path / "env",
            skip_bootstrap_hook=False,
        )
    )
    assert result.bootstrap_executed is False
    assert any("raised" in n and "bootstrap exploded" in n for n in result.notes)


def test_forge_power_bootstrap_hook_attr_missing(tmp_path: Path, monkeypatch):
    pkg_dir = tmp_path / "fake_power_no_attr"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    (pkg_dir / "bootstrap.py").write_text("# no install function defined here\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop("fake_power_no_attr", None)
    sys.modules.pop("fake_power_no_attr.bootstrap", None)

    m_text = """\
name: fake_missing
kind: power
display_name: Fake Missing
version: "1.0.0"
modes: [request-based]
capabilities: []
bootstrap: fake_power_no_attr.bootstrap:install
"""
    m = load_manifest_from_text(m_text)
    result = asyncio.run(
        forge_power(
            m,
            repo_root=tmp_path / "repo",
            target_dir=tmp_path / "units",
            env_file=tmp_path / "env",
            skip_bootstrap_hook=False,
        )
    )
    assert result.bootstrap_executed is False
    assert any("no attribute" in n for n in result.notes)


# ----------------------------- ForgeResult.format_report -----------------


def test_forge_result_format_report_shape(tmp_path: Path):
    m = load_manifest_from_text(GMAIL_WEBHOOK)
    result = asyncio.run(
        forge_power(
            m,
            repo_root=tmp_path / "repo",
            target_dir=tmp_path / "units",
            env_file=tmp_path / "env",
            skip_bootstrap_hook=True,
        )
    )
    out = result.format_report()
    assert "gmail" in out
    assert "demiurge-power-gmail.service" in out
    assert "gmail.send" in out
    assert "gmail.read" in out
    assert "dry-run" in out  # bootstrap-skipped note rendered
