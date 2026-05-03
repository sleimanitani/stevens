"""``demiurge hire`` CLI handlers — v0.11 step 6.

Operator surface for Creature lifecycle (Mortals, Beasts, Automatons).
Wires together:

- ``shared.plugins.discovery`` — entry-point discovery of installable
  Creature plugins (step 2).
- ``demiurge.pantheon.hephaestus`` — forge_mortal / forge_beast /
  forge_automaton (step 3e.1–.2).
- ``demiurge.pantheon.hades`` — archive_mortal / archive_beast /
  archive_automaton (step 4).

Subcommands:

- ``demiurge hire list`` — currently spawned Creatures (any kind).
- ``demiurge hire registry`` — catalog of installable Mortal/Beast/
  Automaton plugins (via discovery).
- ``demiurge hire install <name>`` — install a Creature plugin (alias
  of ``spawn``; the difference is naming conventions: "install" means
  a long-lived Creature, "spawn" means short-lived/task-scoped).
- ``demiurge hire spawn <name> --instance <id>`` — forge a Creature
  with a fresh instance_id.
- ``demiurge hire show <creature_id>`` — manifest + capabilities of
  one spawned Creature.
- ``demiurge hire retire <creature_id>`` — archive a Creature via Hades.
- ``demiurge hire pause <id>`` / ``resume <id>`` — supervisor control
  (deferred-stub until v0.11 step 7).

State tracking in v0.11 is file-system based: agents.yaml lists
spawned creature_ids; a per-Creature env profile lives at
``~/.config/demiurge/agents/<creature_id>.env``. v0.11.x adds a real
``mortals`` Postgres table for richer queries (lifecycle history,
parent/child relationships when those land).

Dispatchers used at forge time: v0.11's CLI handlers wire **placeholder
dispatchers** for every god — they error loudly if a tool wrapper is
ever invoked. That's correct for v0.11: the supervisor (step 7) is what
runs Mortals; without it nobody calls the dispatchers. v0.11.x replaces
the placeholder with a real Enkidu-UDS dispatcher.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from pathlib import Path
from typing import Optional

import yaml

from shared.plugins.discovery import (
    DiscoveryError,
    DiscoveryResult,
    InstalledPlugin,
    discover,
)


# ----------------------------- placeholder dispatchers -------------------


async def _placeholder_dispatcher(ctx, *, capability, blessing, **kwargs):
    """Errors loudly if invoked. Wired by every CLI forge call.

    The forge composes a ToolRegistry but does not invoke any tool at
    forge time, so this is never called during ``demiurge hire spawn``
    itself. It would only fire if something tried to call a blessed
    tool inside the forging Mortal's context — which the v0.11 CLI
    doesn't do (the supervisor in step 7 will).
    """
    raise RuntimeError(
        f"capability {capability!r} is registered but the runtime "
        "supervisor isn't live yet (v0.11 step 7). The forge succeeded; "
        "the call would route through a real dispatcher once the "
        "supervisor runs."
    )


def _default_gods_for_forge(capabilities_yaml: Path):
    """Build the default gods + dispatchers map for a forge call.

    All gods are wired (Enkidu real, others stub or blanket-allow); all
    dispatchers are placeholders. Real Enkidu-UDS dispatchers land later.
    """
    from .pantheon.hephaestus.gods import (
        ArachneGod,
        EnkiduGod,
        IrisStubGod,
        JanusGod,
        MnemosyneStubGod,
        SphinxGod,
        ZeusStubGod,
    )
    from .policy import load_policy

    enkidu = EnkiduGod(policy=load_policy(capabilities_yaml))
    gods = {
        "enkidu": enkidu,
        "arachne": ArachneGod(),
        "sphinx": SphinxGod(),
        "janus": JanusGod(),
        "mnemosyne": MnemosyneStubGod(),
        "iris": IrisStubGod(),
        "zeus": ZeusStubGod(),
    }
    dispatchers = {name: _placeholder_dispatcher for name in gods.keys()}
    return gods, dispatchers


# ----------------------------- list / registry ---------------------------


_CREATURE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]*\.[a-z][a-z0-9_]*$")


def _spawned_creature_ids(agents_yaml: Path) -> list[str]:
    """Read agents.yaml and pick out names matching the
    ``<manifest>.<instance>`` shape — those are spawned Creatures."""
    if not agents_yaml.exists():
        return []
    data = yaml.safe_load(agents_yaml.read_text()) or {}
    if not isinstance(data, dict):
        return []
    agents = data.get("agents") or []
    if not isinstance(agents, list):
        return []
    creature_ids = []
    for entry in agents:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str):
            continue
        if _CREATURE_ID_RE.match(name):
            creature_ids.append(name)
    return sorted(creature_ids)


def cmd_hire_list(args: argparse.Namespace) -> int:
    """Print every spawned Creature found in agents.yaml."""
    agents_yaml = args.agents_yaml or _default_agents_yaml()
    spawned = _spawned_creature_ids(agents_yaml)
    if not spawned:
        print(
            "(no Creatures spawned yet — see `demiurge hire registry` "
            "for the catalog of installable plugins)"
        )
        return 0

    print(f"Spawned Creatures ({len(spawned)}):")
    for cid in spawned:
        print(f"  {cid}")
    return 0


def cmd_hire_registry(args: argparse.Namespace) -> int:
    """Print the catalog of installable Mortal/Beast/Automaton plugins."""
    result: DiscoveryResult = discover("mortal")
    if not result.plugins and not result.errors:
        print("(no Creature plugins installed via entry points yet)")
        return 0

    if result.plugins:
        print(f"Installable Creatures ({len(result.plugins)}):")
        for p in sorted(result.plugins, key=lambda x: x.name):
            kind = p.manifest.kind
            caps = ", ".join(p.manifest.capabilities) if p.manifest.capabilities else "—"
            print(f"  {p.name:<24}  kind={kind:<10}  caps=[{caps}]")

    if result.errors:
        print(f"\nBroken Creature plugins ({len(result.errors)}):", file=sys.stderr)
        for e in result.errors:
            print(f"  ✗ {e.name}: {e.error}", file=sys.stderr)

    return 0 if not result.errors else 1


# ----------------------------- show -------------------------------------


def cmd_hire_show(args: argparse.Namespace) -> int:
    """Print the manifest of one spawned Creature."""
    creature_id = args.creature_id
    agents_yaml = args.agents_yaml or _default_agents_yaml()

    spawned = _spawned_creature_ids(agents_yaml)
    if creature_id not in spawned:
        print(
            f"no spawned Creature with id {creature_id!r}.\n"
            f"see `demiurge hire list` for what's spawned.",
            file=sys.stderr,
        )
        return 1

    # Locate the manifest via the manifest-name prefix.
    manifest_name = creature_id.rsplit(".", 1)[0]
    result: DiscoveryResult = discover("mortal")
    plugin: Optional[InstalledPlugin] = next(
        (p for p in result.plugins if p.name == manifest_name), None
    )
    if plugin is None:
        # The Creature is spawned (in agents.yaml) but its manifest
        # plugin isn't currently importable. Common case: the plugin
        # was uninstalled but its forged Creatures linger. We can still
        # show what we know from agents.yaml.
        print(f"Creature: {creature_id}")
        print("  manifest plugin not currently installed — limited info available")
        print(f"  (consider `demiurge hire retire {creature_id}` if no longer needed)")
        return 0

    m = plugin.manifest
    lines = [
        f"Creature: {creature_id}",
        f"  manifest:    {m.name} ({m.kind})",
        f"  display:     {m.display_name}",
        f"  version:     {m.version}",
        f"  capabilities: {', '.join(m.capabilities) if m.capabilities else '—'}",
    ]
    if m.powers:
        lines.append(f"  powers:      {', '.join(m.powers)}")
    if m.bootstrap:
        lines.append(f"  bootstrap:   {m.bootstrap}")
    print("\n".join(lines))
    return 0


# ----------------------------- spawn / install ---------------------------


def _resolve_creature_manifest(name: str, *, from_yaml: Optional[str]):
    """Resolve a Creature manifest from --from-yaml or from entry points.

    Returns the parsed Manifest. Raises ``SystemExit(rc)`` with a clear
    stderr message on failure.
    """
    from shared.plugins.manifest import load_manifest_from_yaml

    if from_yaml:
        manifest = load_manifest_from_yaml(Path(from_yaml))
        if manifest.name != name:
            print(
                f"manifest declares name={manifest.name!r}, but you asked "
                f"for {name!r}",
                file=sys.stderr,
            )
            raise SystemExit(2)
        return manifest

    result = discover("mortal")
    plugin = next((p for p in result.plugins if p.name == name), None)
    if plugin is None:
        broken = next((e for e in result.errors if e.name == name), None)
        if broken is not None:
            print(
                f"Creature plugin {name!r} is registered but broken: "
                f"{broken.error}\n"
                f"resolve the import error or pass --from-yaml <plugin.yaml>",
                file=sys.stderr,
            )
            raise SystemExit(2)
        print(
            f"no Creature plugin {name!r} discoverable via entry points.\n"
            f"if you have a local plugin.yaml, pass --from-yaml <path>.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return plugin.manifest


def cmd_hire_spawn(args: argparse.Namespace) -> int:
    """Spawn a Creature: forge_mortal/beast/automaton via Hephaestus."""
    from .pantheon.hephaestus import forge_automaton, forge_beast, forge_mortal

    try:
        manifest = _resolve_creature_manifest(args.name, from_yaml=args.from_yaml)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 1

    agents_yaml = args.agents_yaml or _default_agents_yaml()
    capabilities_yaml = args.capabilities_yaml or _default_capabilities_yaml()
    repo_root = Path(args.repo_root) if args.repo_root else Path.cwd()
    agents_dir = Path(args.agents_dir) if args.agents_dir else None

    gods, dispatchers = _default_gods_for_forge(capabilities_yaml)

    forge_fn = {
        "mortal": forge_mortal,
        "beast": forge_beast,
        "automaton": forge_automaton,
    }.get(manifest.kind)
    if forge_fn is None:
        print(
            f"manifest kind={manifest.kind!r} is not spawnable via "
            f"`demiurge hire`. Powers use `demiurge powers install`.",
            file=sys.stderr,
        )
        return 2

    try:
        result = asyncio.run(
            forge_fn(
                manifest,
                instance_id=args.instance_id,
                repo_root=repo_root,
                agents_yaml=agents_yaml,
                capabilities_yaml=capabilities_yaml,
                agents_dir=agents_dir,
                gods=gods,
                dispatchers=dispatchers,
                create_pg_schema=not args.skip_pg_schema,
                skip_bootstrap_hook=args.skip_bootstrap_hook,
                force=args.force,
            )
        )
    except Exception as e:  # noqa: BLE001 — surface ForgeError + anything else
        print(f"forge failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    print(f"Spawned {result.kind} {result.creature_id!r}")
    if result.agent_key_path:
        print(f"  agent key: {result.agent_key_path}")
    if result.pg_schema:
        print(f"  pg schema: {result.pg_schema}")
    if result.feed_path:
        print(f"  observation feed: {result.feed_path}")
    if result.capabilities:
        print(f"  capabilities: {', '.join(result.capabilities)}")
    for note in result.notes:
        print(f"  note: {note}")
    return 0


# ----------------------------- retire -----------------------------------


def cmd_hire_retire(args: argparse.Namespace) -> int:
    """Retire a Creature: archive_mortal/beast/automaton via Hades."""
    from .pantheon.hades import archive_automaton, archive_beast, archive_mortal

    creature_id = args.creature_id
    agents_yaml = args.agents_yaml or _default_agents_yaml()
    capabilities_yaml = args.capabilities_yaml or _default_capabilities_yaml()
    agents_dir = Path(args.agents_dir) if args.agents_dir else None

    # Determine kind from the manifest (if discoverable). Fall back to
    # mortal — the archive paths for mortal/beast/automaton differ only
    # in the Postgres schema name prefix; the file/yaml work is identical.
    manifest_name = creature_id.rsplit(".", 1)[0] if "." in creature_id else creature_id
    result_disc: DiscoveryResult = discover("mortal")
    plugin = next((p for p in result_disc.plugins if p.name == manifest_name), None)
    kind = plugin.manifest.kind if plugin is not None else "mortal"

    archive_fn = {
        "mortal": archive_mortal,
        "beast": archive_beast,
        "automaton": archive_automaton,
    }.get(kind, archive_mortal)

    result = archive_fn(
        creature_id,
        agents_yaml=agents_yaml,
        capabilities_yaml=capabilities_yaml,
        agents_dir=agents_dir,
        drop_data=args.drop_data,
    )
    print(result.format_report())
    return 0 if result.ok else 1


# ----------------------------- pause / resume (deferred) -----------------


def _send_runtime_request(op: str, creature_id: str) -> int:
    """Shared CLI helper: connect to the runtime daemon's UDS and dispatch."""
    from .runtime.daemon import default_socket_path, send_request

    sock = default_socket_path()
    try:
        resp = send_request({"op": op, "creature_id": creature_id}, socket_path=sock)
    except (ConnectionRefusedError, FileNotFoundError):
        print(
            f"runtime daemon is not running (no socket at {sock}).\n"
            f"start it with: systemctl --user start demiurge-runtime",
            file=sys.stderr,
        )
        return 1
    if not resp.get("ok"):
        print(f"daemon refused: {resp.get('error')}", file=sys.stderr)
        return 1
    return 0


