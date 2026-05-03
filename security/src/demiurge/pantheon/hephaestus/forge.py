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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from shared.creatures.feed import ObservationFeed
from shared.plugins.manifest import (
    Manifest,
    Mode,
)

from ...bootstrap.systemd import ServiceUnit, write_units
from ...bootstrap.postgres import env_file_path


class ForgeError(Exception):
    """Hephaestus refused to forge or hit a hard failure mid-forge."""


@dataclass(frozen=True)
class ForgeAction:
    """One systemd unit Hephaestus wrote (or noted as unchanged)."""

    path: Path
    verb: str  # "created" | "updated" | "unchanged" — same shape as bootstrap.systemd


@dataclass(frozen=True)
class ForgeResult:
    """Structured outcome of a forge call. Operator-readable + machine-readable."""

    creature_id: str
    kind: str
    systemd_units: list[ForgeAction] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    bootstrap_executed: bool = False
    notes: list[str] = field(default_factory=list)
    """Operator-readable caveats — deferred-feature warnings, partial
    successes, anything Hephaestus thinks the operator should see."""

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
