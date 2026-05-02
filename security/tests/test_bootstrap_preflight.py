"""Tests for demiurge.bootstrap.preflight — v0.10 step 5.

Covers the shared docker-group detector and the back-compat re-export
on cli_bootstrap.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from demiurge.bootstrap import preflight


def test_in_docker_group_member(monkeypatch):
    monkeypatch.setenv("USER", "alice")
    fake = MagicMock(gr_mem=["alice", "bob"])
    monkeypatch.setattr(preflight.grp, "getgrnam", lambda name: fake)
    assert preflight.in_docker_group() is True


def test_in_docker_group_non_member(monkeypatch):
    monkeypatch.setenv("USER", "alice")
    fake = MagicMock(gr_mem=["bob", "carol"])
    monkeypatch.setattr(preflight.grp, "getgrnam", lambda name: fake)
    fake_pw = MagicMock(pw_gid=1000)
    monkeypatch.setattr("pwd.getpwnam", lambda name: fake_pw)
    fake_primary = MagicMock(gr_name="alice")
    monkeypatch.setattr(preflight.grp, "getgrgid", lambda gid: fake_primary)
    assert preflight.in_docker_group() is False


def test_in_docker_group_primary_match(monkeypatch):
    """Edge case: docker is the user's *primary* group (rare but possible)."""
    monkeypatch.setenv("USER", "alice")
    fake = MagicMock(gr_mem=[])  # not in supplementary
    monkeypatch.setattr(preflight.grp, "getgrnam", lambda name: fake)
    fake_pw = MagicMock(pw_gid=999)
    monkeypatch.setattr("pwd.getpwnam", lambda name: fake_pw)
    fake_primary = MagicMock(gr_name="docker")
    monkeypatch.setattr(preflight.grp, "getgrgid", lambda gid: fake_primary)
    assert preflight.in_docker_group() is True


def test_in_docker_group_no_docker_group(monkeypatch):
    monkeypatch.setenv("USER", "alice")
    def raise_keyerror(name):
        raise KeyError(name)
    monkeypatch.setattr(preflight.grp, "getgrnam", raise_keyerror)
    assert preflight.in_docker_group() is False


def test_in_docker_group_no_user_env(monkeypatch):
    monkeypatch.delenv("USER", raising=False)
    monkeypatch.delenv("LOGNAME", raising=False)
    assert preflight.in_docker_group() is False


def test_in_docker_group_explicit_user(monkeypatch):
    fake = MagicMock(gr_mem=["explicit-user"])
    monkeypatch.setattr(preflight.grp, "getgrnam", lambda name: fake)
    assert preflight.in_docker_group(user="explicit-user") is True


def test_docker_group_removal_hint():
    h = preflight.docker_group_removal_hint()
    assert "gpasswd -d" in h
    assert "docker" in h
    assert "newgrp" in h


def test_cli_bootstrap_back_compat_export(monkeypatch):
    """`cli_bootstrap._in_docker_group` still works (delegates to preflight)."""
    from demiurge.bootstrap import cli_bootstrap

    fake = MagicMock(gr_mem=["zoe"])
    monkeypatch.setattr(preflight.grp, "getgrnam", lambda name: fake)
    monkeypatch.setenv("USER", "zoe")
    assert cli_bootstrap._in_docker_group() is True
