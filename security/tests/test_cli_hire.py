"""Tests for the `demiurge hire` CLI — v0.11 step 6."""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from demiurge import cli
from demiurge.cli_hire import (
    cmd_hire_list,
    cmd_hire_pause,
    cmd_hire_registry,
    cmd_hire_resume,
    cmd_hire_retire,
    cmd_hire_show,
    cmd_hire_spawn,
)
from shared.plugins.discovery import (
    DiscoveryError,
    DiscoveryResult,
    InstalledPlugin,
)
from shared.plugins.manifest import load_manifest_from_text


# ----------------------------- helpers -----------------------------------


EMAIL_PM_MORTAL_YAML = """\
name: email_pm
kind: mortal
display_name: Email PM
version: "1.0.0"
capabilities:
  - gmail.send
"""


SCHEDULER_AUTOMATON_YAML = """\
name: scheduler
kind: automaton
display_name: Scheduler
version: "1.0.0"
capabilities: []
"""


def _email_pm_plugin() -> InstalledPlugin:
    return InstalledPlugin(
        name="email_pm",
        kind="mortal",
        manifest=load_manifest_from_text(EMAIL_PM_MORTAL_YAML),
        dist_name="demiurge-mortal-email-pm",
        dist_version="1.0.0",
        entry_point_value="demiurge_mortal_email_pm:manifest",
    )


def _args(**kw):
    return argparse.Namespace(**kw)


def _setup_workspace(tmp_path: Path) -> dict[str, Path]:
    ws = {
        "agents_yaml": tmp_path / "agents.yaml",
        "capabilities_yaml": tmp_path / "capabilities.yaml",
        "agents_dir": tmp_path / "agents",
        "feed_base": tmp_path / "feeds",
        "repo_root": tmp_path / "repo",
    }
    ws["agents_dir"].mkdir(parents=True, exist_ok=True)
    ws["repo_root"].mkdir(parents=True, exist_ok=True)
    return ws


def _write_agents_yaml(path: Path, names: list[str]) -> None:
    path.write_text(
        yaml.safe_dump(
            {"agents": [{"name": n, "pubkey_b64": "stub_key"} for n in names]}
        )
    )


# ----------------------------- list ---------------------------------------


def test_hire_list_empty(tmp_path: Path, capsys):
    ws = _setup_workspace(tmp_path)
    rc = cmd_hire_list(_args(agents_yaml=ws["agents_yaml"]))
    out = capsys.readouterr().out
    assert rc == 0
    assert "no Creatures spawned" in out


def test_hire_list_only_creature_shape(tmp_path: Path, capsys):
    """Only `<manifest>.<instance>`-shaped names are Creatures; bare
    agent names like `enkidu` aren't."""
    ws = _setup_workspace(tmp_path)
    _write_agents_yaml(
        ws["agents_yaml"],
        ["enkidu", "email_pm.personal", "trip_planner.tokyo_2026", "operator"],
    )
    rc = cmd_hire_list(_args(agents_yaml=ws["agents_yaml"]))
    out = capsys.readouterr().out
    assert rc == 0
    assert "email_pm.personal" in out
    assert "trip_planner.tokyo_2026" in out
    assert "enkidu" not in out  # not a Creature
    assert "operator" not in out  # not a Creature


