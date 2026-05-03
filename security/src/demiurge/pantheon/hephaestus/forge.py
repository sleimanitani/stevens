"""Hephaestus's forge — turn a manifest into a runnable Creature.

v0.11 step 3d ships ``forge_power(manifest)``. Steps 3e onward add
``forge_mortal``, ``forge_beast``, ``forge_automaton``.

A *power* is a manifest whose ``kind`` is ``"power"`` and whose
``modes`` declare one or more of webhook / listener / polling /
request-based. Forging a power means:

1. Validate the manifest is the right kind.
2. For each declared *reactive* mode (webhook / listener / polling),
   convert the manifest's ``runtime`` block into a ``ServiceUnit`` and
   write it under ``~/.config/systemd/user/``.
3. Best-effort import + execute the manifest's ``bootstrap`` hook so
   the power can prepare its own state (sealed-store secrets, schema
   migrations, OAuth client registration, etc.) at install time.
4. Return a structured ``ForgeResult`` describing what was done.

Idempotent: re-forging a power that's already been forged with the
same manifest is a no-op (write_units reports each file as
``unchanged`` rather than rewriting). Differential re-forge (manifest
changed since last install) is detected via the systemd module's
``updated`` action — Hephaestus surfaces that to the operator so they
know to restart the unit.

Polling-mode powers in v0.11 emit a deferred-note rather than building
the systemd timer artifact: timer generation lands either in 3e
alongside the scheduler Automaton or as a focused 3d.1 follow-up.
Either way, no v0.11 power has polling-only mode today, so the
deferral has no operational impact.
"""

from __future__ import annotations

import importlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

from shared.creatures.dispatch import collect_blessings
from shared.creatures.feed import ObservationFeed
from shared.creatures.tools import GodlyBlessing, ToolRegistry
from shared.plugins.manifest import (
    Manifest,
    Mode,
)

from ...bootstrap.systemd import ServiceUnit, write_units
from ...bootstrap.postgres import env_file_path
from ...presets import Preset, PresetRule, merge_into_capabilities
from ...provision import provision_agent
from .gods import EnkiduGod
from .tool_routing import (
    DEFAULT_ROUTES,
    GodDispatcher,
    forge_blessed_registry,
)


class ForgeError(Exception):
    """Hephaestus refused to forge or hit a hard failure mid-forge."""


@dataclass(frozen=True)
class ForgeAction:
    """One systemd unit Hephaestus wrote (or noted as unchanged)."""

    path: Path
    verb: str  # "created" | "updated" | "unchanged" — same shape as bootstrap.systemd


@dataclass(frozen=True)
class ForgeResult:
    """Structured outcome of a forge call. Operator-readable + machine-readable.

    Mortal/Beast/Automaton forges populate the ``creature``-flavored fields
    (``agent_key_path``, ``policy_written``, ``pg_schema``, ``registry``,
    ``feed_path``, ``angel_specs``) in addition to the universal ones.
    Power forges leave them as defaults — Powers don't have agent
    identities or per-Creature schemas.
    """

    creature_id: str
    kind: str
    systemd_units: list[ForgeAction] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    bootstrap_executed: bool = False
    notes: list[str] = field(default_factory=list)
    """Operator-readable caveats — deferred-feature warnings, partial
    successes, anything Hephaestus thinks the operator should see."""

    # Creature-flavored fields (populated for Mortal/Beast/Automaton forges).
    agent_key_path: Optional[Path] = None
    policy_written: bool = False
    pg_schema: Optional[str] = None  # "mortal_<id>" / "beast_<id>" / "automaton_<id>"
    registry: Optional[ToolRegistry] = None
    feed_path: Optional[Path] = None
    angel_specs: list[Any] = field(default_factory=list)  # AngelSpec; Any to avoid import cycle in this annotation

    def format_report(self) -> str:
        lines = [f"Forged {self.kind} {self.creature_id!r}:"]
        if self.systemd_units:
            for action in self.systemd_units:
                symbol = {"created": "+", "updated": "~", "unchanged": "·"}.get(
                    action.verb, "?"
                )
                lines.append(f"  {symbol} {action.path.name}: {action.verb}")
        else:
            lines.append("  (no systemd units required)")
        if self.capabilities:
            lines.append(
                f"  capabilities exposed: {', '.join(self.capabilities)}"
            )
        if self.bootstrap_executed:
            lines.append("  bootstrap hook: executed")
        for note in self.notes:
            lines.append(f"  note: {note}")
        return "\n".join(lines)


