"""Tests for demiurge.reset — local-state wipe for fresh-install testing."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from demiurge.reset import (
    ResetPlan,
    build_plan,
    execute_plan,
    post_wipe_next_steps,
)


# --- build_plan ---


def test_build_plan_default_includes_everything(monkeypatch):
    monkeypatch.delenv("DEMIURGE_SECURITY_SECRETS", raising=False)
    monkeypatch.delenv("DEMIURGE_SECURITY_AUDIT_DIR", raising=False)
    plan = build_plan()
    assert plan.sealed_store_dir is not None
    assert plan.audit_dir is not None
    assert plan.agents_config_dir is not None
    assert plan.janus_profile_dir is not None
    assert plan.keyring_entry is True
    assert plan.pdf_corpus_dir is not None
    assert "channel_accounts" in plan.postgres_tables
    assert "standing_approvals" in plan.postgres_tables


def test_build_plan_keep_flags():
    plan = build_plan(
        keep_sealed=True, keep_audit=True, keep_agents=True,
        keep_janus_profile=True, keep_keyring=True,
        keep_postgres=True, keep_pdf_corpus=True,
    )
    assert plan.sealed_store_dir is None
    assert plan.audit_dir is None
    assert plan.agents_config_dir is None
    assert plan.janus_profile_dir is None
    assert plan.keyring_entry is False
    assert plan.pdf_corpus_dir is None
    assert plan.postgres_tables == []


def test_build_plan_partial_keep():
    plan = build_plan(keep_postgres=True)
    assert plan.sealed_store_dir is not None
    assert plan.postgres_tables == []


def test_build_plan_uses_env_overrides(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("DEMIURGE_SECURITY_SECRETS", str(tmp_path / "vault"))
    monkeypatch.setenv("DEMIURGE_SECURITY_AUDIT_DIR", str(tmp_path / "audit"))
    plan = build_plan()
    assert plan.sealed_store_dir == tmp_path / "vault"
    assert plan.audit_dir == tmp_path / "audit"


# --- render() ---


def test_render_includes_all_sections(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("DEMIURGE_SECURITY_SECRETS", str(tmp_path / "v"))
    rendered = build_plan().render()
    assert "sealed store dir" in rendered
    assert "audit log dir" in rendered
    assert "agent profiles" in rendered
    assert "Janus profile" in rendered
    assert "OS keyring entry" in rendered
    assert "Postgres tables" in rendered
    assert "channel_accounts" in rendered
    assert "NOT touched" in rendered


def test_render_marks_existence(tmp_path: Path):
    plan = ResetPlan(sealed_store_dir=tmp_path / "exists")
    (tmp_path / "exists").mkdir()
    out = plan.render()
    assert "[exists]" in out

    plan = ResetPlan(sealed_store_dir=tmp_path / "absent")
    out = plan.render()
    assert "[absent]" in out


# --- execute_plan ---


@pytest.mark.asyncio
async def test_execute_wipes_existing_dirs(tmp_path: Path):
    sealed = tmp_path / "vault"
    audit = tmp_path / "audit"
    sealed.mkdir()
    (sealed / "x.bin").write_bytes(b"secret")
    audit.mkdir()
    (audit / "today.jsonl").write_text("[]")

    plan = ResetPlan(
        sealed_store_dir=sealed,
        audit_dir=audit,
        keyring_entry=False,    # avoid touching real keyring in tests
        postgres_tables=[],     # avoid Postgres dep in tests
    )
    results = await execute_plan(plan)
    assert not sealed.exists()
    assert not audit.exists()
    assert any("wiped" in r and "vault" in r for r in results)
    assert any("wiped" in r and "audit" in r for r in results)


@pytest.mark.asyncio
async def test_execute_skips_absent_dirs(tmp_path: Path):
    plan = ResetPlan(
        sealed_store_dir=tmp_path / "absent_vault",
        keyring_entry=False,
        postgres_tables=[],
    )
    results = await execute_plan(plan)
    assert any("skipped (absent)" in r for r in results)


@pytest.mark.asyncio
async def test_execute_clears_keyring(monkeypatch):
    cleared = {"called": False}

    def fake_clear():
        cleared["called"] = True

    import demiurge.keyring_passphrase as kp

    monkeypatch.setattr(kp, "clear", fake_clear)
    plan = ResetPlan(keyring_entry=True, postgres_tables=[])
    results = await execute_plan(plan)
    assert cleared["called"]
    assert any("keyring" in r.lower() for r in results)


@pytest.mark.asyncio
async def test_execute_postgres_failure_doesnt_crash(monkeypatch):
    """No DATABASE_URL → graceful skip, not exception."""
    plan = ResetPlan(
        sealed_store_dir=None, audit_dir=None,
        agents_config_dir=None, janus_profile_dir=None,
        keyring_entry=False,
        pdf_corpus_dir=None,
        postgres_tables=["channel_accounts"],
    )
    monkeypatch.delenv("DATABASE_URL", raising=False)
    results = await execute_plan(plan)
    assert any("Postgres wipe skipped" in r for r in results)


# --- post_wipe_next_steps ---


def test_post_wipe_next_steps_includes_secrets_init():
    s = post_wipe_next_steps()
    assert "demiurge secrets init" in s
    assert "demiurge onboard gmail" in s