def test_hire_list_handles_missing_yaml(tmp_path: Path, capsys):
    rc = cmd_hire_list(_args(agents_yaml=tmp_path / "nonexistent.yaml"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "no Creatures" in out


# ----------------------------- registry -----------------------------------


def test_hire_registry_empty(monkeypatch, capsys):
    monkeypatch.setattr(
        "demiurge.cli_hire.discover", lambda kind: DiscoveryResult()
    )
    rc = cmd_hire_registry(_args())
    out = capsys.readouterr().out
    assert rc == 0
    assert "no Creature plugins" in out


def test_hire_registry_lists_installable(monkeypatch, capsys):
    monkeypatch.setattr(
        "demiurge.cli_hire.discover",
        lambda kind: DiscoveryResult(plugins=[_email_pm_plugin()]),
    )
    rc = cmd_hire_registry(_args())
    out = capsys.readouterr().out
    assert rc == 0
    assert "Installable Creatures" in out
    assert "email_pm" in out
    assert "kind=mortal" in out
    assert "gmail.send" in out


def test_hire_registry_surfaces_broken(monkeypatch, capsys):
    monkeypatch.setattr(
        "demiurge.cli_hire.discover",
        lambda kind: DiscoveryResult(
            errors=[
                DiscoveryError(
                    group="demiurge.mortals",
                    name="broken",
                    dist_name="demiurge-mortal-broken",
                    entry_point_value="x:y",
                    error="ImportError: missing dep",
                )
            ]
        ),
    )
    rc = cmd_hire_registry(_args())
    cap = capsys.readouterr()
    assert rc == 1
    assert "Broken Creature plugins" in cap.err
    assert "broken" in cap.err


# ----------------------------- show ---------------------------------------


def test_hire_show_missing_id(tmp_path: Path, capsys):
    ws = _setup_workspace(tmp_path)
    rc = cmd_hire_show(
        _args(creature_id="email_pm.personal", agents_yaml=ws["agents_yaml"])
    )
    err = capsys.readouterr().err
    assert rc == 1
    assert "no spawned Creature" in err


def test_hire_show_with_spawned_and_installed_plugin(tmp_path: Path, monkeypatch, capsys):
    ws = _setup_workspace(tmp_path)
    _write_agents_yaml(ws["agents_yaml"], ["email_pm.personal"])
    monkeypatch.setattr(
        "demiurge.cli_hire.discover",
        lambda kind: DiscoveryResult(plugins=[_email_pm_plugin()]),
    )
    rc = cmd_hire_show(
        _args(creature_id="email_pm.personal", agents_yaml=ws["agents_yaml"])
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "Creature: email_pm.personal" in out
    assert "kind=mortal" not in out  # this is the show line, not registry
    assert "manifest:    email_pm (mortal)" in out
    assert "gmail.send" in out


def test_hire_show_spawned_but_plugin_uninstalled(tmp_path: Path, monkeypatch, capsys):
    """Creature is in agents.yaml but its plugin isn't installed any more —
    we still show partial info."""
    ws = _setup_workspace(tmp_path)
    _write_agents_yaml(ws["agents_yaml"], ["email_pm.personal"])
    monkeypatch.setattr(
        "demiurge.cli_hire.discover", lambda kind: DiscoveryResult()
    )
    rc = cmd_hire_show(
        _args(creature_id="email_pm.personal", agents_yaml=ws["agents_yaml"])
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "manifest plugin not currently installed" in out
    assert "consider `demiurge hire retire" in out


# ----------------------------- spawn -------------------------------------


def test_hire_spawn_from_yaml_succeeds(tmp_path: Path, monkeypatch, capsys):
    """End-to-end: --from-yaml + real forge_mortal (against fake DB)."""
    ws = _setup_workspace(tmp_path)
    manifest_path = tmp_path / "plugin.yaml"
    manifest_path.write_text(EMAIL_PM_MORTAL_YAML)

    rc = cmd_hire_spawn(
        _args(
            name="email_pm",
            instance_id="personal",
            from_yaml=str(manifest_path),
            agents_yaml=ws["agents_yaml"],
            capabilities_yaml=ws["capabilities_yaml"],
            agents_dir=ws["agents_dir"],
            repo_root=str(ws["repo_root"]),
            skip_pg_schema=True,
            skip_bootstrap_hook=True,
            force=False,
        )
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "Spawned mortal 'email_pm.personal'" in out
    assert "agent key:" in out
    assert "observation feed:" in out

    # Verify agents.yaml has the new spawn.
    spawned = yaml.safe_load(ws["agents_yaml"].read_text())["agents"]
    assert any(a["name"] == "email_pm.personal" for a in spawned)


def test_hire_spawn_name_mismatch(tmp_path: Path, capsys):
    ws = _setup_workspace(tmp_path)
    manifest_path = tmp_path / "plugin.yaml"
    manifest_path.write_text(EMAIL_PM_MORTAL_YAML)
    rc = cmd_hire_spawn(
        _args(
            name="other_thing",
            instance_id="personal",
            from_yaml=str(manifest_path),
            agents_yaml=ws["agents_yaml"],
            capabilities_yaml=ws["capabilities_yaml"],
            agents_dir=ws["agents_dir"],
            repo_root=str(ws["repo_root"]),
            skip_pg_schema=True,
            skip_bootstrap_hook=True,
            force=False,
        )
    )
    err = capsys.readouterr().err
    assert rc == 2
    assert "manifest declares" in err


def test_hire_spawn_unknown_name(monkeypatch, tmp_path: Path, capsys):
    ws = _setup_workspace(tmp_path)
    monkeypatch.setattr(
        "demiurge.cli_hire.discover", lambda kind: DiscoveryResult()
    )
    rc = cmd_hire_spawn(
        _args(
            name="missing",
            instance_id="x",
            from_yaml=None,
            agents_yaml=ws["agents_yaml"],
            capabilities_yaml=ws["capabilities_yaml"],
            agents_dir=ws["agents_dir"],
            repo_root=str(ws["repo_root"]),
            skip_pg_schema=True,
            skip_bootstrap_hook=True,
            force=False,
        )
    )
    err = capsys.readouterr().err
    assert rc == 1
    assert "no Creature plugin 'missing'" in err


def test_hire_spawn_automaton_kind(tmp_path: Path, capsys):
    """Spawning an Automaton manifest dispatches to forge_automaton."""
    ws = _setup_workspace(tmp_path)
    manifest_path = tmp_path / "plugin.yaml"
    manifest_path.write_text(SCHEDULER_AUTOMATON_YAML)
    rc = cmd_hire_spawn(
        _args(
            name="scheduler",
            instance_id="default",
            from_yaml=str(manifest_path),
            agents_yaml=ws["agents_yaml"],
            capabilities_yaml=ws["capabilities_yaml"],
            agents_dir=ws["agents_dir"],
            repo_root=str(ws["repo_root"]),
            skip_pg_schema=True,
            skip_bootstrap_hook=True,
            force=False,
        )
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "Spawned automaton 'scheduler.default'" in out


def test_hire_spawn_rejects_power_kind(tmp_path: Path, capsys):
    """Powers go through `demiurge powers install`, not `hire spawn`."""
    ws = _setup_workspace(tmp_path)
    manifest_path = tmp_path / "plugin.yaml"
    manifest_path.write_text("""\
name: gmail
kind: power
display_name: Gmail
version: "1.0.0"
modes: [request-based]
capabilities: []
bootstrap: x:y
""")
    rc = cmd_hire_spawn(
        _args(
            name="gmail",
            instance_id="default",
            from_yaml=str(manifest_path),
            agents_yaml=ws["agents_yaml"],
            capabilities_yaml=ws["capabilities_yaml"],
            agents_dir=ws["agents_dir"],
            repo_root=str(ws["repo_root"]),
            skip_pg_schema=True,
            skip_bootstrap_hook=True,
            force=False,
        )
    )
    err = capsys.readouterr().err
    assert rc == 2
    assert "powers install" in err


# ----------------------------- retire ------------------------------------


def test_hire_retire_calls_archive(tmp_path: Path, monkeypatch, capsys):
    ws = _setup_workspace(tmp_path)
    captured = {}

    from demiurge.pantheon.hades import ArchiveAction, ArchiveResult

    def fake_archive(creature_id, **kw):
        captured["id"] = creature_id
        captured["kw"] = kw
        return ArchiveResult(
            creature_id=creature_id,
            kind="mortal",
            actions=[ArchiveAction(verb="removed", description="agent identity")],
        )

    import demiurge.pantheon.hades as hades_mod

    monkeypatch.setattr(hades_mod, "archive_mortal", fake_archive)
    monkeypatch.setattr(
        "demiurge.cli_hire.discover", lambda kind: DiscoveryResult()
    )

    rc = cmd_hire_retire(
        _args(
            creature_id="email_pm.personal",
            agents_yaml=ws["agents_yaml"],
            capabilities_yaml=ws["capabilities_yaml"],
            agents_dir=ws["agents_dir"],
            drop_data=False,
        )
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert captured["id"] == "email_pm.personal"
    assert "Archived mortal" in out


def test_hire_retire_uses_kind_from_discovered_manifest(tmp_path: Path, monkeypatch, capsys):
    """If the plugin is still installed, its kind drives which archive_X is called."""
    ws = _setup_workspace(tmp_path)
    captured = {}

    from demiurge.pantheon.hades import ArchiveAction, ArchiveResult

    def fake_archive_automaton(creature_id, **kw):
        captured["called"] = "automaton"
        return ArchiveResult(
            creature_id=creature_id,
            kind="automaton",
            actions=[ArchiveAction(verb="removed", description="agent")],
        )

    def fake_archive_mortal(creature_id, **kw):
        captured["called"] = "mortal"
        return ArchiveResult(
            creature_id=creature_id,
            kind="mortal",
            actions=[ArchiveAction(verb="removed", description="agent")],
        )

    import demiurge.pantheon.hades as hades_mod

    monkeypatch.setattr(hades_mod, "archive_automaton", fake_archive_automaton)
    monkeypatch.setattr(hades_mod, "archive_mortal", fake_archive_mortal)

    auto_plugin = InstalledPlugin(
        name="scheduler",
        kind="automaton",
        manifest=load_manifest_from_text(SCHEDULER_AUTOMATON_YAML),
        dist_name="x",
        dist_version="1.0.0",
        entry_point_value="x:y",
    )
    monkeypatch.setattr(
        "demiurge.cli_hire.discover",
        lambda kind: DiscoveryResult(plugins=[auto_plugin]),
    )

    cmd_hire_retire(
        _args(
            creature_id="scheduler.default",
            agents_yaml=ws["agents_yaml"],
            capabilities_yaml=ws["capabilities_yaml"],
            agents_dir=ws["agents_dir"],
            drop_data=False,
        )
    )
    assert captured["called"] == "automaton"


# ----------------------------- pause / resume stubs ----------------------


def test_hire_pause_when_daemon_not_running(capsys, tmp_path: Path, monkeypatch):
    """Daemon down → clear error and rc=1."""
    monkeypatch.setattr(
        "demiurge.runtime.daemon.default_socket_path",
        lambda: tmp_path / "missing.sock",
    )
    rc = cmd_hire_pause(_args(creature_id="x"))
    err = capsys.readouterr().err
    assert rc == 1
    assert "runtime daemon is not running" in err


def test_hire_resume_when_daemon_not_running(capsys, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "demiurge.runtime.daemon.default_socket_path",
        lambda: tmp_path / "missing.sock",
    )
    rc = cmd_hire_resume(_args(creature_id="x"))
    err = capsys.readouterr().err
    assert rc == 1
    assert "runtime daemon is not running" in err


def test_hire_pause_when_daemon_responds_ok(monkeypatch, capsys):
    """Daemon ok-response → rc=0 + 'paused …' message."""

    def fake_send(req, **kw):
        return {"ok": True, "data": {"creature_id": req["creature_id"], "paused": True}}

    monkeypatch.setattr("demiurge.cli_hire.send_request", fake_send, raising=False)
    # cli_hire imports send_request lazily inside _send_runtime_request,
    # so we patch at the import location used there.
    import demiurge.runtime.daemon as daemon_mod

    monkeypatch.setattr(daemon_mod, "send_request", fake_send)

    rc = cmd_hire_pause(_args(creature_id="email_pm.personal"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "paused email_pm.personal" in out


def test_hire_resume_when_daemon_responds_ok(monkeypatch, capsys):
    def fake_send(req, **kw):
        return {"ok": True, "data": {"creature_id": req["creature_id"], "resumed": True}}

    import demiurge.runtime.daemon as daemon_mod

    monkeypatch.setattr(daemon_mod, "send_request", fake_send)

    rc = cmd_hire_resume(_args(creature_id="email_pm.personal"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "resumed email_pm.personal" in out


def test_hire_pause_when_daemon_refuses(monkeypatch, capsys):
    """Daemon error response → rc=1 + error from daemon."""

    def fake_send(req, **kw):
        return {"ok": False, "error": "creature not found"}

    import demiurge.runtime.daemon as daemon_mod

    monkeypatch.setattr(daemon_mod, "send_request", fake_send)

    rc = cmd_hire_pause(_args(creature_id="ghost.thing"))
    err = capsys.readouterr().err
    assert rc == 1
    assert "creature not found" in err


# ----------------------------- top-level argparse ------------------------


def test_top_level_hire_subcommand_exists():
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["hire", "--help"])


def test_top_level_hire_spawn_args():
    parser = cli.build_parser()
    args = parser.parse_args(
        [
            "hire",
            "spawn",
            "email_pm",
            "--instance",
            "personal",
            "--from-yaml",
            "/tmp/plugin.yaml",
            "--skip-pg-schema",
            "--skip-bootstrap-hook",
        ]
    )
    assert args.cmd == "hire"
    assert args.subcmd == "spawn"
    assert args.name == "email_pm"
    assert args.instance_id == "personal"
    assert args.from_yaml == "/tmp/plugin.yaml"
    assert args.skip_pg_schema is True
    assert args.skip_bootstrap_hook is True


def test_top_level_hire_install_alias_works():
    """`install` is an alias of `spawn` — both parse identically."""
    parser = cli.build_parser()
    args = parser.parse_args(
        ["hire", "install", "email_pm", "--instance", "personal"]
    )
    assert args.cmd == "hire"
    assert args.subcmd == "install"
    assert args.fn is cmd_hire_spawn


def test_top_level_hire_retire():
    parser = cli.build_parser()
    args = parser.parse_args(["hire", "retire", "email_pm.personal", "--drop-data"])
    assert args.cmd == "hire"
    assert args.subcmd == "retire"
    assert args.creature_id == "email_pm.personal"
    assert args.drop_data is True