# ----------------------------- mode → ServiceUnit ------------------------


def _service_unit_for_webhook(manifest: Manifest) -> ServiceUnit:
    """Webhook power → uvicorn unit binding to 127.0.0.1:<port>.

    The manifest's ``runtime.webhook.handler`` is treated as the uvicorn
    ``app`` reference (``module:attr`` of a FastAPI/Starlette app).
    Binding to 127.0.0.1 is deliberate — webhooks are meant to be
    fronted by a tunnel (Tailscale Funnel, Cloudflare Tunnel) that
    handles TLS + the public reachability story.
    """
    rt = manifest.runtime.webhook  # type: ignore[union-attr]
    assert rt is not None  # validated by manifest schema (3a)
    exec_cmd = (
        f"uvicorn {rt.handler} --host 127.0.0.1 --port {rt.port}"
    )
    return ServiceUnit(
        name=f"demiurge-power-{manifest.name}",
        description=f"{manifest.display_name} — webhook power",
        exec_cmd=exec_cmd,
        after=("demiurge-security.service", "postgresql.service"),
    )


def _service_unit_for_listener(manifest: Manifest) -> ServiceUnit:
    """Listener power → long-running ``python -m`` unit.

    The manifest's ``runtime.listener.command`` is a ``module:attr``
    pointing at the listener loop. We invoke it via ``python -c`` so the
    ``module:attr`` shape works without requiring the plugin to ship a
    dedicated ``__main__.py``.
    """
    rt = manifest.runtime.listener  # type: ignore[union-attr]
    assert rt is not None
    module, attr = rt.command.split(":", 1)
    exec_cmd = (
        f'python -c "import asyncio, importlib; '
        f"m = importlib.import_module({module!r}); "
        f"asyncio.run(getattr(m, {attr!r})())\""
    )
    return ServiceUnit(
        name=f"demiurge-power-{manifest.name}",
        description=f"{manifest.display_name} — listener power",
        exec_cmd=exec_cmd,
        after=("demiurge-security.service", "postgresql.service"),
    )


# ----------------------------- bootstrap-hook executor -------------------


async def _run_bootstrap_hook(manifest: Manifest, *, dry_run: bool) -> tuple[bool, Optional[str]]:
    """Best-effort: import + call the manifest's bootstrap hook.

    Returns ``(executed, note)``. ``executed`` is True if the hook ran
    to completion. ``note`` is an operator-readable caveat (skipped, not
    importable, raised) — recorded in ForgeResult.notes regardless of
    success/failure so the operator sees the full story.

    The bootstrap hook signature is ``async def install(manifest) -> None``
    or ``def install(manifest) -> None`` — we call it with the full
    manifest so it can inspect secrets/sysdeps/etc. It receives no
    sealed-store handle in v0.11; that arrives in 3e once we have a
    real Hephaestus context to thread through.
    """
    if dry_run:
        return False, f"bootstrap hook {manifest.bootstrap!r} skipped (dry-run)"
    if not manifest.bootstrap:
        return False, "no bootstrap hook declared"

    try:
        module_path, attr = manifest.bootstrap.split(":", 1)
    except ValueError:
        return False, (
            f"bootstrap field {manifest.bootstrap!r} doesn't match "
            f"'module:attr' shape; skipping"
        )

    try:
        module = importlib.import_module(module_path)
    except (ImportError, ModuleNotFoundError) as e:
        return False, (
            f"bootstrap hook {manifest.bootstrap!r} not importable: "
            f"{type(e).__name__}: {e}"
        )

    hook = getattr(module, attr, None)
    if hook is None:
        return False, (
            f"bootstrap hook {manifest.bootstrap!r}: module has no "
            f"attribute {attr!r}"
        )
    if not callable(hook):
        return False, (
            f"bootstrap hook {manifest.bootstrap!r} is not callable"
        )

    try:
        result = hook(manifest)
        # Allow both sync and async hooks.
        import inspect

        if inspect.isawaitable(result):
            await result
    except Exception as e:  # noqa: BLE001 — operator wants to see anything that went wrong
        return False, (
            f"bootstrap hook {manifest.bootstrap!r} raised: "
            f"{type(e).__name__}: {e}"
        )

    return True, None


