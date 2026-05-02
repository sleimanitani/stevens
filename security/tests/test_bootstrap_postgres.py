"""Tests for demiurge.bootstrap.postgres — v0.10 step 2."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from demiurge.bootstrap import postgres as bp


# ----------------------------- platform ----------------------------------


def test_detect_platform_macos(monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")
    assert bp._detect_platform() == "macos"


def test_detect_platform_windows(monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")
    assert bp._detect_platform() == "windows"


def test_detect_platform_linux_debian(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("sys.platform", "linux")
    fake = tmp_path / "os-release"
    fake.write_text('NAME="Ubuntu"\nID=ubuntu\nID_LIKE=debian\n')
    with patch.object(bp, "Path", side_effect=lambda p: fake if p == "/etc/os-release" else Path(p)):
        assert bp._detect_platform() == "linux-debian"


def test_detect_platform_linux_other(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("sys.platform", "linux")
    fake = tmp_path / "os-release"
    fake.write_text('NAME="Arch Linux"\nID=arch\n')
    with patch.object(bp, "Path", side_effect=lambda p: fake if p == "/etc/os-release" else Path(p)):
        assert bp._detect_platform() == "linux-other"


# ----------------------------- detection helpers --------------------------


def test_psql_version_present(monkeypatch):
    monkeypatch.setattr(bp.shutil, "which", lambda c: "/usr/bin/psql")
    fake_run = MagicMock(return_value=MagicMock(stdout="psql (PostgreSQL) 16.13 (Ubuntu)"))
    monkeypatch.setattr(bp.subprocess, "run", fake_run)
    present, ver = bp._psql_version()
    assert present is True
    assert ver == "16.13"


def test_psql_version_absent(monkeypatch):
    monkeypatch.setattr(bp.shutil, "which", lambda c: None)
    present, ver = bp._psql_version()
    assert present is False
    assert ver is None


def test_psql_version_unparseable(monkeypatch):
    monkeypatch.setattr(bp.shutil, "which", lambda c: "/usr/bin/psql")
    fake_run = MagicMock(return_value=MagicMock(stdout="weird output"))
    monkeypatch.setattr(bp.subprocess, "run", fake_run)
    present, ver = bp._psql_version()
    assert present is True
    assert ver is None


def test_server_reachable_yes(monkeypatch):
    monkeypatch.setattr(bp.shutil, "which", lambda c: "/usr/bin/pg_isready")
    monkeypatch.setattr(
        bp.subprocess, "run", MagicMock(return_value=MagicMock(returncode=0))
    )
    assert bp._server_reachable() is True


def test_server_reachable_no_binary(monkeypatch):
    monkeypatch.setattr(bp.shutil, "which", lambda c: None)
    assert bp._server_reachable() is False


def test_server_reachable_nonzero(monkeypatch):
    monkeypatch.setattr(bp.shutil, "which", lambda c: "/usr/bin/pg_isready")
    monkeypatch.setattr(
        bp.subprocess, "run", MagicMock(return_value=MagicMock(returncode=2))
    )
    assert bp._server_reachable() is False


def test_pgvector_pkg_installed_skipped_on_macos():
    assert bp._pgvector_pkg_installed("macos") is None


def test_pgvector_pkg_installed_yes_on_debian(monkeypatch):
    monkeypatch.setattr(bp.shutil, "which", lambda c: "/usr/bin/dpkg-query")
    monkeypatch.setattr(
        bp.subprocess,
        "run",
        MagicMock(return_value=MagicMock(returncode=0, stdout="install ok installed")),
    )
    assert bp._pgvector_pkg_installed("linux-debian") is True


def test_pgvector_pkg_installed_no_on_debian(monkeypatch):
    monkeypatch.setattr(bp.shutil, "which", lambda c: "/usr/bin/dpkg-query")
    monkeypatch.setattr(
        bp.subprocess,
        "run",
        MagicMock(return_value=MagicMock(returncode=1, stdout="")),
    )
    assert bp._pgvector_pkg_installed("linux-debian") is False


# ----------------------------- detect() composition -----------------------


def test_detect_composes_when_server_down(monkeypatch):
    monkeypatch.setattr(bp, "_detect_platform", lambda: "linux-debian")
    monkeypatch.setattr(bp, "_psql_version", lambda: (False, None))
    monkeypatch.setattr(bp, "_server_reachable", lambda: False)
    monkeypatch.setattr(bp, "_pgvector_pkg_installed", lambda p: False)
    s = bp.detect()
    assert s.platform == "linux-debian"
    assert s.psql_present is False
    assert s.server_reachable is False
    assert s.peer_role_exists is None  # not probed when server down
    assert s.target_role_exists is None
    assert s.needs_install is True


def test_detect_composes_when_server_up(monkeypatch):
    monkeypatch.setattr(bp, "_detect_platform", lambda: "linux-debian")
    monkeypatch.setattr(bp, "_psql_version", lambda: (True, "16.13"))
    monkeypatch.setattr(bp, "_server_reachable", lambda: True)
    monkeypatch.setattr(bp, "_pgvector_pkg_installed", lambda p: True)
    monkeypatch.setattr(
        bp, "_probe_via_psycopg", lambda role, database: (True, True, True, True)
    )
    s = bp.detect()
    assert s.server_reachable is True
    assert s.peer_role_exists is True
    assert s.target_role_exists is True
    assert s.target_db_exists is True
    assert s.vector_extension_present is True
    assert s.needs_install is False
    assert s.needs_provisioning is False


def test_state_needs_provisioning_true_when_role_missing():
    s = bp.PostgresState(
        platform="linux-debian",
        psql_present=True,
        psql_version="16.13",
        server_reachable=True,
        pgvector_pkg_installed=True,
        peer_role_exists=True,
        target_role_exists=False,
        target_db_exists=False,
        vector_extension_present=False,
    )
    assert s.needs_install is False
    assert s.needs_provisioning is True


# ----------------------------- install_instructions -----------------------


def _state(**overrides) -> bp.PostgresState:
    base = dict(
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
    base.update(overrides)
    return bp.PostgresState(**base)


def test_install_instructions_none_when_already_ready():
    s = _state(server_reachable=True, peer_role_exists=True)
    assert bp.install_instructions(s) is None


def test_install_instructions_debian_full(monkeypatch):
    monkeypatch.setenv("USER", "alice")
    plan = bp.install_instructions(_state())
    assert plan is not None
    assert "apt-get install -y postgresql-16 postgresql-16-pgvector" in plan.sudo_block
    assert "PGDG" in plan.sudo_block or "apt.postgresql.org" in plan.sudo_block
    assert "createuser -s alice" in plan.sudo_block


def test_install_instructions_debian_only_grant_when_server_up(monkeypatch):
    monkeypatch.setenv("USER", "bob")
    s = _state(server_reachable=True, peer_role_exists=False)
    plan = bp.install_instructions(s)
    assert plan is not None
    assert "apt-get install" not in plan.sudo_block
    assert "createuser -s bob" in plan.sudo_block


def test_install_instructions_macos():
    s = _state(platform="macos")
    plan = bp.install_instructions(s)
    assert plan is not None
    assert "brew install postgresql@16 pgvector" in plan.sudo_block


def test_install_instructions_windows():
    s = _state(platform="windows")
    plan = bp.install_instructions(s)
    assert plan is not None
    assert "EnterpriseDB" in plan.sudo_block or "windows" in plan.sudo_block.lower()


def test_install_instructions_unknown_platform():
    s = _state(platform="haiku")
    plan = bp.install_instructions(s)
    assert plan is not None
    assert "haiku" in plan.sudo_block


# ----------------------------- write_env_file -----------------------------


def test_write_env_file_creates_new(tmp_path: Path):
    target = tmp_path / "demiurge" / "env"
    path, changed = bp.write_env_file(dsn="postgresql:///x", path=target)
    assert path == target
    assert changed is True
    text = target.read_text()
    assert text.strip() == "DATABASE_URL=postgresql:///x"
    assert (target.stat().st_mode & 0o777) == 0o600


def test_write_env_file_idempotent_same_value(tmp_path: Path):
    target = tmp_path / "env"
    target.write_text("DATABASE_URL=postgresql:///x\n")
    path, changed = bp.write_env_file(dsn="postgresql:///x", path=target)
    assert changed is False
    assert target.read_text() == "DATABASE_URL=postgresql:///x\n"


def test_write_env_file_replaces_existing_value(tmp_path: Path):
    target = tmp_path / "env"
    target.write_text("OTHER=keep\nDATABASE_URL=postgresql:///old\nALSO=keep2\n")
    path, changed = bp.write_env_file(dsn="postgresql:///new", path=target)
    assert changed is True
    text = target.read_text()
    assert "DATABASE_URL=postgresql:///new" in text
    assert "DATABASE_URL=postgresql:///old" not in text
    assert "OTHER=keep" in text
    assert "ALSO=keep2" in text


def test_env_file_path_uses_xdg(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert bp.env_file_path() == tmp_path / "demiurge" / "env"


def test_env_file_path_default(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    p = bp.env_file_path()
    assert p == tmp_path / ".config" / "demiurge" / "env"


# ----------------------------- format_state ------------------------------


def test_format_state_renders_all_fields():
    s = bp.PostgresState(
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
    out = bp.format_state(s)
    assert "linux-debian" in out
    assert "16.13" in out
    assert "vector extension" in out


def test_format_state_omits_unprobed_fields():
    s = _state()  # server_reachable=False → no probe data
    out = bp.format_state(s)
    assert "vector extension" not in out
    assert "assistant DB" not in out


# ----------------------------- main() CLI --------------------------------


def test_main_default_prints_state_and_instructions(monkeypatch, capsys):
    monkeypatch.setattr(bp, "detect", lambda **kw: _state())
    rc = bp.main([])
    out = capsys.readouterr().out
    assert rc == 1  # not-yet-actionable
    assert "Postgres detection" in out
    assert "apt-get install" in out


def test_main_default_already_ready(monkeypatch, capsys):
    monkeypatch.setattr(
        bp, "detect", lambda **kw: _state(server_reachable=True, peer_role_exists=True)
    )
    rc = bp.main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Postgres is ready" in out


def test_main_ensure_when_server_down(monkeypatch, capsys):
    monkeypatch.setattr(bp, "detect", lambda **kw: _state())
    rc = bp.main(["--ensure"])
    err_or_out = capsys.readouterr().out + capsys.readouterr().err
    assert rc == 2


def test_main_ensure_runs_provisioner(monkeypatch, capsys):
    monkeypatch.setattr(
        bp, "detect", lambda **kw: _state(server_reachable=True, peer_role_exists=True)
    )
    monkeypatch.setattr(
        bp, "ensure_role_and_database", lambda **kw: ["created role 'assistant'"]
    )
    rc = bp.main(["--ensure"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "created role 'assistant'" in out


def test_main_write_env(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.setattr(
        bp, "detect", lambda **kw: _state(server_reachable=True, peer_role_exists=True)
    )
    target = tmp_path / "env"
    monkeypatch.setattr(bp, "write_env_file", lambda **kw: (target, True))
    rc = bp.main(["--write-env"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "wrote" in out
    assert "DATABASE_URL=" in out


# ----------------------------- integration -------------------------------


@pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration test — requires real Postgres reachable as the OS user",
)
def test_ensure_role_and_database_idempotent_real():
    """Re-running on a host that already has assistant role/DB/vector is a no-op.

    Sol's dev box is in exactly this state after v0.10 step 1. We don't
    create a transient role here — that would require reasoning about
    cleanup if the test crashes. Just verify idempotency on the existing
    install.
    """
    actions1 = bp.ensure_role_and_database()
    actions2 = bp.ensure_role_and_database()
    assert actions2 == []  # second call must be a no-op
    # First call may or may not be empty depending on prior state, but if
    # it did anything, those changes must now be reflected:
    assert isinstance(actions1, list)
