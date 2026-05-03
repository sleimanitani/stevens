"""Tests for the `demiurge powers` CLI — v0.11 step 5."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from demiurge import cli
from demiurge.cli_powers import (
    cmd_channels_list_deprecated,
    cmd_powers_install,
    cmd_powers_list,
    cmd_powers_registry,
    cmd_powers_show,
    cmd_powers_uninstall,
)
from shared.plugins.discovery import (
    DiscoveryError,
    DiscoveryResult,
    InstalledPlugin,
)
from shared.plugins.manifest import load_manifest_from_text


# ----------------------------- helpers -----------------------------------


GMAIL_POWER_YAML = """\
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
secrets:
  - name: gmail.oauth_client.id
    prompt: "Google OAuth client ID"
    onboard_via: "demiurge wizard google"
bootstrap: gmail_adapter.bootstrap:install
"""


def _gmail_plugin() -> InstalledPlugin:
    return InstalledPlugin(
        name="gmail",
        kind="power",
        manifest=load_manifest_from_text(GMAIL_POWER_YAML),
        dist_name="demiurge-power-gmail",
        dist_version="1.0.0",
        entry_point_value="demiurge_power_gmail:manifest",
    )


def _args(**kw):
    """Quick argparse.Namespace stand-in for handler tests."""
    import argparse

    return argparse.Namespace(**kw)


# ----------------------------- powers list -------------------------------


def test_powers_list_empty(monkeypatch, capsys):
    monkeypatch.setattr(
        "demiurge.cli_powers.discover", lambda kind: DiscoveryResult()
    )
    rc = cmd_powers_list(_args())
    out = capsys.readouterr().out
    assert rc == 0
    assert "no powers installed" in out


def test_powers_list_with_installed(monkeypatch, capsys):
    monkeypatch.setattr(
        "demiurge.cli_powers.discover",
        lambda kind: DiscoveryResult(plugins=[_gmail_plugin()]),
    )
    rc = cmd_powers_list(_args())
    out = capsys.readouterr().out
    assert rc == 0
    assert "Installed powers (1)" in out
    assert "gmail" in out
    assert "1.0.0" in out
    assert "webhook" in out
    assert "gmail.send" in out


def test_powers_list_with_broken_plugin(monkeypatch, capsys):
    monkeypatch.setattr(
        "demiurge.cli_powers.discover",
        lambda kind: DiscoveryResult(
            plugins=[_gmail_plugin()],
            errors=[
                DiscoveryError(
                    group="demiurge.powers",
                    name="broken",
                    dist_name="demiurge-power-broken",
                    entry_point_value="x:y",
                    error="ImportError: nope",
                )
            ],
        ),
    )
    rc = cmd_powers_list(_args())
    cap = capsys.readouterr()
    assert rc == 1  # broken plugin → non-zero exit
    assert "Installed powers" in cap.out
    assert "Broken plugins" in cap.err
    assert "broken" in cap.err
    assert "ImportError" in cap.err


# ----------------------------- powers registry ---------------------------


def test_powers_registry_renders(capsys):
    rc = cmd_powers_registry(_args())
    out = capsys.readouterr().out
    assert rc == 0
    assert "Known powers" in out
    # `channels_list.render()` produces a known shape; just check it's not empty.
    assert len(out) > 50


# ----------------------------- powers show -------------------------------


def test_powers_show_existing(monkeypatch, capsys):
    monkeypatch.setattr(
        "demiurge.cli_powers.discover",
        lambda kind: DiscoveryResult(plugins=[_gmail_plugin()]),
    )
    rc = cmd_powers_show(_args(name="gmail"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "power: gmail" in out
    assert "Gmail" in out  # display_name
    assert "1.0.0" in out
    assert "webhook, request-based" in out
    assert "gmail.send" in out
    assert "gmail.oauth_client.id" in out
    assert "demiurge wizard google" in out
    assert "demiurge-power-gmail" in out


def test_powers_show_unknown(monkeypatch, capsys):
    monkeypatch.setattr(
        "demiurge.cli_powers.discover", lambda kind: DiscoveryResult()
    )
    rc = cmd_powers_show(_args(name="nope"))
    err = capsys.readouterr().err
    assert rc == 1
    assert "no installed power" in err


def test_powers_show_broken_distinct_from_unknown(monkeypatch, capsys):
    monkeypatch.setattr(
        "demiurge.cli_powers.discover",
        lambda kind: DiscoveryResult(
            errors=[
                DiscoveryError(
                    group="demiurge.powers",
                    name="halfbroken",
                    dist_name="demiurge-power-halfbroken",
                    entry_point_value="x:y",
                    error="ImportError: nope",
                )
            ],
        ),
    )
    rc = cmd_powers_show(_args(name="halfbroken"))
    err = capsys.readouterr().err
    assert rc == 2  # different exit code from unknown
    assert "broken" in err


# ----------------------------- powers install ----------------------------


def test_powers_install_from_yaml(tmp_path: Path, monkeypatch, capsys):
    """Operator passes --from-yaml; we forge directly from the manifest file."""
    manifest_path = tmp_path / "plugin.yaml"
    manifest_path.write_text(GMAIL_POWER_YAML)

    captured = {}

    async def fake_forge_power(manifest, **kw):
        captured["manifest_name"] = manifest.name
        captured["kw"] = kw
        from demiurge.pantheon.hephaestus import ForgeResult

        return ForgeResult(creature_id=manifest.name, kind="power", notes=["fake forge"])

    monkeypatch.setattr("demiurge.cli_powers.forge_power", fake_forge_power, raising=False)
    # The function imports forge_power inline; need to patch the module.
    import demiurge.cli_powers as cli_powers_mod

    monkeypatch.setattr(cli_powers_mod, "discover", lambda kind: DiscoveryResult())
    # Patch through the late-bound import.
    import demiurge.pantheon.hephaestus as h

    monkeypatch.setattr(h, "forge_power", fake_forge_power)

    rc = cmd_powers_install(
        _args(
            name="gmail",
            from_yaml=str(manifest_path),
            repo_root=str(tmp_path / "repo"),
            target_dir=str(tmp_path / "units"),
            skip_bootstrap_hook=True,
        )
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert captured["manifest_name"] == "gmail"
    assert "Forged power 'gmail'" in out


def test_powers_install_from_yaml_name_mismatch(tmp_path: Path, capsys):
    manifest_path = tmp_path / "plugin.yaml"
    manifest_path.write_text(GMAIL_POWER_YAML)
    rc = cmd_powers_install(
        _args(
            name="not_gmail",
            from_yaml=str(manifest_path),
            repo_root=str(tmp_path),
            target_dir=str(tmp_path / "units"),
            skip_bootstrap_hook=True,
        )
    )
    err = capsys.readouterr().err
    assert rc == 2
    assert "manifest declares" in err


def test_powers_install_unknown_name(monkeypatch, capsys):
    monkeypatch.setattr(
        "demiurge.cli_powers.discover", lambda kind: DiscoveryResult()
    )
    rc = cmd_powers_install(
        _args(
            name="ghost",
            from_yaml=None,
            repo_root=None,
            target_dir=None,
            skip_bootstrap_hook=True,
        )
    )
    err = capsys.readouterr().err
    assert rc == 1
    assert "no power named 'ghost'" in err


def test_powers_install_broken_plugin_clear_error(monkeypatch, capsys):
    monkeypatch.setattr(
        "demiurge.cli_powers.discover",
        lambda kind: DiscoveryResult(
            errors=[
                DiscoveryError(
                    group="demiurge.powers",
                    name="halfbroken",
                    dist_name="demiurge-power-halfbroken",
                    entry_point_value="x:y",
                    error="ImportError: nope",
                )
            ],
        ),
    )
    rc = cmd_powers_install(
        _args(
            name="halfbroken",
            from_yaml=None,
            repo_root=None,
            target_dir=None,
            skip_bootstrap_hook=True,
        )
    )
    err = capsys.readouterr().err
    assert rc == 2
    assert "broken" in err
    assert "--from-yaml" in err  # operator gets the suggested workaround


# ----------------------------- powers uninstall --------------------------


def test_powers_uninstall(tmp_path: Path, monkeypatch, capsys):
    captured = {}

    def fake_archive_power(name, *, target_dir=None):
        captured["name"] = name
        captured["target_dir"] = target_dir
        from demiurge.pantheon.hades import ArchiveAction, ArchiveResult

        return ArchiveResult(
            creature_id=name,
            kind="power",
            actions=[ArchiveAction(verb="removed", description="systemd unit")],
        )

    import demiurge.pantheon.hades as hades_mod

    monkeypatch.setattr(hades_mod, "archive_power", fake_archive_power)

    rc = cmd_powers_uninstall(_args(name="gmail", target_dir=str(tmp_path / "units")))
    out = capsys.readouterr().out
    assert rc == 0
    assert captured["name"] == "gmail"
    assert "Archived power 'gmail'" in out


# ----------------------------- channels alias ----------------------------


def test_channels_list_alias_emits_deprecation(monkeypatch, capsys):
    monkeypatch.setattr(
        "demiurge.cli_powers.discover", lambda kind: DiscoveryResult()
    )
    rc = cmd_channels_list_deprecated(_args())
    cap = capsys.readouterr()
    assert rc == 0
    assert "DEPRECATION" in cap.err
    assert "demiurge powers list" in cap.err


# ----------------------------- top-level argparse wiring -----------------


def test_top_level_parser_has_powers_subcommand():
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["powers", "--help"])


def test_top_level_parser_powers_list():
    parser = cli.build_parser()
    args = parser.parse_args(["powers", "list"])
    assert args.cmd == "powers"
    assert args.subcmd == "list"
    assert args.fn is cmd_powers_list


def test_top_level_parser_powers_install_args():
    parser = cli.build_parser()
    args = parser.parse_args(
        [
            "powers",
            "install",
            "gmail",
            "--from-yaml",
            "/tmp/plugin.yaml",
            "--repo-root",
            "/tmp/repo",
            "--skip-bootstrap-hook",
        ]
    )
    assert args.name == "gmail"
    assert args.from_yaml == "/tmp/plugin.yaml"
    assert args.repo_root == "/tmp/repo"
    assert args.skip_bootstrap_hook is True


def test_top_level_parser_channels_list_alias_still_works():
    """Deprecated alias must still parse — operators may have it in scripts."""
    parser = cli.build_parser()
    args = parser.parse_args(["channels", "list"])
    assert args.cmd == "channels"
    assert args.fn is cmd_channels_list_deprecated