# ----------------------------- forge_power -------------------------------


async def forge_power(
    manifest: Manifest,
    *,
    repo_root: Path,
    target_dir: Optional[Path] = None,
    env_file: Optional[Path] = None,
    skip_bootstrap_hook: bool = False,
) -> ForgeResult:
    """Forge a power. Validates kind, generates systemd units per the
    manifest's ``modes`` + ``runtime`` block, runs the bootstrap hook,
    returns a structured ``ForgeResult``.

    Idempotent: a second call with the same manifest reports each
    systemd unit as ``unchanged``. Differential re-forge (manifest
    edited since last forge) shows ``updated`` and a note instructing
    the operator to ``systemctl --user restart demiurge-power-<name>``.

    ``repo_root`` is required — the systemd unit's ``WorkingDirectory=``
    points there. ``target_dir`` defaults to ``~/.config/systemd/user/``.
    ``env_file`` defaults to ``~/.config/demiurge/env``.
    """
    if manifest.kind != "power":
        raise ForgeError(
            f"forge_power: expected manifest kind='power', got {manifest.kind!r}"
        )

    services: list[ServiceUnit] = []
    notes: list[str] = []

    for mode in manifest.modes or []:
        if mode == Mode.WEBHOOK:
            services.append(_service_unit_for_webhook(manifest))
        elif mode == Mode.LISTENER:
            services.append(_service_unit_for_listener(manifest))
        elif mode == Mode.POLLING:
            notes.append(
                "polling mode: systemd timer artifact deferred (no v0.11 power "
                "currently uses polling-only); will be wired in step 3e or "
                "3d.1 alongside the scheduler Automaton"
            )
        elif mode == Mode.REQUEST_BASED:
            # Pure outbound — no runtime artifact needed. The capabilities
            # the manifest exposes get registered with the capability
            # registry by the power's bootstrap hook (or static import).
            pass

    actions: list[ForgeAction] = []
    if services:
        ef = env_file or env_file_path()
        write_actions = write_units(
            repo_root=repo_root,
            target_dir=target_dir,
            env_file=ef,
            services=services,
        )
        for path, verb in write_actions:
            actions.append(ForgeAction(path=path, verb=verb))
            if verb == "updated":
                notes.append(
                    f"{path.name} changed since last forge — restart with "
                    f"`systemctl --user restart {path.stem}`"
                )

    bootstrap_executed, bootstrap_note = await _run_bootstrap_hook(
        manifest, dry_run=skip_bootstrap_hook
    )
    if bootstrap_note:
        notes.append(bootstrap_note)

    return ForgeResult(
        creature_id=manifest.name,
        kind="power",
        systemd_units=actions,
        capabilities=list(manifest.capabilities),
        bootstrap_executed=bootstrap_executed,
        notes=notes,
    )


# ----------------------------- forge_mortal ------------------------------


_INSTANCE_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _build_creature_id(manifest_name: str, instance_id: str) -> str:
    """``<manifest>.<instance>`` — deterministic per (name, instance) pair.

    Same (name, instance) → same creature_id → same agent identity, same
    Postgres schema, same observation feed path. Different instance_id →
    different creature_id and a fresh forge.

    instance_id is operator-supplied via ``demiurge hire spawn`` — e.g.
    "personal" / "work" for an email_pm Mortal, or
    "tokyo_2026" for a trip_planner. snake_case alnum, lowercase.
    """
    if not _INSTANCE_ID_RE.match(instance_id):
        raise ForgeError(
            f"instance_id {instance_id!r} must be snake_case "
            f"lowercase-alnum (matches {_INSTANCE_ID_RE.pattern!r})"
        )
    return f"{manifest_name}.{instance_id}"


def _manifest_to_preset(manifest: Manifest, creature_id: str) -> Preset:
    """Convert a Mortal manifest's ``capabilities:`` list into a Preset.

    Preset shape is what ``merge_into_capabilities`` expects: a list of
    PresetRule(capability, accounts=[]) — accounts left empty for now,
    since v0.11 manifests don't have a per-capability scope field. (When
    the manifest gains a `scope:` block — likely v0.11.x — this is the
    place to honor it.)
    """
    rules = [PresetRule(capability=cap, accounts=[]) for cap in manifest.capabilities]
    return Preset(name=f"manifest:{creature_id}", allow=rules)


