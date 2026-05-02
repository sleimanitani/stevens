"""Tests for stevens_security.bootstrap.systemd — v0.10 step 3."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from stevens_security.bootstrap import systemd as bs


# ----------------------------- catalog ------------------------------------


def test_catalog_contains_security_first():
    """Security must appear first so the dependency graph is consistent."""
    assert bs.DEFAULT_SERVICES[0].name == "stevens-security"


def test_every_non_security_service_lists_security_in_after():
    """Adapter/runtime units must wait for the security UDS to exist."""
    for s in bs.DEFAULT_SERVICES:
        if s.name == "stevens-security":
            continue
        assert bs.SECURITY_UNIT in s.after, f"{s.name} doesn't depend on security"


def test_unit_names_have_stevens_prefix():
    for s in bs.DEFAULT_SERVICES:
        assert s.name.startswith("stevens-"), s.name


def test_no_whatsapp_web_node_service():
    """Whatsapp Web (Node.js) is excluded from v0.10 systemd units —
    it'll move to a v0.11 plugin."""
    for s in bs.DEFAULT_SERVICES:
        assert "whatsapp-adapter" not in s.name or "cloud" in s.name


# ----------------------------- render_unit -------------------------------


def test_render_unit_includes_required_blocks(tmp_path: Path):
    s = bs.ServiceUnit(
        name="stevens-foo",
        description="Foo",
        exec_cmd="python -m foo",
        after=("stevens-security.service",),
    )
    text = bs.render_unit(s, repo_root=tmp_path, env_file=tmp_path / "env")
    assert "[Unit]" in text
    assert "[Service]" in text
    assert "[Install]" in text
    assert "Description=Foo" in text
    assert "After=stevens-security.service" in text
    assert f"WorkingDirectory={tmp_path}" in text
    assert f"EnvironmentFile=-{tmp_path / 'env'}" in text
    assert f"ExecStart=uv run --directory {tmp_path} python -m foo" in text
    assert "Restart=on-failure" in text
    assert "WantedBy=default.target" in text


def test_render_unit_no_after_omits_after_line(tmp_path: Path):
    s = bs.ServiceUnit(
        name="stevens-x", description="x", exec_cmd="x"
    )
    text = bs.render_unit(s, repo_root=tmp_path, env_file=tmp_path / "env")
    assert "After=" not in text


def test_render_unit_extra_env(tmp_path: Path):
    s = bs.ServiceUnit(
        name="stevens-x",
        description="x",
        exec_cmd="x",
        extra_env=(("STEVENS_CALLER_NAME", "alice"), ("X", "y")),
    )
    text = bs.render_unit(s, repo_root=tmp_path, env_file=tmp_path / "env")
    assert 'Environment="STEVENS_CALLER_NAME=alice"' in text
    assert 'Environment="X=y"' in text


def test_render_unit_environment_file_optional_dash(tmp_path: Path):
    """Leading '-' on EnvironmentFile= makes systemd not error if missing."""
    s = bs.ServiceUnit(name="stevens-x", description="x", exec_cmd="x")
    text = bs.render_unit(s, repo_root=tmp_path, env_file=tmp_path / "absent")
    assert "EnvironmentFile=-" in text


# ----------------------------- write_units --------------------------------