def cmd_hire_pause(args: argparse.Namespace) -> int:
    """SIGTERM the Creature's process and block its restart.

    Talks to the running ``demiurge-runtime`` daemon over its UDS.
    The daemon's Supervisor.pause() does the actual signaling; this
    CLI just relays the request.
    """
    rc = _send_runtime_request("pause", args.creature_id)
    if rc == 0:
        print(f"paused {args.creature_id}")
    return rc


def cmd_hire_resume(args: argparse.Namespace) -> int:
    """Resume a paused Creature."""
    rc = _send_runtime_request("resume", args.creature_id)
    if rc == 0:
        print(f"resumed {args.creature_id}")
    return rc


# ----------------------------- defaults helpers --------------------------


def _default_agents_yaml() -> Path:
    """Lazy lookup so test injection still works."""
    return Path(
        os.environ.get("DEMIURGE_SECURITY_AGENTS", "security/policy/agents.yaml")
    )


def _default_capabilities_yaml() -> Path:
    return Path(
        os.environ.get(
            "DEMIURGE_SECURITY_POLICY", "security/policy/capabilities.yaml"
        )
    )


# ----------------------------- argparse wiring ---------------------------


def add_hire_parser(top: argparse._SubParsersAction) -> None:
    """Attach the `demiurge hire` subcommand tree to the top-level parser."""
    hire = top.add_parser(
        "hire",
        help="manage spawned Creatures (Mortals, Beasts, Automatons)",
        description=(
            "Hire is the operator surface for Creature lifecycle. Use "
            "`spawn` to forge a fresh instance from an installed plugin, "
            "`list` to see what's spawned, `retire` to archive."
        ),
    )
    sub = hire.add_subparsers(dest="subcmd", required=True)

    h_list = sub.add_parser("list", help="spawned Creatures")
    h_list.add_argument("--agents-yaml", help="override agents.yaml path")
    h_list.set_defaults(fn=cmd_hire_list)

    h_reg = sub.add_parser(
        "registry", help="installable Creature plugins (via entry points)"
    )
    h_reg.set_defaults(fn=cmd_hire_registry)

    h_show = sub.add_parser("show", help="manifest details for one spawned Creature")
    h_show.add_argument("creature_id", help="<manifest>.<instance> form")
    h_show.add_argument("--agents-yaml", help="override agents.yaml path")
    h_show.set_defaults(fn=cmd_hire_show)

    for spawn_alias in ("spawn", "install"):
        h_spawn = sub.add_parser(
            spawn_alias,
            help=(
                "spawn a fresh Creature instance"
                if spawn_alias == "spawn"
                else "install a Creature (alias of `spawn`)"
            ),
        )
        h_spawn.add_argument("name", help="Creature manifest name (e.g. email_pm)")
        h_spawn.add_argument(
            "--instance",
            "--instance-id",
            dest="instance_id",
            required=True,
            help="instance suffix (snake_case lowercase alnum)",
        )
        h_spawn.add_argument("--from-yaml", help="explicit path to plugin.yaml")
        h_spawn.add_argument("--agents-yaml", help="override agents.yaml")
        h_spawn.add_argument("--capabilities-yaml", help="override capabilities.yaml")
        h_spawn.add_argument("--agents-dir", help="override agents dir for keys")
        h_spawn.add_argument(
            "--repo-root", help="repo root (default: cwd)",
        )
        h_spawn.add_argument(
            "--skip-pg-schema",
            action="store_true",
            help="don't create the per-Creature Postgres schema",
        )
        h_spawn.add_argument(
            "--skip-bootstrap-hook",
            action="store_true",
            help="don't run the manifest's bootstrap hook",
        )
        h_spawn.add_argument(
            "--force",
            action="store_true",
            help="re-key an existing identity (rotation)",
        )
        h_spawn.set_defaults(fn=cmd_hire_spawn)

    h_ret = sub.add_parser("retire", help="archive a Creature (Hades)")
    h_ret.add_argument("creature_id")
    h_ret.add_argument("--agents-yaml")
    h_ret.add_argument("--capabilities-yaml")
    h_ret.add_argument("--agents-dir")
    h_ret.add_argument(
        "--drop-data",
        action="store_true",
        help="drop the Creature's pg schema instead of renaming/archiving",
    )
    h_ret.set_defaults(fn=cmd_hire_retire)

    h_pause = sub.add_parser("pause", help="pause a running Creature (deferred to step 7)")
    h_pause.add_argument("creature_id")
    h_pause.set_defaults(fn=cmd_hire_pause)

    h_resume = sub.add_parser("resume", help="resume a paused Creature (deferred to step 7)")
    h_resume.add_argument("creature_id")
    h_resume.set_defaults(fn=cmd_hire_resume)