def _create_pg_schema_if_configured(
    schema_name: str, *, dsn: Optional[str] = None
) -> tuple[bool, Optional[str]]:
    """Best-effort ``CREATE SCHEMA IF NOT EXISTS <schema_name>``.

    DSN comes from ``$DATABASE_URL`` by default (the same one bootstrap
    wrote to ``~/.config/demiurge/env``). Returns ``(created, note)`` —
    ``created`` is True if the schema actually exists (whether we created
    it or it was already there); ``note`` is an operator-readable string
    when we couldn't try (no DSN) or when the attempt failed.
    """
    actual_dsn = dsn if dsn is not None else os.environ.get("DATABASE_URL")
    if not actual_dsn:
        return False, "Postgres schema not created (no $DATABASE_URL)"

    try:
        import psycopg
    except ImportError:
        return False, "Postgres schema not created (psycopg not available)"

    try:
        with psycopg.connect(actual_dsn, autocommit=True, connect_timeout=3) as conn:
            # Quote the identifier safely. psycopg.sql is the right tool.
            from psycopg import sql

            conn.execute(
                sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(
                    sql.Identifier(schema_name)
                )
            )
    except Exception as e:  # noqa: BLE001
        return False, f"Postgres schema {schema_name!r} create failed: {type(e).__name__}: {e}"

    return True, None


async def _forge_creature(
    manifest: Manifest,
    *,
    expected_kind: str,
    schema_prefix: str,
    instance_id: str,
    repo_root: Path,
    agents_yaml: Path,
    capabilities_yaml: Path,
    gods: Optional[Mapping[str, GodlyBlessing]],
    dispatchers: Optional[Mapping[str, GodDispatcher]],
    routes: Mapping[str, str],
    feed_base: Optional[Path],
    agents_dir: Optional[Path],
    socket_path: str,
    create_pg_schema: bool,
    skip_bootstrap_hook: bool,
    force: bool,
) -> ForgeResult:
    """Shared implementation for ``forge_mortal`` / ``forge_beast`` /
    ``forge_automaton``.

    All three Creature kinds go through the same forge: agent identity +
    policy block + ToolRegistry + observation feed + per-Creature
    Postgres schema (best-effort) + bootstrap hook. The differences are
    in what's *missing* downstream — the supervisor (step 7) builds a
    different Context type per kind, but the artifact production is
    identical.

    - Mortals get a MortalContext (llm + tools + memory + bus).
    - Beasts get a BeastContext (model only).
    - Automatons get an AutomatonContext (bus only).

    Hephaestus produces the artifacts here; the supervisor consumes them.
    """
    if manifest.kind != expected_kind:
        raise ForgeError(
            f"forge_{expected_kind}: expected manifest kind={expected_kind!r}, "
            f"got {manifest.kind!r}"
        )

    if gods is None or dispatchers is None:
        raise ForgeError(
            f"forge_{expected_kind}: must supply both 'gods' and 'dispatchers'. "
            f"Caller (typically `demiurge hire spawn`) is responsible for "
            f"the per-deployment binding."
        )

    notes: list[str] = []
    creature_id = _build_creature_id(manifest.name, instance_id)
    pg_schema_full = f"{schema_prefix}_{creature_id.replace('.', '_')}"

    # 3. Write policy block.
    preset = _manifest_to_preset(manifest, creature_id)
    try:
        policy_changed = merge_into_capabilities(
            capabilities_yaml, creature_id, preset
        )
    except Exception as e:  # noqa: BLE001 — surface PresetError too
        raise ForgeError(
            f"failed to write policy block for {creature_id!r}: {e}"
        ) from e
    if policy_changed:
        notes.append(
            f"policy block for {creature_id!r} merged into "
            f"{capabilities_yaml.name}"
        )

    # 4. Provision agent identity.
    try:
        provision_result = provision_agent(
            name=creature_id,
            preset_name=None,
            agents_yaml=agents_yaml,
            capabilities_yaml=capabilities_yaml,
            agents_dir=agents_dir,
            socket_path=socket_path,
            force=force,
        )
    except Exception as e:  # noqa: BLE001
        raise ForgeError(
            f"agent identity provisioning failed for {creature_id!r}: {e}"
        ) from e

    # 5. Reload Enkidu's policy view + collect blessings.
    enkidu_god = gods.get("enkidu")
    if isinstance(enkidu_god, EnkiduGod):
        from ...policy import load_policy

        enkidu_god.policy = load_policy(capabilities_yaml)

    blessing_result = await collect_blessings(
        creature_id=creature_id,
        capabilities=list(manifest.capabilities),
        gods=dict(gods),
        routes=routes,
    )
    if not blessing_result.ok:
        raise ForgeError(
            f"blessing collection failed for {creature_id!r}:\n"
            f"{blessing_result.format_report()}"
        )

    # 6. Compose ToolRegistry. Universal tools included for Mortals; for
    # Beasts/Automatons we still include them (no harm in `think`/`return`
    # being available, even if they're rarely useful for non-agency
    # creatures — Beasts don't have a monologue loop to think within, but
    # `think` still produces a useful audit trace if invoked).
    registry = forge_blessed_registry(
        blessings=blessing_result.blessings,
        dispatchers=dict(dispatchers),
    )

    # 7. Create observation feed.
    feed = ObservationFeed(creature_id, base=feed_base)

    # 8. Postgres schema (best-effort).
    pg_schema: Optional[str] = None
    if create_pg_schema:
        ok, pg_note = _create_pg_schema_if_configured(pg_schema_full)
        if ok:
            pg_schema = pg_schema_full
        if pg_note:
            notes.append(pg_note)

    # 9. Bootstrap hook (best-effort).
    bootstrap_executed, bootstrap_note = await _run_bootstrap_hook(
        manifest, dry_run=skip_bootstrap_hook
    )
    if bootstrap_note:
        notes.append(bootstrap_note)

    return ForgeResult(
        creature_id=creature_id,
        kind=expected_kind,
        systemd_units=[],
        capabilities=list(manifest.capabilities),
        bootstrap_executed=bootstrap_executed,
        notes=notes,
        agent_key_path=provision_result.key_path,
        policy_written=True,
        pg_schema=pg_schema,
        registry=registry,
        feed_path=feed.path,
        angel_specs=[],
    )


