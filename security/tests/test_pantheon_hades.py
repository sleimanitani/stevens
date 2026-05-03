"""Tests for demiurge.pantheon.hades — v0.11 step 4."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
import yaml

from demiurge.pantheon.hades import (
    ArchiveAction,
    ArchiveError,
    ArchiveResult,
    archive_automaton,
    archive_beast,
    archive_mortal,
    archive_power,
    exile_pantheon_member,
    fade_pantheon_member,
    ragnarok,
)
from demiurge.pantheon.hephaestus import (
    forge_automaton,
    forge_beast,
    forge_mortal,
    forge_power,
)
from demiurge.pantheon.hephaestus.gods import EnkiduGod
from demiurge.policy import load_policy
from shared.creatures.feed import ObservationFeed
from shared.plugins.manifest import load_manifest_from_text


# ----------------------------- helpers -----------------------------------


def _setup_workspace(tmp_path: Path) -> dict[str, Path]:
    workspace = {
        "agents_yaml": tmp_path / "agents.yaml",
        "capabilities_yaml": tmp_path / "capabilities.yaml",
        "agents_dir": tmp_path / "agents",
        "feed_base": tmp_path / "feeds",
        "archive_base": tmp_path / "archive",
        "repo_root": tmp_path / "repo",
        "units_dir": tmp_path / "units",
    }
    workspace["agents_dir"].mkdir(parents=True, exist_ok=True)
    workspace["repo_root"].mkdir(parents=True, exist_ok=True)
    return workspace


async def _fake_dispatcher(ctx, *, capability, blessing, **kwargs):
    return f"dispatched: {capability}"


EMAIL_PM_MORTAL = """\
name: email_pm
kind: mortal
display_name: Email PM
version: "1.0.0"
capabilities:
  - gmail.send
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


GMAIL_POWER = """\
name: gmail
kind: power
display_name: Gmail
version: "1.0.0"
modes: [request-based]
capabilities: []
bootstrap: gmail_adapter.bootstrap:install
"""


# ----------------------------- archive_power -----------------------------


def test_archive_power_removes_existing_unit(tmp_path: Path):
    """forge_power creates a unit; archive_power removes it."""
    ws = _setup_workspace(tmp_path)
    # Build a webhook power so a unit gets written.
    m_text = """\
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
capabilities: []
bootstrap: gmail_adapter.bootstrap:install
"""
    m = load_manifest_from_text(m_text)
    forge_result = asyncio.run(
        forge_power(
            m,
            repo_root=ws["repo_root"],
            target_dir=ws["units_dir"],
            env_file=tmp_path / "env",
            skip_bootstrap_hook=True,
        )
    )
    unit_path = forge_result.systemd_units[0].path
    assert unit_path.exists()

    result = archive_power("gmail", target_dir=ws["units_dir"])
    assert result.ok
    assert result.kind == "power"
    assert not unit_path.exists()
    assert any(a.verb == "removed" for a in result.actions)


def test_archive_power_idempotent(tmp_path: Path):
    """Re-archiving a power that's already gone reports unchanged."""
    ws = _setup_workspace(tmp_path)
    ws["units_dir"].mkdir(parents=True, exist_ok=True)
    result = archive_power("never_existed", target_dir=ws["units_dir"])
    assert result.ok
    assert any(a.verb == "unchanged" for a in result.actions)


def test_archive_power_format_report(tmp_path: Path):
    ws = _setup_workspace(tmp_path)
    ws["units_dir"].mkdir(parents=True, exist_ok=True)
    result = archive_power("nope", target_dir=ws["units_dir"])
    out = result.format_report()
    assert "Archived power 'nope'" in out
    assert "systemd unit" in out


# ----------------------------- archive_creature: round trip --------------


def _forge_a_mortal(ws: dict[str, Path]) -> str:
    """Helper that forges email_pm.personal so we have something to archive."""
    m = load_manifest_from_text(EMAIL_PM_MORTAL)
    asyncio.run(
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
            create_pg_schema=False,
            skip_bootstrap_hook=True,
        )
    )
    return "email_pm.personal"


