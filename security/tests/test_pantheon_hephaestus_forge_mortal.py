"""Tests for forge_mortal — v0.11 step 3e.1."""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml

from demiurge.pantheon.hephaestus import (
    ForgeError,
    ForgeResult,
    forge_mortal,
)
from demiurge.pantheon.hephaestus.gods import EnkiduGod
from demiurge.policy import load_policy
from shared.creatures.feed import ObservationFeed
from shared.creatures.tools import Blessing, GodlyBlessing, ToolRequest
from shared.plugins.manifest import load_manifest_from_text


# ----------------------------- manifest fixtures -------------------------


EMAIL_PM_MORTAL = """\
name: email_pm
kind: mortal
display_name: Email PM
version: "1.0.0"
capabilities:
  - gmail.send
  - gmail.read
powers:
  - gmail
"""


EMAIL_PM_NO_BOOTSTRAP = EMAIL_PM_MORTAL  # Mortals don't require bootstrap


GMAIL_POWER = """\
name: gmail
kind: power
display_name: Gmail
version: "1.0.0"
modes: [request-based]
capabilities: []
bootstrap: gmail_adapter.bootstrap:install
"""


# ----------------------------- helpers -----------------------------------


def _setup_workspace(tmp_path: Path) -> dict[str, Path]:
    workspace = {
        "agents_yaml": tmp_path / "agents.yaml",
        "capabilities_yaml": tmp_path / "capabilities.yaml",
        "agents_dir": tmp_path / "agents",
        "feed_base": tmp_path / "feeds",
        "repo_root": tmp_path / "repo",
    }
    workspace["agents_dir"].mkdir(parents=True, exist_ok=True)
    workspace["repo_root"].mkdir(parents=True, exist_ok=True)
    return workspace


async def _fake_dispatcher(ctx, *, capability, blessing, **kwargs):
    return f"dispatched: {capability}"


# ----------------------------- kind validation ---------------------------


def test_forge_mortal_rejects_power_manifest(tmp_path: Path):
    ws = _setup_workspace(tmp_path)
    m = load_manifest_from_text(GMAIL_POWER)
    with pytest.raises(ForgeError, match="kind='mortal'"):
        asyncio.run(
            forge_mortal(
                m,
                instance_id="personal",
                repo_root=ws["repo_root"],
                agents_yaml=ws["agents_yaml"],
                capabilities_yaml=ws["capabilities_yaml"],
                agents_dir=ws["agents_dir"],
                feed_base=ws["feed_base"],
                gods={},
                dispatchers={},
            )
        )


def test_forge_mortal_rejects_invalid_instance_id(tmp_path: Path):
    ws = _setup_workspace(tmp_path)
    m = load_manifest_from_text(EMAIL_PM_MORTAL)
    with pytest.raises(ForgeError, match="instance_id"):
        asyncio.run(
            forge_mortal(
                m,
                instance_id="UpperCase",  # invalid
                repo_root=ws["repo_root"],
                agents_yaml=ws["agents_yaml"],
                capabilities_yaml=ws["capabilities_yaml"],
                agents_dir=ws["agents_dir"],
                feed_base=ws["feed_base"],
                gods={"enkidu": EnkiduGod(policy=load_policy(ws["capabilities_yaml"]))},
                dispatchers={"enkidu": _fake_dispatcher},
            )
        )


def test_forge_mortal_requires_gods_and_dispatchers(tmp_path: Path):
    """Failing loud is better than auto-building defaults that might
    not match the live deployment."""
    ws = _setup_workspace(tmp_path)
    m = load_manifest_from_text(EMAIL_PM_MORTAL)
    with pytest.raises(ForgeError, match="gods.*dispatchers"):
        asyncio.run(
            forge_mortal(
                m,
                instance_id="personal",
                repo_root=ws["repo_root"],
                agents_yaml=ws["agents_yaml"],
                capabilities_yaml=ws["capabilities_yaml"],
                agents_dir=ws["agents_dir"],
                feed_base=ws["feed_base"],
                # gods=None, dispatchers=None  → ForgeError
            )
        )


# ----------------------------- end-to-end forge --------------------------


