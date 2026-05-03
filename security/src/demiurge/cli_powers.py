"""``demiurge powers`` CLI handlers — v0.11 step 5.

Wires together:
- ``shared.plugins.discovery`` — entry-point discovery (step 2).
- ``demiurge.pantheon.hephaestus`` — forge_power (step 3d).
- ``demiurge.pantheon.hades`` — archive_power (step 4).

Subcommands:

- ``demiurge powers list`` — installed powers (via discovery).
- ``demiurge powers registry`` — curated catalog of known powers from
  ``resources/powers_registry.yaml`` (or the legacy
  ``channels_list._CHANNELS`` until that's migrated).
- ``demiurge powers install <name>`` — locate the plugin, run
  Hephaestus.forge_power.
- ``demiurge powers uninstall <name>`` — Hades.archive_power.
- ``demiurge powers show <name>`` — manifest + capabilities + scope.

Plus deprecated alias: ``demiurge channels …`` calls into the powers
handlers with a deprecation warning. The flag exists so existing
muscle memory still works through v0.11; v0.11.x or v0.12 may drop it.

This module is import-free of network/db side effects on import — the
work happens inside the ``cmd_*`` handler functions.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Optional

from shared.plugins.discovery import (
    DiscoveryResult,
    InstalledPlugin,
    discover,
)


# ----------------------------- list / registry ---------------------------


def cmd_powers_list(args: argparse.Namespace) -> int:
    """Print every installed power discovered via entry points.

    Failures (broken plugins) are printed after the success listing so
    the operator sees both — installed-and-working AND
    installed-but-broken in one view.
    """
    result: DiscoveryResult = discover("power")
    if not result.plugins and not result.errors:
        print("(no powers installed yet — see `demiurge powers registry` for the catalog)")
        return 0

    if result.plugins:
        print(f"Installed powers ({len(result.plugins)}):")
        for p in sorted(result.plugins, key=lambda x: x.name):
            modes = (
                ",".join(m.value for m in p.manifest.modes) if p.manifest.modes else "—"
            )
            print(
                f"  {p.name}  {p.dist_version:<10}  modes={modes}  "
                f"caps=[{', '.join(p.manifest.capabilities)}]"
            )

    if result.errors:
        print(f"\nBroken plugins ({len(result.errors)}):", file=sys.stderr)
        for e in result.errors:
            print(f"  ✗ {e.name} ({e.dist_name or '<unknown dist>'}): {e.error}", file=sys.stderr)

    return 0 if not result.errors else 1


def cmd_powers_registry(args: argparse.Namespace) -> int:
    """Print the curated catalog of known-but-not-necessarily-installed powers.

    For v0.11 the catalog is the legacy ``channels_list`` module — once
    we publish a real ``resources/powers_registry.yaml``, this shifts
    to read from there. The legacy reuse keeps `demiurge powers registry`
    operationally useful from day one.
    """
    from . import channels_list

    print("Known powers (catalog):")
    print(channels_list.render())
    return 0


# ----------------------------- show -------------------------------------


def cmd_powers_show(args: argparse.Namespace) -> int:
    """Print the manifest of one installed power."""
    name = args.name
    result: DiscoveryResult = discover("power")
    plugin = next((p for p in result.plugins if p.name == name), None)
    if plugin is None:
        # Maybe it's broken?
        broken = next((e for e in result.errors if e.name == name), None)
        if broken is not None:
            print(
                f"power {name!r} is registered but broken: {broken.error}",
                file=sys.stderr,
            )
            return 2
        print(f"no installed power named {name!r}", file=sys.stderr)
        return 1

    m = plugin.manifest
    lines = [
        f"power: {m.name}",
        f"  display_name: {m.display_name}",
        f"  version:      {m.version}",
        f"  source:       {m.source or '—'}",
        f"  maintainer:   {m.maintainer or '—'}",
        f"  modes:        {', '.join((md.value for md in (m.modes or [])))}",
        f"  capabilities: {', '.join(m.capabilities)}",
    ]
    if m.secrets:
        lines.append("  secrets:")
        for s in m.secrets:
            via = f" (onboard via: {s.onboard_via})" if s.onboard_via else ""
            lines.append(f"    - {s.name}{via}")
    if m.system_deps.apt or m.system_deps.brew:
        lines.append("  system_deps:")
        if m.system_deps.apt:
            lines.append(f"    apt: {', '.join(m.system_deps.apt)}")
        if m.system_deps.brew:
            lines.append(f"    brew: {', '.join(m.system_deps.brew)}")
    lines.append(f"  bootstrap:    {m.bootstrap or '—'}")
    lines.append(f"  dist:         {plugin.dist_name} {plugin.dist_version}")
    print("\n".join(lines))
    return 0


# ----------------------------- install / uninstall -----------------------


def cmd_powers_install(args: argparse.Namespace) -> int:
    """Install a power: locate its manifest, run Hephaestus.forge_power.

    For v0.11 this is **not** a literal pip-install — the plugin
    packaging story (PyPI / git repo / local plugins/ dir) is itself
    part of v0.11 step 8. Here we accept either:

    - ``--from-installed`` (default behavior with bare ``<name>``):
      plugin is already on PYTHONPATH; we discover it via entry points
      and forge from its manifest.
    - ``--from-yaml <path>``: explicit path to a ``plugin.yaml`` —
      useful for testing and for the in-tree ``plugins/<name>/``
      development workflow before the package machinery lands.

    Either way, the actual install step (the systemd unit write +
    bootstrap hook) is Hephaestus.forge_power.
    """
    from .pantheon.hephaestus import forge_power
    from shared.plugins.manifest import load_manifest_from_yaml

    name = args.name

    # Resolve manifest.
    if args.from_yaml:
        manifest = load_manifest_from_yaml(Path(args.from_yaml))
        if manifest.name != name:
            print(
                f"manifest declares name={manifest.name!r}, but you asked to install "
                f"{name!r} — pass the right name, or use the entry-point install path",
                file=sys.stderr,
            )
            return 2
    else:
        result = discover("power")
        plugin: Optional[InstalledPlugin] = next(
            (p for p in result.plugins if p.name == name), None
        )
        if plugin is None:
            broken = next((e for e in result.errors if e.name == name), None)
            if broken is not None:
                print(
                    f"power {name!r} is registered but broken: {broken.error}\n"
                    f"resolve the import error and retry, or pass --from-yaml "
                    f"with the manifest path directly",
                    file=sys.stderr,
                )
                return 2
            print(
                f"no power named {name!r} discoverable via entry points.\n"
                f"if you have a local plugin.yaml, pass --from-yaml <path>.",
                file=sys.stderr,
            )
            return 1
        manifest = plugin.manifest

    # Resolve repo root for systemd unit's WorkingDirectory.
    repo_root = Path(args.repo_root) if args.repo_root else Path.cwd()

    forge_result = asyncio.run(
        forge_power(
            manifest,
            repo_root=repo_root,
            target_dir=Path(args.target_dir) if args.target_dir else None,
            skip_bootstrap_hook=args.skip_bootstrap_hook,
        )
    )
    print(forge_result.format_report())
    return 0


def cmd_powers_uninstall(args: argparse.Namespace) -> int:
    """Uninstall a power: Hades.archive_power."""
    from .pantheon.hades import archive_power

    result = archive_power(
        args.name,
        target_dir=Path(args.target_dir) if args.target_dir else None,
    )
    print(result.format_report())
    return 0 if result.ok else 1


# ----------------------------- channels alias (deprecated) ---------------


def cmd_channels_list_deprecated(args: argparse.Namespace) -> int:
    """`demiurge channels list` — deprecated alias of `demiurge powers list`.

    v0.11 keeps the alias for muscle-memory continuity. Prints a
    deprecation note to stderr (so it doesn't pollute stdout pipelines)
    and delegates.
    """
    print(
        "DEPRECATION: `demiurge channels list` is deprecated; use "
        "`demiurge powers list` instead. v0.12 may remove this alias.",
        file=sys.stderr,
    )
    return cmd_powers_list(args)


# ----------------------------- argparse wiring ---------------------------


def add_powers_parser(top: argparse._SubParsersAction) -> None:
    """Attach the `demiurge powers` subcommand tree to the top-level parser.

    Called from ``demiurge.cli.build_parser``. Kept in this module so the
    import surface stays organized.
    """
    powers = top.add_parser(
        "powers",
        help="manage external integrations (powers — was 'channels')",
        description=(
            "Powers are external-world integrations: webhook channels, "
            "listener daemons, polling scrapers, request-based APIs. Use "
            "`list` to see installed, `registry` for the catalog, "
            "`install`/`uninstall` to manage."
        ),
    )
    sub = powers.add_subparsers(dest="subcmd", required=True)

    p_list = sub.add_parser("list", help="installed powers (via entry-point discovery)")
    p_list.set_defaults(fn=cmd_powers_list)

    p_reg = sub.add_parser("registry", help="catalog of known powers (curated)")
    p_reg.set_defaults(fn=cmd_powers_registry)

    p_show = sub.add_parser("show", help="manifest + capability scope of one installed power")
    p_show.add_argument("name")
    p_show.set_defaults(fn=cmd_powers_show)

    p_install = sub.add_parser(
        "install",
        help="install a power (forge runtime artifact + run bootstrap hook)",
    )
    p_install.add_argument("name")
    p_install.add_argument(
        "--from-yaml",
        help="explicit path to plugin.yaml (for local/in-tree plugins not yet on PYTHONPATH)",
    )
    p_install.add_argument(
        "--repo-root",
        help="repo root for systemd unit WorkingDirectory (default: cwd)",
    )
    p_install.add_argument(
        "--target-dir",
        help="systemd unit dir (default: ~/.config/systemd/user/)",
    )
    p_install.add_argument(
        "--skip-bootstrap-hook",
        action="store_true",
        help="don't run the manifest's bootstrap hook (useful for testing)",
    )
    p_install.set_defaults(fn=cmd_powers_install)

    p_uninstall = sub.add_parser(
        "uninstall",
        help="uninstall a power (archive_power: remove systemd unit)",
    )
    p_uninstall.add_argument("name")
    p_uninstall.add_argument(
        "--target-dir",
        help="systemd unit dir (default: ~/.config/systemd/user/)",
    )
    p_uninstall.set_defaults(fn=cmd_powers_uninstall)
