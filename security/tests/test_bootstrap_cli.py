"""Tests for demiurge.bootstrap.cli_bootstrap — v0.10 step 4."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from demiurge.bootstrap import cli_bootstrap as cb
from demiurge.bootstrap import postgres as bp


# ----------------------------- preflight ---------------------------------


def test_preflight_passes_on_normal_box(monkeypatch):
    monkeypatch.setattr(cb, "_in_docker_group", lambda user=None: False)
    monkeypatch.setattr(cb.shutil, "which", lambda c: "/usr/bin/uv")
    monkeypatch.setattr(sys, "platform", "linux")
    r = cb.preflight()
    assert r.ok
    assert r.failures == []


def test_preflight_fails_when_uv_missing(monkeypatch):
    monkeypatch.setattr(cb, "_in_docker_group", lambda user=None: False)
    monkeypatch.setattr(cb.shutil, "which", lambda c: None)
    r = cb.preflight()
    assert not r.ok
    assert any("uv" in f for f in r.failures)


def test_preflight_hard_fails_in_docker_group(monkeypatch):
    monkeypatch.setattr(cb.shutil, "which", lambda c: "/usr/bin/uv")
    monkeypatch.setattr(cb, "_in_docker_group", lambda user=None: True)
    r = cb.preflight()
    assert not r.ok
    msg = " ".join(r.failures).lower()
    assert "docker" in msg
    assert "passwordless root" in msg or "gpasswd" in msg


def test_preflight_warns_on_non_linux(monkeypatch):
    monkeypatch.setattr(cb, "_in_docker_group", lambda user=None: False)
    monkeypatch.setattr(cb.shutil, "which", lambda c: "/usr/bin/uv")
    monkeypatch.setattr(sys, "platform", "darwin")
    r = cb.preflight()
    assert r.ok  # warning, not failure
    assert any("darwin" in w or "Linux" in w for w in r.warnings)


# Note: docker-group detection logic itself is tested in
# test_bootstrap_preflight.py (the shared module). Here we only assert
# that cli_bootstrap delegates to it.


# ----------------------------- run_bootstrap -----------------------------


def _ready_state() -> bp.PostgresState:
    return bp.PostgresState(
        platform="linux-debian",
        psql_present=True,
        psql_version="16.13",
        server_reachable=True,
        pgvector_pkg_installed=True,
        peer_role_exists=True,
        target_role_exists=True,
        target_db_exists=True,
        vector_extension_present=True,
    )


def _missing_state() -> bp.PostgresState:
    return bp.PostgresState(
        platform="linux-debian",
        psql_present=False,
        psql_version=None,
        server_reachable=False,
        pgvector_pkg_installed=False,
        peer_role_exists=None,
        target_role_exists=None,
        target_db_exists=None,
        vector_extension_present=None,
    )


def _passing_preflight():
    return cb.PreflightResult(failures=[], warnings=[])


def test_run_bootstrap_preflight_failure(monkeypatch, capsys):
    monkeypatch.setattr(
        cb,
        "preflight",
        lambda: cb.PreflightResult(failures=["python too old"]),
    )
    rc = cb.run_bootstrap()
    out = capsys.readouterr().out
    assert rc == 2
    assert "preflight failed" in out
    assert "python too old" in out


def test_run_bootstrap_postgres_missing_returns_1(monkeypatch, capsys):
    monkeypatch.setattr(cb, "preflight", _passing_preflight)
    monkeypatch.setattr(cb.postgres, "detect", lambda **kw: _missing_state())
    rc = cb.run_bootstrap()
    out = capsys.readouterr().out
    assert rc == 1
    assert "apt-get install" in out
    assert "bootstrap paused" in out


def test_run_bootstrap_dry_run_full_path(monkeypatch, capsys, tmp_path: Path):
    monkeypatch.setattr(cb, "preflight", _passing_preflight)
    monkeypatch.setattr(cb.postgres, "detect", lambda **kw: _ready_state())
    rc = cb.run_bootstrap(dry_run=True, repo_root=tmp_path)
    out = capsys.readouterr().out
    assert rc == 0
    assert "(dry-run)" in out
    assert "would call ensure_role_and_database" in out
    assert "would apply migrations" in out
    assert "would write DATABASE_URL" in out
    assert "next steps" in out


def test_run_bootstrap_real_path(monkeypatch, capsys, tmp_path: Path):
    monkeypatch.setattr(cb, "preflight", _passing_preflight)
    monkeypatch.setattr(cb.postgres, "detect", lambda **kw: _ready_state())
    monkeypatch.setattr(
        cb.postgres,
        "ensure_role_and_database",
        lambda **kw: ["created role 'assistant'"],
    )
    monkeypatch.setattr(cb.migrate, "apply_migrations", lambda dsn, mig_dir: 9)
    monkeypatch.setattr(
        cb.migrate, "_resolve_migrations_dir", lambda arg: tmp_path / "mig"
    )
    monkeypatch.setattr(
        cb.postgres, "write_env_file", lambda dsn: (tmp_path / "env", True)
    )
    monkeypatch.setattr(
        cb.systemd,
        "write_units",
        lambda repo_root: [(tmp_path / "u" / "demiurge-security.service", "created")],
    )
    monkeypatch.setattr(cb.systemd, "format_actions", lambda actions: "  + demiurge-security.service: created")
    monkeypatch.setattr(cb.systemd, "reload_user_daemon", lambda: True)
    monkeypatch.setattr(cb.systemd, "is_lingering", lambda: True)
    monkeypatch.setattr(sys, "platform", "linux")

    rc = cb.run_bootstrap(repo_root=tmp_path)
    out = capsys.readouterr().out
    assert rc == 0
    assert "created role 'assistant'" in out
    assert "applied 9 migration" in out
    assert "demiurge-security.service" in out
    assert "daemon-reload" in out
    # When linger=True, no enable-linger hint:
    assert "enable-linger" not in out
    assert "demiurge secrets init" in out


def test_run_bootstrap_real_path_prints_linger_hint(monkeypatch, capsys, tmp_path: Path):
    monkeypatch.setattr(cb, "preflight", _passing_preflight)
    monkeypatch.setattr(cb.postgres, "detect", lambda **kw: _ready_state())
    monkeypatch.setattr(cb.postgres, "ensure_role_and_database", lambda **kw: [])
    monkeypatch.setattr(cb.migrate, "apply_migrations", lambda dsn, mig_dir: 9)
    monkeypatch.setattr(
        cb.migrate, "_resolve_migrations_dir", lambda arg: tmp_path / "mig"
    )
    monkeypatch.setattr(
        cb.postgres, "write_env_file", lambda dsn: (tmp_path / "env", False)
    )
    monkeypatch.setattr(
        cb.systemd, "write_units", lambda repo_root: []
    )
    monkeypatch.setattr(cb.systemd, "format_actions", lambda actions: "")
    monkeypatch.setattr(cb.systemd, "reload_user_daemon", lambda: True)
    monkeypatch.setattr(cb.systemd, "is_lingering", lambda: False)
    monkeypatch.setattr(cb.systemd, "enable_linger_command", lambda: "sudo loginctl enable-linger eve")
    monkeypatch.setattr(sys, "platform", "linux")
    rc = cb.run_bootstrap(repo_root=tmp_path)
    out = capsys.readouterr().out
    assert rc == 0
    assert "loginctl enable-linger eve" in out


def test_run_bootstrap_skips_systemd_on_macos(monkeypatch, capsys, tmp_path: Path):
    monkeypatch.setattr(cb, "preflight", _passing_preflight)
    monkeypatch.setattr(cb.postgres, "detect", lambda **kw: _ready_state())
    monkeypatch.setattr(cb.postgres, "ensure_role_and_database", lambda **kw: [])
    monkeypatch.setattr(cb.migrate, "apply_migrations", lambda dsn, mig_dir: 9)
    monkeypatch.setattr(
        cb.migrate, "_resolve_migrations_dir", lambda arg: tmp_path / "mig"
    )
    monkeypatch.setattr(
        cb.postgres, "write_env_file", lambda dsn: (tmp_path / "env", True)
    )
    monkeypatch.setattr(sys, "platform", "darwin")
    rc = cb.run_bootstrap(repo_root=tmp_path)
    out = capsys.readouterr().out
    assert rc == 0
    assert "non-Linux" in out


# ----------------------------- top-level CLI -----------------------------


def test_top_level_stevens_bootstrap_in_help(capsys):
    """`demiurge bootstrap` is registered as a top-level subcommand."""
    from demiurge.cli import build_parser

    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["bootstrap", "--help"])
    out = capsys.readouterr().out
    assert "preflight" in out.lower() or "first-time setup" in out.lower()
    assert "--dry-run" in out


def test_top_level_dispatches_to_run_bootstrap(monkeypatch):
    from demiurge import cli

    captured = {}

    def fake_run(*, dry_run, repo_root):
        captured["dry_run"] = dry_run
        captured["repo_root"] = repo_root
        return 0

    monkeypatch.setattr(
        "demiurge.bootstrap.cli_bootstrap.run_bootstrap", fake_run
    )
    rc = cli.main(["bootstrap", "--dry-run"])
    assert rc == 0
    assert captured == {"dry_run": True, "repo_root": None}