def test_forge_mortal_end_to_end(tmp_path: Path):
    ws = _setup_workspace(tmp_path)
    m = load_manifest_from_text(EMAIL_PM_MORTAL)

    # Forge picks up the policy block we'll write below.
    result = asyncio.run(
        forge_mortal(
            m,
            instance_id="personal",
            repo_root=ws["repo_root"],
            agents_yaml=ws["agents_yaml"],
            capabilities_yaml=ws["capabilities_yaml"],
            agents_dir=ws["agents_dir"],
            feed_base=ws["feed_base"],
            gods={
                "enkidu": EnkiduGod(policy=load_policy(ws["capabilities_yaml"])),
            },
            dispatchers={"enkidu": _fake_dispatcher},
            create_pg_schema=False,  # no real DB needed for this test
            skip_bootstrap_hook=True,
        )
    )

    assert isinstance(result, ForgeResult)
    assert result.kind == "mortal"
    assert result.creature_id == "email_pm.personal"
    assert result.policy_written is True

    # Agent identity provisioned.
    assert result.agent_key_path is not None
    assert result.agent_key_path.exists()
    assert result.agent_key_path.stat().st_mode & 0o777 == 0o600

    # Policy block written to capabilities.yaml.
    policy_data = yaml.safe_load(ws["capabilities_yaml"].read_text())
    agent_entry = next(
        a for a in policy_data["agents"] if a["name"] == "email_pm.personal"
    )
    assert {r["capability"] for r in agent_entry["allow"]} == {
        "gmail.send",
        "gmail.read",
    }

    # agents.yaml has the new identity.
    agents_data = yaml.safe_load(ws["agents_yaml"].read_text())
    names = {a["name"] for a in agents_data["agents"]}
    assert "email_pm.personal" in names

    # ToolRegistry has the blessed tools + universal tools.
    assert result.registry is not None
    assert "gmail.send" in result.registry.names()
    assert "gmail.read" in result.registry.names()
    assert "think" in result.registry.names()
    assert "mortal.return" in result.registry.names()

    # Observation feed exists at the right path.
    assert result.feed_path is not None
    assert result.feed_path.exists()
    assert result.feed_path.parent.name == "email_pm.personal"


def test_forge_mortal_creature_id_format(tmp_path: Path):
    """creature_id is `<manifest>.<instance>` deterministically."""
    ws = _setup_workspace(tmp_path)
    m = load_manifest_from_text(EMAIL_PM_MORTAL)
    result = asyncio.run(
        forge_mortal(
            m,
            instance_id="work",
            repo_root=ws["repo_root"],
            agents_yaml=ws["agents_yaml"],
            capabilities_yaml=ws["capabilities_yaml"],
            agents_dir=ws["agents_dir"],
            feed_base=ws["feed_base"],
            gods={"enkidu": EnkiduGod(policy=load_policy(ws["capabilities_yaml"]))},
            dispatchers={"enkidu": _fake_dispatcher},
            create_pg_schema=False,
            skip_bootstrap_hook=True,
        )
    )
    assert result.creature_id == "email_pm.work"


def test_forge_mortal_two_instances_get_different_identities(tmp_path: Path):
    """Different instance_ids → different creature_ids → different keys."""
    ws = _setup_workspace(tmp_path)
    m = load_manifest_from_text(EMAIL_PM_MORTAL)
    kw = dict(
        repo_root=ws["repo_root"],
        agents_yaml=ws["agents_yaml"],
        capabilities_yaml=ws["capabilities_yaml"],
        agents_dir=ws["agents_dir"],
        feed_base=ws["feed_base"],
        gods={"enkidu": EnkiduGod(policy=load_policy(ws["capabilities_yaml"]))},
        dispatchers={"enkidu": _fake_dispatcher},
        create_pg_schema=False,
        skip_bootstrap_hook=True,
    )

    a = asyncio.run(forge_mortal(m, instance_id="personal", **kw))

    # Refresh policy after first forge so EnkiduGod sees the new policy
    # block when we forge the second instance.
    kw["gods"] = {"enkidu": EnkiduGod(policy=load_policy(ws["capabilities_yaml"]))}
    b = asyncio.run(forge_mortal(m, instance_id="work", **kw))

    assert a.creature_id != b.creature_id
    assert a.agent_key_path != b.agent_key_path
    assert a.feed_path != b.feed_path


# ----------------------------- denied-capability path --------------------