def test_archive_mortal_removes_identity_and_policy(tmp_path: Path):
    ws = _setup_workspace(tmp_path)
    creature_id = _forge_a_mortal(ws)

    # Pre-archive sanity: identity + policy exist.
    agents_data = yaml.safe_load(ws["agents_yaml"].read_text())
    assert any(a["name"] == creature_id for a in agents_data["agents"])
    policy_data = yaml.safe_load(ws["capabilities_yaml"].read_text())
    assert any(a["name"] == creature_id for a in policy_data["agents"])

    result = archive_mortal(
        creature_id,
        agents_yaml=ws["agents_yaml"],
        capabilities_yaml=ws["capabilities_yaml"],
        agents_dir=ws["agents_dir"],
        feed_base=ws["feed_base"],
        archive_base=ws["archive_base"],
    )

    assert result.ok
    assert result.kind == "mortal"

    # Identity gone.
    agents_data = yaml.safe_load(ws["agents_yaml"].read_text())
    assert not any(a.get("name") == creature_id for a in agents_data.get("agents") or [])

    # Policy gone.
    policy_data = yaml.safe_load(ws["capabilities_yaml"].read_text())
    assert not any(a.get("name") == creature_id for a in policy_data.get("agents") or [])

    # Key file gone.
    assert not (ws["agents_dir"] / f"{creature_id}.key").exists()
    # Env file gone.
    assert not (ws["agents_dir"] / f"{creature_id}.env").exists()


def test_archive_mortal_archives_observation_feed(tmp_path: Path):
    ws = _setup_workspace(tmp_path)
    creature_id = _forge_a_mortal(ws)

    feed_dir = (ws["feed_base"] / creature_id)
    assert feed_dir.exists()

    archive_mortal(
        creature_id,
        agents_yaml=ws["agents_yaml"],
        capabilities_yaml=ws["capabilities_yaml"],
        agents_dir=ws["agents_dir"],
        feed_base=ws["feed_base"],
        archive_base=ws["archive_base"],
    )

    # Original feed dir gone.
    assert not feed_dir.exists()
    # Archive root has a renamed copy of the feed.
    archive_dirs = list(ws["archive_base"].iterdir())
    assert len(archive_dirs) == 1
    assert archive_dirs[0].name.startswith(creature_id)
    # Inside, the events.jsonl is preserved.
    assert (archive_dirs[0] / "events.jsonl").exists()


def test_archive_mortal_idempotent(tmp_path: Path):
    """Re-archive: everything reports unchanged."""
    ws = _setup_workspace(tmp_path)
    creature_id = _forge_a_mortal(ws)

    archive_mortal(
        creature_id,
        agents_yaml=ws["agents_yaml"],
        capabilities_yaml=ws["capabilities_yaml"],
        agents_dir=ws["agents_dir"],
        feed_base=ws["feed_base"],
        archive_base=ws["archive_base"],
    )
    second = archive_mortal(
        creature_id,
        agents_yaml=ws["agents_yaml"],
        capabilities_yaml=ws["capabilities_yaml"],
        agents_dir=ws["agents_dir"],
        feed_base=ws["feed_base"],
        archive_base=ws["archive_base"],
    )

    assert second.ok
    # Every action either unchanged or skipped (no DB).
    for a in second.actions:
        assert a.verb in {"unchanged", "skipped"}, f"unexpected verb: {a.verb} for {a.description}"


def test_archive_then_re_forge_is_clean(tmp_path: Path):
    """After archive, a fresh forge with the same creature_id works
    without ``force=True``."""
    ws = _setup_workspace(tmp_path)
    creature_id = _forge_a_mortal(ws)

    archive_mortal(
        creature_id,
        agents_yaml=ws["agents_yaml"],
        capabilities_yaml=ws["capabilities_yaml"],
        agents_dir=ws["agents_dir"],
        feed_base=ws["feed_base"],
        archive_base=ws["archive_base"],
    )

    # Re-forge — should NOT need force=True.
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
            create_pg_schema=False,
            skip_bootstrap_hook=True,
        )
    )
    assert result.creature_id == creature_id


# ----------------------------- archive_beast / archive_automaton ---------


def test_archive_beast_round_trip(tmp_path: Path):
    ws = _setup_workspace(tmp_path)
    m = load_manifest_from_text(SUMMARIZER_BEAST)
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
    result = archive_beast(
        "summarizer.default",
        agents_yaml=ws["agents_yaml"],
        capabilities_yaml=ws["capabilities_yaml"],
        agents_dir=ws["agents_dir"],
        feed_base=ws["feed_base"],
        archive_base=ws["archive_base"],
    )
    assert result.ok
    assert result.kind == "beast"


