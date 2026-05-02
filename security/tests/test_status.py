"""Tests for demiurge.status."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from demiurge import audit_tail, status
from demiurge.sealed_store import initialize_store


def test_status_empty_install(tmp_path: Path) -> None:
    out = status.render_status(
        secrets_root=tmp_path / "vault",
        socket_path=str(tmp_path / "missing.sock"),
        agents_yaml=tmp_path / "agents.yaml",
        audit_dir=tmp_path / "audit",
    )
    assert "not initialized" in out
    assert "Enkidu:          not running" in out
    assert "(none registered)" in out


def test_status_running_install(tmp_path: Path) -> None:
    initialize_store(tmp_path / "vault", b"x")
    sock = tmp_path / "sock"
    sock.touch()
    (tmp_path / "agents.yaml").write_text(
        yaml.safe_dump({"agents": [{"name": "email_pm", "pubkey_b64": "x"}]})
    )
    audit_dir = tmp_path / "audit"
    log = audit_dir / f"{audit_tail.today_utc()}.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        json.dumps(
            {
                "ts": "2026-04-29T00:00:01Z",
                "trace_id": "1",
                "outcome": "ok",
                "latency_ms": 7,
                "caller": "email_pm",
                "capability": "ping",
            }
        )
        + "\n"
    )

    out = status.render_status(
        secrets_root=tmp_path / "vault",
        socket_path=str(sock),
        agents_yaml=tmp_path / "agents.yaml",
        audit_dir=audit_dir,
    )
    assert "initialized" in out
    assert "socket present" in out
    assert "email_pm" in out
    assert "ping" in out  # last audit line surfaces in status