def test_forge_mortal_denial_short_circuits(tmp_path: Path):
    """If a god denies a capability the manifest declared, forge fails."""
    ws = _setup_workspace(tmp_path)

    class DenyingGod:
        async def bless(self, *, creature_id, request):
            from shared.creatures.tools import Denial

            return Denial(
                capability=request.capability,
                creature_id=creature_id,
                god="denying_god",
                reason="nope",
            )

        async def commission_angel(self, *, creature_id):
            return None

    # Use a manifest whose capability prefix routes to enkidu, but stub
    # the enkidu god with a denying god.
    m = load_manifest_from_text(EMAIL_PM_MORTAL)

    with pytest.raises(ForgeError, match="blessing collection failed"):
        asyncio.run(
            forge_mortal(
                m,
                instance_id="personal",
                repo_root=ws["repo_root"],
                agents_yaml=ws["agents_yaml"],
                capabilities_yaml=ws["capabilities_yaml"],
                agents_dir=ws["agents_dir"],
                feed_base=ws["feed_base"],
                gods={"enkidu": DenyingGod()},  # type: ignore[dict-item]
                dispatchers={"enkidu": _fake_dispatcher},
                create_pg_schema=False,
                skip_bootstrap_hook=True,
            )
        )


def test_forge_mortal_unrouted_capability_short_circuits(tmp_path: Path):
    """A capability whose prefix has no route → forge fails."""
    ws = _setup_workspace(tmp_path)
    m_text = """\
name: x
kind: mortal
display_name: X
version: "1.0.0"
capabilities:
  - psychic.divine
"""
    m = load_manifest_from_text(m_text)
    with pytest.raises(ForgeError, match="blessing collection failed"):
        asyncio.run(
            forge_mortal(
                m,
                instance_id="ghost",
                repo_root=ws["repo_root"],
                agents_yaml=ws["agents_yaml"],
                capabilities_yaml=ws["capabilities_yaml"],
                agents_dir=ws["agents_dir"],
                feed_base=ws["feed_base"],
                gods={"enkidu": EnkiduGod(policy=load_policy(ws["capabilities_yaml"]))},
                dispatchers={"enkidu": _fake_dispatcher},
                create_pg_schema=False,
                skip_bootstrap_hook=True,
            )
        )


# ----------------------------- idempotency / re-forge --------------------


def test_forge_mortal_re_forge_same_instance_fails_without_force(tmp_path: Path):
    """Re-forging the same instance without --force raises (silent
    key rotation is exactly what this prevents)."""
    ws = _setup_workspace(tmp_path)
    m = load_manifest_from_text(EMAIL_PM_MORTAL)
    kw = dict(
        repo_root=ws["repo_root"],
        agents_yaml=ws["agents_yaml"],
        capabilities_yaml=ws["capabilities_yaml"],
        agents_dir=ws["agents_dir"],
        feed_base=ws["feed_base"],
        dispatchers={"enkidu": _fake_dispatcher},
        create_pg_schema=False,
        skip_bootstrap_hook=True,
    )

    asyncio.run(
        forge_mortal(
            m,
            instance_id="personal",
            gods={"enkidu": EnkiduGod(policy=load_policy(ws["capabilities_yaml"]))},
            **kw,
        )
    )
    # Second call without force → ForgeError wrapping ProvisionError.
    with pytest.raises(ForgeError, match="provisioning failed"):
        asyncio.run(
            forge_mortal(
                m,
                instance_id="personal",
                gods={"enkidu": EnkiduGod(policy=load_policy(ws["capabilities_yaml"]))},
                **kw,
            )
        )


def test_forge_mortal_re_forge_with_force_rotates_key(tmp_path: Path):
    """force=True rotates the key (existing entry replaced)."""
    ws = _setup_workspace(tmp_path)
    m = load_manifest_from_text(EMAIL_PM_MORTAL)
    kw = dict(
        repo_root=ws["repo_root"],
        agents_yaml=ws["agents_yaml"],
        capabilities_yaml=ws["capabilities_yaml"],
        agents_dir=ws["agents_dir"],
        feed_base=ws["feed_base"],
        dispatchers={"enkidu": _fake_dispatcher},
        create_pg_schema=False,
        skip_bootstrap_hook=True,
    )

    a = asyncio.run(
        forge_mortal(
            m,
            instance_id="personal",
            gods={"enkidu": EnkiduGod(policy=load_policy(ws["capabilities_yaml"]))},
            **kw,
        )
    )
    key_a = a.agent_key_path.read_text()

    b = asyncio.run(
        forge_mortal(
            m,
            instance_id="personal",
            gods={"enkidu": EnkiduGod(policy=load_policy(ws["capabilities_yaml"]))},
            force=True,
            **kw,
        )
    )
    key_b = b.agent_key_path.read_text()

    assert key_a != key_b  # rotation happened
    assert a.agent_key_path == b.agent_key_path  # same path


# ----------------------------- Postgres schema (best-effort) -------------


