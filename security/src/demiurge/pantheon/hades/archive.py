"""Hades's archive operations — the end-side lifecycle executor.

v0.11 step 4. Symmetric to Hephaestus's forge: every kind of Creature
that can be forged can be archived. The archive flow is fail-tolerant
(unlike forge which is fail-fast) — if a teardown step fails, we
record the failure but continue with the rest. The reason: archive is
about *cleanup*, and a partial cleanup is better than a stuck Creature
with some artifacts left behind.

For Powers:
1. Remove the systemd unit file (``demiurge-power-<name>.service``).
2. (Optional) reload systemd daemon to make removal take effect.
3. Capabilities exposed by the power are unregistered implicitly when
   its adapter stops — no separate deregistration step.

For Creatures (Mortal/Beast/Automaton):
1. Remove the agent identity (entry in agents.yaml; key file).
2. Remove the policy block (entry in capabilities.yaml).
3. Move the observation feed to ``~/.local/state/demiurge/archive/<creature_id>/``.
4. Rename the per-Creature Postgres schema to
   ``archived_<kind>_<id>_<ts>`` (default) or drop with ``--drop-data``.
5. Retire any attached in-process angels (a v0.11 no-op since they're
   garbage-collected; v0.13's out-of-process angels need an explicit
   stop).

Pantheon-member archival (Fading / Exile / Ragnarök) is a heavier
operation — it touches a god's substrate that other Creatures still
depend on. For v0.11 the three corresponding functions are stubs that
print manual-intervention instructions; we don't have a god currently
fading and pretending otherwise would just be ceremony.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

from ...bootstrap.systemd import systemd_user_dir
from ...provision import default_agents_dir
from shared.creatures.feed import feed_path_for, feed_root


class ArchiveError(Exception):
    """Hades hit a hard failure mid-archive (rare; usually we record
    the failure as a note and continue)."""


@dataclass(frozen=True)
class ArchiveAction:
    """One archival step: ``("removed" | "archived" | "renamed" |
    "dropped" | "unchanged" | "skipped" | "failed", description)``."""

    verb: str
    description: str
    path: Optional[Path] = None
    error: Optional[str] = None


@dataclass(frozen=True)
class ArchiveResult:
    """Structured outcome of an archive call. Symmetric to ForgeResult."""

    creature_id: str
    kind: str
    actions: list[ArchiveAction] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    pg_schema_action: Optional[str] = None  # "renamed" | "dropped" | None

    @property
    def ok(self) -> bool:
        return not any(a.verb == "failed" for a in self.actions)

    def format_report(self) -> str:
        lines = [f"Archived {self.kind} {self.creature_id!r}:"]
        for a in self.actions:
            symbol = {
                "removed": "-",
                "archived": "→",
                "renamed": "~",
                "dropped": "✗",
                "unchanged": "·",
                "skipped": "·",
                "failed": "!",
            }.get(a.verb, "?")
            tail = ""
            if a.path is not None:
                tail = f"  ({a.path})"
            lines.append(f"  {symbol} {a.description}{tail}")
            if a.error:
                lines.append(f"      error: {a.error}")
        if self.pg_schema_action:
            lines.append(f"  pg schema: {self.pg_schema_action}")
        for note in self.notes:
            lines.append(f"  note: {note}")
        return "\n".join(lines)


# ----------------------------- helpers -----------------------------------


def _archive_root(base: Optional[Path] = None) -> Path:
    """``~/.local/state/demiurge/archive/`` — where retired feeds land."""
    if base is not None:
        return base
    env = os.environ.get("DEMIURGE_ARCHIVE_ROOT")
    if env:
        return Path(env)
    return Path("~/.local/state/demiurge/archive").expanduser()


def _now_stamp() -> str:
    """Compact UTC timestamp for archive-name suffixing."""
    return datetime.now(tz=timezone.utc).strftime("%Y%m%d%H%M%S")


def _try_remove_file(path: Path, description: str) -> ArchiveAction:
    """Best-effort file removal. Returns a fitting ArchiveAction."""
    try:
        if path.exists():
            path.unlink()
            return ArchiveAction(verb="removed", description=description, path=path)
        return ArchiveAction(verb="unchanged", description=f"{description} (already absent)", path=path)
    except OSError as e:
        return ArchiveAction(
            verb="failed",
            description=description,
            path=path,
            error=f"{type(e).__name__}: {e}",
        )


def _remove_agent_from_yaml(yaml_path: Path, name: str) -> ArchiveAction:
    """Remove an agent entry from agents.yaml or capabilities.yaml.

    Both files share the same top-level shape (``agents: [{name: ...}]``),
    so this is a single helper.
    """
    if not yaml_path.exists():
        return ArchiveAction(
            verb="unchanged",
            description=f"{yaml_path.name}: agent entry (file absent)",
            path=yaml_path,
        )
    try:
        data = yaml.safe_load(yaml_path.read_text()) or {}
    except yaml.YAMLError as e:
        return ArchiveAction(
            verb="failed",
            description=f"{yaml_path.name}: parse",
            path=yaml_path,
            error=f"{type(e).__name__}: {e}",
        )
    if not isinstance(data, dict):
        return ArchiveAction(
            verb="failed",
            description=f"{yaml_path.name}: top-level not a map",
            path=yaml_path,
        )
    agents = data.get("agents") or []
    if not isinstance(agents, list):
        return ArchiveAction(
            verb="failed",
            description=f"{yaml_path.name}: 'agents' not a list",
            path=yaml_path,
        )

    before = len(agents)
    new_agents = [a for a in agents if not (isinstance(a, dict) and a.get("name") == name)]
    if len(new_agents) == before:
        return ArchiveAction(
            verb="unchanged",
            description=f"{yaml_path.name}: no entry for {name!r}",
            path=yaml_path,
        )

    data["agents"] = new_agents
    try:
        yaml_path.write_text(yaml.safe_dump(data, sort_keys=False))
    except OSError as e:
        return ArchiveAction(
            verb="failed",
            description=f"{yaml_path.name}: write",
            path=yaml_path,
            error=f"{type(e).__name__}: {e}",
        )
    return ArchiveAction(
        verb="removed",
        description=f"{yaml_path.name}: {name!r} entry",
        path=yaml_path,
    )


def _archive_feed(creature_id: str, *, feed_base: Optional[Path] = None, archive_base: Optional[Path] = None) -> ArchiveAction:
    """Move the per-Creature feed directory under the archive root.

    Renames the directory to include a UTC timestamp, so multiple
    archive cycles for the same creature_id don't collide.
    """
    feed_dir = feed_path_for(creature_id, base=feed_base).parent
    archive_dir = _archive_root(archive_base) / f"{creature_id}_{_now_stamp()}"

    if not feed_dir.exists():
        return ArchiveAction(
            verb="unchanged",
            description="observation feed (already absent)",
            path=feed_dir,
        )
    try:
        archive_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(feed_dir), str(archive_dir))
        return ArchiveAction(
            verb="archived",
            description="observation feed",
            path=archive_dir,
        )
    except OSError as e:
        return ArchiveAction(
            verb="failed",
            description="observation feed",
            path=feed_dir,
            error=f"{type(e).__name__}: {e}",
        )


def _archive_or_drop_pg_schema(
    schema_name: str, *, drop_data: bool, dsn: Optional[str] = None
) -> tuple[Optional[str], ArchiveAction]:
    """Best-effort schema rename or drop.

    Returns ``(pg_schema_action, archive_action)``: the action string
    summarizes what happened ("renamed" / "dropped" / None) for
    ArchiveResult.pg_schema_action; the ArchiveAction lands in actions[].
    """
    actual_dsn = dsn if dsn is not None else os.environ.get("DATABASE_URL")
    if not actual_dsn:
        return None, ArchiveAction(
            verb="skipped",
            description=f"pg schema {schema_name!r} (no $DATABASE_URL)",
        )
    try:
        import psycopg
        from psycopg import sql
    except ImportError:
        return None, ArchiveAction(
            verb="skipped",
            description=f"pg schema {schema_name!r} (psycopg unavailable)",
        )

    try:
        with psycopg.connect(actual_dsn, autocommit=True, connect_timeout=3) as conn:
            # Check the schema actually exists; nothing to do if not.
            row = conn.execute(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name = %s",
                (schema_name,),
            ).fetchone()
            if row is None:
                return None, ArchiveAction(
                    verb="unchanged",
                    description=f"pg schema {schema_name!r} (already absent)",
                )

            if drop_data:
                conn.execute(
                    sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema_name))
                )
                return "dropped", ArchiveAction(
                    verb="dropped",
                    description=f"pg schema {schema_name!r} (drop_data=True)",
                )

            archived_name = f"archived_{schema_name}_{_now_stamp()}"
            conn.execute(
                sql.SQL("ALTER SCHEMA {} RENAME TO {}").format(
                    sql.Identifier(schema_name), sql.Identifier(archived_name)
                )
            )
            return "renamed", ArchiveAction(
                verb="renamed",
                description=f"pg schema {schema_name!r} → {archived_name!r}",
            )
    except Exception as e:  # noqa: BLE001
        return None, ArchiveAction(
            verb="failed",
            description=f"pg schema {schema_name!r}",
            error=f"{type(e).__name__}: {e}",
        )


# ----------------------------- archive_power -----------------------------


def archive_power(
    name: str,
    *,
    target_dir: Optional[Path] = None,
) -> ArchiveResult:
    """Archive a power. Removes its systemd unit + reports.

    ``target_dir`` overrides the systemd unit dir for tests; defaults to
    ``~/.config/systemd/user/``.
    """
    actions: list[ArchiveAction] = []
    notes: list[str] = []

    units_dir = target_dir or systemd_user_dir()
    unit_path = units_dir / f"demiurge-power-{name}.service"

    actions.append(
        _try_remove_file(unit_path, f"systemd unit demiurge-power-{name}.service")
    )

    notes.append(
        "systemctl --user daemon-reload not invoked here — the operator "
        "(or a follow-on supervisor) should reload to make the unit removal "
        "take effect"
    )

    return ArchiveResult(
        creature_id=name,
        kind="power",
        actions=actions,
        notes=notes,
    )


# ----------------------------- archive_creature --------------------------


def _archive_creature(
    creature_id: str,
    *,
    expected_kind: str,
    schema_prefix: str,
    agents_yaml: Path,
    capabilities_yaml: Path,
    agents_dir: Optional[Path] = None,
    feed_base: Optional[Path] = None,
    archive_base: Optional[Path] = None,
    drop_data: bool = False,
) -> ArchiveResult:
    """Shared body for archive_mortal / archive_beast / archive_automaton.

    Order (continues on individual failures, recording each):
    1. Remove agent entry from agents.yaml.
    2. Remove agent entry (policy block) from capabilities.yaml.
    3. Remove agent .key + .env files.
    4. Archive observation feed (rename to archive root with timestamp).
    5. Archive or drop per-Creature pg schema.
    """
    actions: list[ArchiveAction] = []
    notes: list[str] = []

    # 1 + 2. Yaml entries.
    actions.append(_remove_agent_from_yaml(agents_yaml, creature_id))
    actions.append(_remove_agent_from_yaml(capabilities_yaml, creature_id))

    # 3. Keypair + env profile files.
    adir = agents_dir or default_agents_dir()
    actions.append(_try_remove_file(adir / f"{creature_id}.key", "agent private key"))
    actions.append(_try_remove_file(adir / f"{creature_id}.env", "agent env profile"))

    # 4. Observation feed.
    actions.append(_archive_feed(creature_id, feed_base=feed_base, archive_base=archive_base))

    # 5. pg schema.
    schema_id = creature_id.replace(".", "_")
    schema_full = f"{schema_prefix}_{schema_id}"
    pg_action_str, pg_action = _archive_or_drop_pg_schema(schema_full, drop_data=drop_data)
    actions.append(pg_action)

    return ArchiveResult(
        creature_id=creature_id,
        kind=expected_kind,
        actions=actions,
        notes=notes,
        pg_schema_action=pg_action_str,
    )


def archive_mortal(
    creature_id: str,
    *,
    agents_yaml: Path,
    capabilities_yaml: Path,
    agents_dir: Optional[Path] = None,
    feed_base: Optional[Path] = None,
    archive_base: Optional[Path] = None,
    drop_data: bool = False,
) -> ArchiveResult:
    """Archive a Mortal."""
    return _archive_creature(
        creature_id,
        expected_kind="mortal",
        schema_prefix="mortal",
        agents_yaml=agents_yaml,
        capabilities_yaml=capabilities_yaml,
        agents_dir=agents_dir,
        feed_base=feed_base,
        archive_base=archive_base,
        drop_data=drop_data,
    )


def archive_beast(
    creature_id: str,
    *,
    agents_yaml: Path,
    capabilities_yaml: Path,
    agents_dir: Optional[Path] = None,
    feed_base: Optional[Path] = None,
    archive_base: Optional[Path] = None,
    drop_data: bool = False,
) -> ArchiveResult:
    """Archive a Beast."""
    return _archive_creature(
        creature_id,
        expected_kind="beast",
        schema_prefix="beast",
        agents_yaml=agents_yaml,
        capabilities_yaml=capabilities_yaml,
        agents_dir=agents_dir,
        feed_base=feed_base,
        archive_base=archive_base,
        drop_data=drop_data,
    )


def archive_automaton(
    creature_id: str,
    *,
    agents_yaml: Path,
    capabilities_yaml: Path,
    agents_dir: Optional[Path] = None,
    feed_base: Optional[Path] = None,
    archive_base: Optional[Path] = None,
    drop_data: bool = False,
) -> ArchiveResult:
    """Archive an Automaton."""
    return _archive_creature(
        creature_id,
        expected_kind="automaton",
        schema_prefix="automaton",
        agents_yaml=agents_yaml,
        capabilities_yaml=capabilities_yaml,
        agents_dir=agents_dir,
        feed_base=feed_base,
        archive_base=archive_base,
        drop_data=drop_data,
    )


# ----------------------------- Pantheon-member transitions (stubs) -------


def fade_pantheon_member(name: str, **_) -> ArchiveResult:
    """Pantheon member is no longer broadly needed — formally Fade it.

    v0.11 stub: prints manual-intervention instructions. Real Fading
    needs careful migration of any data the god still owns + a
    confirmation that no other Creature depends on it. We don't have a
    fading god today; promote this when one appears.
    """
    return ArchiveResult(
        creature_id=name,
        kind="pantheon_member",
        actions=[
            ArchiveAction(
                verb="skipped",
                description="Fade is a manual operation in v0.11",
            )
        ],
        notes=[
            f"Fading {name!r} requires: (1) confirm no Creature depends on it, "
            f"(2) migrate any owned data to the successor god, (3) update "
            f"DEMIURGE.md §1.1 status. None of this is automated yet."
        ],
    )


def exile_pantheon_member(name: str, *, reason: str, **_) -> ArchiveResult:
    """Pantheon member pulled after a problem — formally Exile it.

    v0.11 stub: prints manual-intervention instructions. Real Exile
    requires capability revocation across all Creatures + an evidence-
    preservation pass. We don't have an exiled god today.
    """
    return ArchiveResult(
        creature_id=name,
        kind="pantheon_member",
        actions=[
            ArchiveAction(
                verb="skipped",
                description=f"Exile (reason: {reason}) is a manual operation in v0.11",
            )
        ],
        notes=[
            f"Exiling {name!r} requires: (1) revoke all capabilities the god "
            f"granted across every Creature (audit log review), (2) preserve "
            f"the god's audit + last state for postmortem, (3) update "
            f"DEMIURGE.md §1.1. None of this is automated yet."
        ],
    )


def ragnarok(name: str, *, confirm: bool = False, **_) -> ArchiveResult:
    """Pantheon member fully removed — Ragnarök.

    v0.11 stub. Ragnarök is the heaviest transition; needs Sol-and-only-
    Sol confirmation, no automation around it.
    """
    return ArchiveResult(
        creature_id=name,
        kind="pantheon_member",
        actions=[
            ArchiveAction(
                verb="skipped",
                description="Ragnarök is a manual operation in v0.11",
            )
        ],
        notes=[
            f"Ragnarök for {name!r} drops everything: code, audit, archive, "
            f"any reference in DEMIURGE.md. By design this requires Sol's "
            f"hand at every step. confirm={confirm} ignored in this stub."
        ],
    )
