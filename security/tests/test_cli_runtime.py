"""Tests for the `demiurge runtime` CLI — v0.11 step 7.4."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from demiurge import cli
from demiurge.cli_runtime import (
    cmd_runtime_reload,
    cmd_runtime_start,
    cmd_runtime_status,
    cmd_runtime_stop,
)


def _args(**kw):
    return argparse.Namespace(**kw)


# ----------------------------- start ------------------------------------


def test_runtime_start_prints_systemd_hint(capsys):
    rc = cmd_runtime_start(_args(foreground=False))
    out = capsys.readouterr().out
    assert rc == 0
    assert "systemctl --user start demiurge-runtime" in out
    assert "--foreground" in out


# ----------------------------- stop / status / reload (no daemon) -------


def test_runtime_stop_without_daemon(capsys, tmp_path: Path):
    rc = cmd_runtime_stop(_args(socket=str(tmp_path / "absent.sock"), timeout=1.0))
    err = capsys.readouterr().err
    assert rc == 1
    assert "not running" in err


def test_runtime_status_without_daemon(capsys, tmp_path: Path):
    rc = cmd_runtime_status(
        _args(socket=str(tmp_path / "absent.sock"), timeout=1.0, json=False)
    )
    err = capsys.readouterr().err
    assert rc == 1
    assert "not running" in err


def test_runtime_reload_without_daemon(capsys, tmp_path: Path):
    rc = cmd_runtime_reload(_args(socket=str(tmp_path / "absent.sock"), timeout=1.0))
    err = capsys.readouterr().err
    assert rc == 1
    assert "not running" in err


# ----------------------------- with mocked daemon (send_request) --------


def test_runtime_status_with_daemon(monkeypatch, capsys):
    """Mocked send_request returns canned status; CLI renders the table."""

    def fake_send(req, **kw):
        return {
            "ok": True,
            "data": {
                "running": True,
                "socket_path": "/tmp/x.sock",
                "processes": [
                    {
                        "name": "demiurge-power-gmail",
                        "desired_state": "running",
                        "is_running": True,
                        "pid": 12345,
                        "last_started_at": 1700000000.0,
                        "last_exited_at": None,
                        "last_exit_code": None,
                        "restart_count": 0,
                        "log_path": "/tmp/log.log",
                    }
                ],
            },
        }

    import demiurge.cli_runtime as cli_runtime_mod

    monkeypatch.setattr(cli_runtime_mod, "send_request", fake_send)
    rc = cmd_runtime_status(_args(socket=None, timeout=1.0, json=False))
    out = capsys.readouterr().out
    assert rc == 0
    assert "demiurge-power-gmail" in out
    assert "pid=12345" in out
    assert "state=running" in out


def test_runtime_status_json(monkeypatch, capsys):
    def fake_send(req, **kw):
        return {"ok": True, "data": {"processes": [], "running": True}}

    import demiurge.cli_runtime as cli_runtime_mod

    monkeypatch.setattr(cli_runtime_mod, "send_request", fake_send)
    rc = cmd_runtime_status(_args(socket=None, timeout=1.0, json=True))
    out = capsys.readouterr().out
    assert rc == 0
    parsed = json.loads(out)
    assert parsed["running"] is True


def test_runtime_stop_with_daemon(monkeypatch, capsys):
    def fake_send(req, **kw):
        assert req == {"op": "shutdown"}
        return {"ok": True, "data": {"shutdown": "requested"}}

    import demiurge.cli_runtime as cli_runtime_mod

    monkeypatch.setattr(cli_runtime_mod, "send_request", fake_send)
    rc = cmd_runtime_stop(_args(socket=None, timeout=1.0))
    out = capsys.readouterr().out
    assert rc == 0
    assert "shutdown requested" in out


def test_runtime_reload_with_daemon(monkeypatch, capsys):
    def fake_send(req, **kw):
        assert req == {"op": "reload"}
        return {
            "ok": True,
            "data": {"powers_registered": 3, "creatures_registered": 1},
        }

    import demiurge.cli_runtime as cli_runtime_mod

    monkeypatch.setattr(cli_runtime_mod, "send_request", fake_send)
    rc = cmd_runtime_reload(_args(socket=None, timeout=1.0))
    out = capsys.readouterr().out
    assert rc == 0
    assert "3 power(s)" in out
    assert "1 creature(s)" in out


def test_runtime_status_handles_daemon_error(monkeypatch, capsys):
    def fake_send(req, **kw):
        return {"ok": False, "error": "internal whoopsie"}

    import demiurge.cli_runtime as cli_runtime_mod

    monkeypatch.setattr(cli_runtime_mod, "send_request", fake_send)
    rc = cmd_runtime_status(_args(socket=None, timeout=1.0, json=False))
    err = capsys.readouterr().err
    assert rc == 1
    assert "whoopsie" in err


# ----------------------------- top-level argparse ------------------------


def test_top_level_runtime_subcommand_exists():
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["runtime", "--help"])


def test_top_level_runtime_status_dispatches():
    parser = cli.build_parser()
    args = parser.parse_args(["runtime", "status"])
    assert args.cmd == "runtime"
    assert args.subcmd == "status"
    assert args.fn is cmd_runtime_status


def test_top_level_runtime_start_foreground_flag():
    parser = cli.build_parser()
    args = parser.parse_args(["runtime", "start", "--foreground"])
    assert args.foreground is True


def test_top_level_runtime_status_json_flag():
    parser = cli.build_parser()
    args = parser.parse_args(["runtime", "status", "--json"])
    assert args.json is True