def test_forge_mortal_pg_schema_skipped_without_database_url(
    tmp_path: Path, monkeypatch
):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    ws = _setup_workspace(tmp_path)
    m = load_manifest_from_text(EMAIL_PM_MORTAL)
    result = asyncio.run(
        forge_mortal(
            m,
            instance_id="personal",
            repo_root=ws["repo_root"],
            agents_yaml=ws["agents_yaml"],
            capabilities_yaml=ws["capabilities_yaml"],
            agents_dir=ws["agents_dir"],
            feed_base=ws["feed_base"],
            gods={"enkidu": EnkiduGod(policy=load_policy(ws["capabilities_yaml"]))},
            dispatchers={"enkidu": _fake_dispatcher},
            create_pg_schema=True,
            skip_bootstrap_hook=True,
        )
    )
    assert result.pg_schema is None
    assert any("DATABASE_URL" in n for n in result.notes)


@pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration test — requires real Postgres",
)
def test_forge_mortal_creates_pg_schema_when_db_available(tmp_path: Path):
    """Integration: real Postgres → schema actually created.

    Idempotent — re-running is a no-op (CREATE SCHEMA IF NOT EXISTS).
    Cleanup: drop the schema afterwards so we don't pollute the dev DB.
    """
    ws = _setup_workspace(tmp_path)
    m = load_manifest_from_text(EMAIL_PM_MORTAL)
    instance = f"forge_mortal_test_{datetime.now(tz=timezone.utc).strftime('%Y%m%d%H%M%S')}"

    try:
        result = asyncio.run(
            forge_mortal(
                m,
                instance_id=instance,
                repo_root=ws["repo_root"],
                agents_yaml=ws["agents_yaml"],
                capabilities_yaml=ws["capabilities_yaml"],
                agents_dir=ws["agents_dir"],
                feed_base=ws["feed_base"],
                gods={"enkidu": EnkiduGod(policy=load_policy(ws["capabilities_yaml"]))},
                dispatchers={"enkidu": _fake_dispatcher},
                create_pg_schema=True,
                skip_bootstrap_hook=True,
            )
        )
        assert result.pg_schema == f"mortal_email_pm_{instance}"
        assert result.pg_schema is not None

        # Verify the schema actually exists in Postgres.
        import psycopg

        with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
            row = conn.execute(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name = %s",
                (result.pg_schema,),
            ).fetchone()
            assert row is not None
    finally:
        # Cleanup.
        try:
            import psycopg
            from psycopg import sql

            with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
                conn.execute(
                    sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                        sql.Identifier(f"mortal_email_pm_{instance}")
                    )
                )
        except Exception:
            pass


# ----------------------------- ToolRegistry usability --------------------


def test_forge_mortal_registry_invokes_blessed_tool(tmp_path: Path):
    """End-to-end: forged registry can dispatch a blessed tool."""
    ws = _setup_workspace(tmp_path)
    m = load_manifest_from_text(EMAIL_PM_MORTAL)
    captured = {}

    async def capturing_dispatcher(ctx, *, capability, blessing, **kwargs):
        captured["capability"] = capability
        captured["kwargs"] = kwargs
        return {"ok": True}

    result = asyncio.run(
        forge_mortal(
            m,
            instance_id="personal",
            repo_root=ws["repo_root"],
            agents_yaml=ws["agents_yaml"],
            capabilities_yaml=ws["capabilities_yaml"],
            agents_dir=ws["agents_dir"],
            feed_base=ws["feed_base"],
            gods={"enkidu": EnkiduGod(policy=load_policy(ws["capabilities_yaml"]))},
            dispatchers={"enkidu": capturing_dispatcher},
            create_pg_schema=False,
            skip_bootstrap_hook=True,
        )
    )

    # Build a fake context to exercise registry.invoke.
    import logging

    from shared.creatures.context import MortalContext
    from shared.creatures.tools import ToolRegistry, with_context

    ctx = MortalContext(
        creature_id=result.creature_id,
        display_name="Email PM",
        audit=ObservationFeed(result.creature_id, base=ws["feed_base"]),
        logger=logging.getLogger("test"),
        llm=object(),  # type: ignore[arg-type]
        tools=result.registry,  # type: ignore[arg-type]
        memory=object(),  # type: ignore[arg-type]
        bus=object(),  # type: ignore[arg-type]
    )

    async def run():
        with with_context(ctx):
            return await result.registry.invoke("gmail.send", to="alice@example.com")

    out = asyncio.run(run())
    assert out == {"ok": True}
    assert captured == {"capability": "gmail.send", "kwargs": {"to": "alice@example.com"}}