async def forge_mortal(
    manifest: Manifest,
    *,
    instance_id: str,
    repo_root: Path,
    agents_yaml: Path,
    capabilities_yaml: Path,
    gods: Optional[Mapping[str, GodlyBlessing]] = None,
    dispatchers: Optional[Mapping[str, GodDispatcher]] = None,
    routes: Mapping[str, str] = DEFAULT_ROUTES,
    feed_base: Optional[Path] = None,
    agents_dir: Optional[Path] = None,
    socket_path: str = "/run/demiurge/security.sock",
    create_pg_schema: bool = True,
    skip_bootstrap_hook: bool = False,
    force: bool = False,
) -> ForgeResult:
    """Forge a Mortal. Idempotent (with ``force=True`` for re-keying).

    Order of operations (fail-fast):

    1. Validate manifest is ``kind="mortal"``.
    2. Build deterministic ``creature_id`` from ``manifest.name`` +
       ``instance_id`` (e.g. ``"email_pm.personal"``).
    3. Write a policy block to ``capabilities.yaml`` under that creature_id
       — converts the manifest's ``capabilities:`` list into a Preset and
       merges via the existing ``merge_into_capabilities``. Idempotent: if
       the policy is already there with the same shape, no-op.
    4. Provision the agent identity via the existing ``provision_agent``
       (keypair + agents.yaml entry + env profile). Re-running raises
       ``ProvisionError`` unless ``force=True`` (silent key rotation is
       the kind of thing this architecture deliberately prevents).
    5. Collect blessings against the gods to verify the policy actually
       grants what the manifest declares. If any required capability is
       denied here, the forge fails.
    6. Compose the ``ToolRegistry`` via ``forge_blessed_registry``.
    7. Create the observation feed for the creature_id.
    8. (Best-effort, gated on ``$DATABASE_URL``) Create the
       ``mortal_<creature_id>`` Postgres schema.
    9. Run the manifest's bootstrap hook (best-effort; failures recorded
       as notes).
    10. Return a ``ForgeResult`` with all the pieces a future supervisor
        needs to instantiate the Mortal.

    Hephaestus does *not* instantiate the Mortal subclass or run its
    monologue loop here — that's the supervisor (v0.11 step 7). This
    function produces the artifacts; the supervisor consumes them.
    """
    return await _forge_creature(
        manifest,
        expected_kind="mortal",
        schema_prefix="mortal",
        instance_id=instance_id,
        repo_root=repo_root,
        agents_yaml=agents_yaml,
        capabilities_yaml=capabilities_yaml,
        gods=gods,
        dispatchers=dispatchers,
        routes=routes,
        feed_base=feed_base,
        agents_dir=agents_dir,
        socket_path=socket_path,
        create_pg_schema=create_pg_schema,
        skip_bootstrap_hook=skip_bootstrap_hook,
        force=force,
    )