def test_write_units_creates(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(sys, "platform", "linux")
    target = tmp_path / "units"
    repo = tmp_path / "repo"
    repo.mkdir()
    actions = bs.write_units(
        repo_root=repo,
        target_dir=target,
        env_file=tmp_path / "env",
    )
    assert all(verb == "created" for _, verb in actions)
    assert len(actions) == len(bs.DEFAULT_SERVICES)
    for path, _ in actions:
        assert path.exists()
        assert path.parent == target


def test_write_units_idempotent(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(sys, "platform", "linux")
    target = tmp_path / "units"
    repo = tmp_path / "repo"
    repo.mkdir()
    bs.write_units(repo_root=repo, target_dir=target, env_file=tmp_path / "env")
    actions = bs.write_units(
        repo_root=repo, target_dir=target, env_file=tmp_path / "env"
    )
    assert all(verb == "unchanged" for _, verb in actions)


def test_write_units_updates_when_content_changes(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(sys, "platform", "linux")
    target = tmp_path / "units"
    repo1 = tmp_path / "repo1"
    repo1.mkdir()
    bs.write_units(repo_root=repo1, target_dir=target, env_file=tmp_path / "env")
    repo2 = tmp_path / "repo2"
    repo2.mkdir()
    actions = bs.write_units(
        repo_root=repo2, target_dir=target, env_file=tmp_path / "env"
    )
    assert all(verb == "updated" for _, verb in actions)


def test_write_units_subset(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(sys, "platform", "linux")
    one = (bs.DEFAULT_SERVICES[0],)
    target = tmp_path / "units"
    actions = bs.write_units(
        repo_root=tmp_path, target_dir=target, env_file=tmp_path / "env", services=one
    )
    assert len(actions) == 1
    assert actions[0][0].name == "stevens-security.service"


def test_write_units_macos_raises(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(sys, "platform", "darwin")
    with pytest.raises(NotImplementedError, match="launchd"):
        bs.write_units(repo_root=tmp_path, target_dir=tmp_path / "u", env_file=tmp_path / "e")


def test_write_units_windows_raises(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(NotImplementedError, match="scheduled tasks"):
        bs.write_units(repo_root=tmp_path, target_dir=tmp_path / "u", env_file=tmp_path / "e")


# ----------------------------- linger ------------------------------------


def test_is_lingering_yes(monkeypatch):
    monkeypatch.setenv("USER", "alice")
    monkeypatch.setattr(bs.shutil, "which", lambda c: "/usr/bin/loginctl")
    monkeypatch.setattr(
        bs.subprocess,
        "run",
        MagicMock(return_value=MagicMock(stdout="Linger=yes\n", returncode=0)),
    )
    assert bs.is_lingering() is True


def test_is_lingering_no(monkeypatch):
    monkeypatch.setenv("USER", "alice")
    monkeypatch.setattr(bs.shutil, "which", lambda c: "/usr/bin/loginctl")
    monkeypatch.setattr(
        bs.subprocess,
        "run",
        MagicMock(return_value=MagicMock(stdout="Linger=no\n", returncode=0)),
    )
    assert bs.is_lingering() is False


def test_is_lingering_no_loginctl(monkeypatch):
    monkeypatch.setenv("USER", "alice")
    monkeypatch.setattr(bs.shutil, "which", lambda c: None)
    assert bs.is_lingering() is False


def test_is_lingering_no_user_env(monkeypatch):
    monkeypatch.delenv("USER", raising=False)
    monkeypatch.delenv("LOGNAME", raising=False)
    monkeypatch.setattr(bs.shutil, "which", lambda c: "/usr/bin/loginctl")
    assert bs.is_lingering() is False


def test_enable_linger_command_uses_user(monkeypatch):
    monkeypatch.setenv("USER", "carol")
    assert bs.enable_linger_command() == "sudo loginctl enable-linger carol"


def test_reload_user_daemon_no_systemctl(monkeypatch):
    monkeypatch.setattr(bs.shutil, "which", lambda c: None)
    assert bs.reload_user_daemon() is False


def test_reload_user_daemon_ok(monkeypatch):
    monkeypatch.setattr(bs.shutil, "which", lambda c: "/usr/bin/systemctl")
    monkeypatch.setattr(
        bs.subprocess, "run", MagicMock(return_value=MagicMock(returncode=0))
    )
    assert bs.reload_user_daemon() is True


# ----------------------------- format ------------------------------------


def test_format_actions(tmp_path: Path):
    out = bs.format_actions(
        [
            (tmp_path / "a.service", "created"),
            (tmp_path / "b.service", "unchanged"),
            (tmp_path / "c.service", "updated"),
        ]
    )
    assert "+ a.service" in out
    assert "· b.service" in out
    assert "~ c.service" in out


# ----------------------------- main() CLI --------------------------------


def test_main_dryrun(monkeypatch, capsys, tmp_path: Path):
    monkeypatch.setattr(sys, "platform", "linux")
    rc = bs.main(["--repo-root", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Dry-run" in out
    assert "stevens-security.service" in out
    for s in bs.DEFAULT_SERVICES:
        assert s.name in out


def test_main_write(monkeypatch, capsys, tmp_path: Path):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(bs, "reload_user_daemon", lambda: True)
    monkeypatch.setattr(bs, "is_lingering", lambda: True)
    rc = bs.main(
        [
            "--write",
            "--repo-root",
            str(tmp_path),
            "--target-dir",
            str(tmp_path / "u"),
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "daemon-reload: ok" in out
    assert (tmp_path / "u" / "stevens-security.service").exists()
    # When linger is True, no enable-linger hint.
    assert "enable-linger" not in out


def test_main_write_prints_linger_hint(monkeypatch, capsys, tmp_path: Path):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(bs, "reload_user_daemon", lambda: True)
    monkeypatch.setattr(bs, "is_lingering", lambda: False)
    monkeypatch.setenv("USER", "dave")
    rc = bs.main(
        [
            "--write",
            "--repo-root",
            str(tmp_path),
            "--target-dir",
            str(tmp_path / "u"),
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "loginctl enable-linger dave" in out


def test_main_non_linux_returns_2(monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "darwin")
    rc = bs.main([])
    err = capsys.readouterr().err
    assert rc == 2
    assert "Linux-only" in err