def test_archive_automaton_round_trip(tmp_path: Path):
    ws = _setup_workspace(tmp_path)
    m = load_manifest_from_text(SCHEDULER_AUTOMATON)
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
    result = archive_automaton(
        "scheduler.default",
        agents_yaml=ws["agents_yaml"],
        capabilities_yaml=ws["capabilities_yaml"],
        agents_dir=ws["agents_dir"],
        feed_base=ws["feed_base"],
        archive_base=ws["archive_base"],
    )
    assert result.ok
    assert result.kind == "automaton"


# ----------------------------- pg-schema integration (gated) -------------


@pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration test — requires real Postgres",
)
def test_archive_mortal_renames_pg_schema(tmp_path: Path):
    """forge creates schema → archive renames it to archived_<...>_<ts>."""
    ws = _setup_workspace(tmp_path)
    m = load_manifest_from_text(EMAIL_PM_MORTAL)
    instance = "archive_test"
    schema_name = f"mortal_email_pm_{instance}"

    try:
        asyncio.run(
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

        result = archive_mortal(
            f"email_pm.{instance}",
            agents_yaml=ws["agents_yaml"],
            capabilities_yaml=ws["capabilities_yaml"],
            agents_dir=ws["agents_dir"],
            feed_base=ws["feed_base"],
            archive_base=ws["archive_base"],
        )
        assert result.pg_schema_action == "renamed"

        # Verify rename happened in Postgres.
        import psycopg

        with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
            row = conn.execute(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name = %s",
                (schema_name,),
            ).fetchone()
            assert row is None  # original gone

            archived = conn.execute(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name LIKE %s",
                (f"archived_{schema_name}_%",),
            ).fetchall()
            assert len(archived) == 1
    finally:
        # Cleanup.
        try:
            import psycopg
            from psycopg import sql

            with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
                for prefix in (schema_name, f"archived_{schema_name}"):
                    rows = conn.execute(
                        "SELECT schema_name FROM information_schema.schemata "
                        "WHERE schema_name LIKE %s",
                        (f"{prefix}%",),
                    ).fetchall()
                    for (s,) in rows:
                        conn.execute(
                            sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                                sql.Identifier(s)
                            )
                        )
        except Exception:
            pass


@pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration test — requires real Postgres",
)
def test_archive_mortal_drop_data_drops_pg_schema(tmp_path: Path):
    ws = _setup_workspace(tmp_path)
    m = load_manifest_from_text(EMAIL_PM_MORTAL)
    instance = "drop_test"
    schema_name = f"mortal_email_pm_{instance}"

    try:
        asyncio.run(
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

        result = archive_mortal(
            f"email_pm.{instance}",
            agents_yaml=ws["agents_yaml"],
            capabilities_yaml=ws["capabilities_yaml"],
            agents_dir=ws["agents_dir"],
            feed_base=ws["feed_base"],
            archive_base=ws["archive_base"],
            drop_data=True,
        )
        assert result.pg_schema_action == "dropped"

        import psycopg

        with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
            row = conn.execute(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name = %s",
                (schema_name,),
            ).fetchone()
            assert row is None
    finally:
        try:
            import psycopg
            from psycopg import sql

            with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
                conn.execute(
                    sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                        sql.Identifier(schema_name)
                    )
                )
        except Exception:
            pass


# ----------------------------- Pantheon-member stubs ---------------------


def test_fade_stub():
    result = fade_pantheon_member("some_god")
    assert result.kind == "pantheon_member"
    assert any(a.verb == "skipped" for a in result.actions)
    assert any("automated" in n for n in result.notes)


def test_exile_stub():
    result = exile_pantheon_member("some_god", reason="security incident")
    assert result.kind == "pantheon_member"
    assert any(a.verb == "skipped" for a in result.actions)
    assert any("security incident" in a.description for a in result.actions)


def test_ragnarok_stub():
    result = ragnarok("some_god")
    assert result.kind == "pantheon_member"
    assert any(a.verb == "skipped" for a in result.actions)