# ----------------------------- forge_beast -------------------------------


async def forge_beast(
    manifest: Manifest,
    *,
    instance_id: str,
    repo_root: Path,
    agents_yaml: Path,
    capabilities_yaml: Path,
    gods: Optional[Mapping[str, GodlyBlessing]] = None,
    dispatchers: Optional[Mapping[str, GodDispatcher]] = None,
    routes: Mapping[str, str] = DEFAULT_ROUTES,
    feed_base: Optional[Path] = None,
    agents_dir: Optional[Path] = None,
    socket_path: str = "/run/demiurge/security.sock",
    create_pg_schema: bool = True,
    skip_bootstrap_hook: bool = False,
    force: bool = False,
) -> ForgeResult:
    """Forge a Beast. Same pipeline as ``forge_mortal``; the difference
    lives in the Context the supervisor (v0.11 step 7) builds — Beasts
    get a ``BeastContext`` (model handle only, no LLM/memory/bus loop).

    Beasts are stochastic Creatures with model output but no agency:
    image generators, summarizers, embedders, classifiers, OCR, etc.
    They're invoked function-style (``await beast.transform(input)``)
    and don't run a monologue loop. Capabilities they declare are
    typically upstream-API calls they need (e.g. an image_gen Beast
    may need ``web.fetch`` to call its model provider).
    """
    return await _forge_creature(
        manifest,
        expected_kind="beast",
        schema_prefix="beast",
        instance_id=instance_id,
        repo_root=repo_root,
        agents_yaml=agents_yaml,
        capabilities_yaml=capabilities_yaml,
        gods=gods,
        dispatchers=dispatchers,
        routes=routes,
        feed_base=feed_base,
        agents_dir=agents_dir,
        socket_path=socket_path,
        create_pg_schema=create_pg_schema,
        skip_bootstrap_hook=skip_bootstrap_hook,
        force=force,
    )


# ----------------------------- forge_automaton ---------------------------


async def forge_automaton(
    manifest: Manifest,
    *,
    instance_id: str,
    repo_root: Path,
    agents_yaml: Path,
    capabilities_yaml: Path,
    gods: Optional[Mapping[str, GodlyBlessing]] = None,
    dispatchers: Optional[Mapping[str, GodDispatcher]] = None,
    routes: Mapping[str, str] = DEFAULT_ROUTES,
    feed_base: Optional[Path] = None,
    agents_dir: Optional[Path] = None,
    socket_path: str = "/run/demiurge/security.sock",
    create_pg_schema: bool = True,
    skip_bootstrap_hook: bool = False,
    force: bool = False,
) -> ForgeResult:
    """Forge an Automaton. Same pipeline as ``forge_mortal``; the
    difference lives in the Context the supervisor (v0.11 step 7)
    builds — Automatons get an ``AutomatonContext`` (bus only, no
    LLM/memory/model).

    Automatons are deterministic, no-LLM Creatures invoked on a
    schedule or by a bus event. Examples: scheduler, RSS poller, log
    shipper. They typically declare a small set of capabilities (often
    just ``bus.*`` to publish events) and have no agency.

    Automatons still get a Postgres schema even though most don't need
    persistent state — a stateful Automaton (e.g. a deduplicating
    poller) will use it; stateless ones (the scheduler) leave it empty.
    Cheap to create, no harm if unused.
    """
    return await _forge_creature(
        manifest,
        expected_kind="automaton",
        schema_prefix="automaton",
        instance_id=instance_id,
        repo_root=repo_root,
        agents_yaml=agents_yaml,
        capabilities_yaml=capabilities_yaml,
        gods=gods,
        dispatchers=dispatchers,
        routes=routes,
        feed_base=feed_base,
        agents_dir=agents_dir,
        socket_path=socket_path,
        create_pg_schema=create_pg_schema,
        skip_bootstrap_hook=skip_bootstrap_hook,
        force=force,
    )
