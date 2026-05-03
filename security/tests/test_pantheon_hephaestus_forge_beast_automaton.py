"""Tests for forge_beast + forge_automaton — v0.11 step 3e.2."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from demiurge.pantheon.hephaestus import (
    ForgeError,
    ForgeResult,
    forge_automaton,
    forge_beast,
    forge_mortal,
)
from demiurge.pantheon.hephaestus.gods import EnkiduGod, ArachneGod
from demiurge.policy import load_policy
from shared.plugins.manifest import load_manifest_from_text


# ----------------------------- manifest fixtures -------------------------


IMAGE_GEN_BEAST = """\
name: image_gen
kind: beast
display_name: Image Generator
version: "1.0.0"
capabilities:
  - web.fetch
"""


SUMMARIZER_BEAST = """\
name: summarizer
kind: beast
display_name: Text Summarizer
version: "1.0.0"
capabilities: []
"""


SCHEDULER_AUTOMATON = """\
name: scheduler
kind: automaton
display_name: Scheduler
version: "1.0.0"
capabilities: []
"""


RSS_AUTOMATON = """\
name: rss_reader
kind: automaton
display_name: RSS Reader
version: "1.0.0"
capabilities:
  - web.fetch
"""


EMAIL_PM_MORTAL = """\
name: email_pm
kind: mortal
display_name: Email PM
version: "1.0.0"
capabilities:
  - gmail.send
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


# ----------------------------- forge_beast: kind validation --------------


def test_forge_beast_rejects_mortal_manifest(tmp_path: Path):
    ws = _setup_workspace(tmp_path)
    m = load_manifest_from_text(EMAIL_PM_MORTAL)
    with pytest.raises(ForgeError, match="kind=.beast"):
        asyncio.run(
            forge_beast(
                m,
                instance_id="default",
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


def test_forge_beast_rejects_automaton_manifest(tmp_path: Path):
    ws = _setup_workspace(tmp_path)
    m = load_manifest_from_text(SCHEDULER_AUTOMATON)
    with pytest.raises(ForgeError, match="kind=.beast"):
        asyncio.run(
            forge_beast(
                m,
                instance_id="default",
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


# ----------------------------- forge_beast: end-to-end -------------------


def test_forge_beast_with_capabilities(tmp_path: Path):
    """A Beast that needs upstream API access via web.fetch."""
    ws = _setup_workspace(tmp_path)
    m = load_manifest_from_text(IMAGE_GEN_BEAST)
    result = asyncio.run(
        forge_beast(
            m,
            instance_id="default",
            repo_root=ws["repo_root"],
            agents_yaml=ws["agents_yaml"],
            capabilities_yaml=ws["capabilities_yaml"],
            agents_dir=ws["agents_dir"],
            feed_base=ws["feed_base"],
            gods={
                "enkidu": EnkiduGod(policy=load_policy(ws["capabilities_yaml"])),
                "arachne": ArachneGod(),
            },
            dispatchers={"enkidu": _fake_dispatcher, "arachne": _fake_dispatcher},
            create_pg_schema=False,
            skip_bootstrap_hook=True,
        )
    )

    assert isinstance(result, ForgeResult)
    assert result.kind == "beast"
    assert result.creature_id == "image_gen.default"
    assert result.policy_written is True
    assert result.agent_key_path is not None
    assert "web.fetch" in result.registry.names()
    # Universal tools still present even though Beasts don't usually use them.
    assert "think" in result.registry.names()
    assert "mortal.return" in result.registry.names()


def test_forge_beast_with_no_capabilities(tmp_path: Path):
    """A Beast with empty capabilities still forges — gets identity +
    feed but an empty (universal-tools-only) registry."""
    ws = _setup_workspace(tmp_path)
    m = load_manifest_from_text(SUMMARIZER_BEAST)
    result = asyncio.run(
        forge_beast(
            m,
            instance_id="default",
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
    assert result.creature_id == "summarizer.default"
    assert result.kind == "beast"
    # Empty capabilities → registry has only universal tools.
    assert set(result.registry.names()) == {"think", "mortal.return"}


def test_forge_beast_creature_id_format(tmp_path: Path):
    ws = _setup_workspace(tmp_path)
    m = load_manifest_from_text(SUMMARIZER_BEAST)
    result = asyncio.run(
        forge_beast(
            m,
            instance_id="fast",
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
    assert result.creature_id == "summarizer.fast"


# ----------------------------- forge_automaton: kind validation ----------


def test_forge_automaton_rejects_mortal_manifest(tmp_path: Path):
    ws = _setup_workspace(tmp_path)
    m = load_manifest_from_text(EMAIL_PM_MORTAL)
    with pytest.raises(ForgeError, match="kind=.automaton"):
        asyncio.run(
            forge_automaton(
                m,
                instance_id="default",
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


def test_forge_automaton_rejects_beast_manifest(tmp_path: Path):
    ws = _setup_workspace(tmp_path)
    m = load_manifest_from_text(SUMMARIZER_BEAST)
    with pytest.raises(ForgeError, match="kind=.automaton"):
        asyncio.run(
            forge_automaton(
                m,
                instance_id="default",
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


# ----------------------------- forge_automaton: end-to-end ---------------


def test_forge_automaton_no_capabilities(tmp_path: Path):
    """A scheduler Automaton with no declared capabilities — pure
    bus-publish (which doesn't need a blessing for v0.11)."""
    ws = _setup_workspace(tmp_path)
    m = load_manifest_from_text(SCHEDULER_AUTOMATON)
    result = asyncio.run(
        forge_automaton(
            m,
            instance_id="default",
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
    assert result.creature_id == "scheduler.default"
    assert result.kind == "automaton"
    assert result.agent_key_path is not None
    assert set(result.registry.names()) == {"think", "mortal.return"}


def test_forge_automaton_with_capabilities(tmp_path: Path):
    ws = _setup_workspace(tmp_path)
    m = load_manifest_from_text(RSS_AUTOMATON)
    result = asyncio.run(
        forge_automaton(
            m,
            instance_id="default",
            repo_root=ws["repo_root"],
            agents_yaml=ws["agents_yaml"],
            capabilities_yaml=ws["capabilities_yaml"],
            agents_dir=ws["agents_dir"],
            feed_base=ws["feed_base"],
            gods={
                "enkidu": EnkiduGod(policy=load_policy(ws["capabilities_yaml"])),
                "arachne": ArachneGod(),
            },
            dispatchers={"enkidu": _fake_dispatcher, "arachne": _fake_dispatcher},
            create_pg_schema=False,
            skip_bootstrap_hook=True,
        )
    )
    assert result.creature_id == "rss_reader.default"
    assert "web.fetch" in result.registry.names()


# ----------------------------- pg schema namespace per kind --------------


@pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration test — requires real Postgres",
)
def test_forge_beast_creates_beast_schema(tmp_path: Path):
    """Per-kind schema prefix: beast_<id> not mortal_<id>."""
    ws = _setup_workspace(tmp_path)
    m = load_manifest_from_text(SUMMARIZER_BEAST)
    instance = "schema_test"
    try:
        result = asyncio.run(
            forge_beast(
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
        assert result.pg_schema == f"beast_summarizer_{instance}"
    finally:
        try:
            import psycopg
            from psycopg import sql

            with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
                conn.execute(
                    sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                        sql.Identifier(f"beast_summarizer_{instance}")
                    )
                )
        except Exception:
            pass


@pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration test — requires real Postgres",
)
def test_forge_automaton_creates_automaton_schema(tmp_path: Path):
    ws = _setup_workspace(tmp_path)
    m = load_manifest_from_text(SCHEDULER_AUTOMATON)
    instance = "schema_test"
    try:
        result = asyncio.run(
            forge_automaton(
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
        assert result.pg_schema == f"automaton_scheduler_{instance}"
    finally:
        try:
            import psycopg
            from psycopg import sql

            with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
                conn.execute(
                    sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                        sql.Identifier(f"automaton_scheduler_{instance}")
                    )
                )
        except Exception:
            pass


# ----------------------------- cross-kind: same instance, different schemas


def test_same_name_different_kinds_get_distinct_pg_schema_prefixes(tmp_path: Path):
    """Catches a regression where all forges shared the 'mortal_' prefix."""
    ws = _setup_workspace(tmp_path)

    # Manifests that share a name but different kinds.
    m_mortal = load_manifest_from_text(EMAIL_PM_MORTAL)
    m_beast = load_manifest_from_text(SUMMARIZER_BEAST)
    m_auto = load_manifest_from_text(SCHEDULER_AUTOMATON)

    kw = dict(
        instance_id="default",
        repo_root=ws["repo_root"],
        agents_yaml=ws["agents_yaml"],
        capabilities_yaml=ws["capabilities_yaml"],
        agents_dir=ws["agents_dir"],
        feed_base=ws["feed_base"],
        dispatchers={"enkidu": _fake_dispatcher},
        create_pg_schema=False,  # we just check the would-be schema name
        skip_bootstrap_hook=True,
    )

    # We can't easily check the schema name when create_pg_schema=False,
    # since pg_schema is None in that case. Instead, monkey-patch the
    # pg-schema-creator to just return what schema name it got asked for.
    import demiurge.pantheon.hephaestus.forge as forge_mod

    captured: list[str] = []

    def fake_creator(schema_name, *, dsn=None):
        captured.append(schema_name)
        return True, None

    orig = forge_mod._create_pg_schema_if_configured
    forge_mod._create_pg_schema_if_configured = fake_creator
    try:
        kw["create_pg_schema"] = True
        asyncio.run(
            forge_mortal(
                m_mortal,
                gods={"enkidu": EnkiduGod(policy=load_policy(ws["capabilities_yaml"]))},
                **kw,
            )
        )
        kw["gods"] = {"enkidu": EnkiduGod(policy=load_policy(ws["capabilities_yaml"]))}
        asyncio.run(forge_beast(m_beast, **kw))
        kw["gods"] = {"enkidu": EnkiduGod(policy=load_policy(ws["capabilities_yaml"]))}
        asyncio.run(forge_automaton(m_auto, **kw))
    finally:
        forge_mod._create_pg_schema_if_configured = orig

    assert captured == [
        "mortal_email_pm_default",
        "beast_summarizer_default",
        "automaton_scheduler_default",
    ]


# ----------------------------- the supplied gods/dispatchers gate --------


def test_forge_beast_requires_gods_and_dispatchers(tmp_path: Path):
    ws = _setup_workspace(tmp_path)
    m = load_manifest_from_text(SUMMARIZER_BEAST)
    with pytest.raises(ForgeError, match="gods.*dispatchers"):
        asyncio.run(
            forge_beast(
                m,
                instance_id="default",
                repo_root=ws["repo_root"],
                agents_yaml=ws["agents_yaml"],
                capabilities_yaml=ws["capabilities_yaml"],
                agents_dir=ws["agents_dir"],
                feed_base=ws["feed_base"],
                # gods=None, dispatchers=None
            )
        )


def test_forge_automaton_requires_gods_and_dispatchers(tmp_path: Path):
    ws = _setup_workspace(tmp_path)
    m = load_manifest_from_text(SCHEDULER_AUTOMATON)
    with pytest.raises(ForgeError, match="gods.*dispatchers"):
        asyncio.run(
            forge_automaton(
                m,
                instance_id="default",
                repo_root=ws["repo_root"],
                agents_yaml=ws["agents_yaml"],
                capabilities_yaml=ws["capabilities_yaml"],
                agents_dir=ws["agents_dir"],
                feed_base=ws["feed_base"],
            )
        )
