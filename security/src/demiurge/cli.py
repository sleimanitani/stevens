"""Demiurge admin CLI — sealed-store operations and agent registration.

Invoked as ``uv run stevens`` (via the ``[project.scripts]`` entry point in
``security/pyproject.toml``) or ``python -m demiurge.cli``.

Subcommands::

    demiurge secrets init     [--root PATH] [--force]
    demiurge secrets add      NAME [--from-file PATH | --from-stdin]
                             [--metadata K=V ...] [--rotate-by-days N]
    demiurge secrets list     [--root PATH] [--all]
    demiurge secrets rotate   ID [--from-file PATH | --from-stdin]
                             [--rotate-by-days N]
    demiurge secrets revoke   ID
    demiurge secrets delete   ID
    demiurge agent register   NAME (--pubkey-b64 B64 | --pubkey-file PATH)
                             [--agents-yaml PATH]

Passphrase source: if ``DEMIURGE_PASSPHRASE`` is set in the environment,
it's used (intended for tests and supervised automation). Otherwise the
CLI prompts via ``getpass``. ``init`` confirms the passphrase.

Default paths come from environment:
- ``DEMIURGE_SECURITY_SECRETS`` (default ``/var/lib/demiurge/secrets``)
- ``DEMIURGE_SECURITY_AGENTS`` (default ``security/policy/agents.yaml``)
"""

from __future__ import annotations

import argparse
import base64
import getpass
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from .onboard import (
    OnboardError,
    ingest_google_oauth_client,
    ingest_whatsapp_app_secret,
    parse_google_client_json,
    run_add_account,
    shred_file,
)
from .provision import (
    ProvisionError,
    default_agents_dir,
    provision_agent,
)
from .sealed_store import (
    SealedStore,
    SealedStoreError,
    UnlockError,
    initialize_store,
)


def _default_root() -> Path:
    return Path(
        os.environ.get("DEMIURGE_SECURITY_SECRETS", "/var/lib/demiurge/secrets")
    )


def _default_agents_yaml() -> Path:
    return Path(
        os.environ.get("DEMIURGE_SECURITY_AGENTS", "security/policy/agents.yaml")
    )


def _get_passphrase(*, confirm: bool = False) -> bytes:
    """Source the passphrase, in priority order: env → OS keyring → prompt.

    The env var honors confirm-mode too (it's an automation/test entry
    point — there's nothing to confirm against). Keyring is only consulted
    in non-confirm mode (use the prompt to set the keyring's value, not
    the keyring itself).
    """
    env = os.environ.get("DEMIURGE_PASSPHRASE")
    if env is not None:
        return env.encode("utf-8")
    if not confirm:
        from . import keyring_passphrase

        cached = keyring_passphrase.get()
        if cached is not None:
            return cached
    p = getpass.getpass("passphrase: ")
    if confirm:
        p2 = getpass.getpass("confirm passphrase: ")
        if p != p2:
            raise SystemExit("passphrases do not match")
    return p.encode("utf-8")


def _read_value(from_file: Optional[str], from_stdin: bool) -> bytes:
    if from_file and from_stdin:
        raise SystemExit("cannot combine --from-file and --from-stdin")
    if from_file:
        return Path(from_file).read_bytes()
    if from_stdin:
        return sys.stdin.buffer.read()
    raise SystemExit("one of --from-file or --from-stdin is required")


def _parse_metadata(items: Optional[List[str]]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for raw in items or []:
        key, sep, value = raw.partition("=")
        if not sep:
            raise SystemExit(f"metadata entry must be key=value, got {raw!r}")
        out[key] = value
    return out


# --- commands ---


def cmd_secrets_init(args: argparse.Namespace) -> int:
    pp = _get_passphrase(confirm=True)
    initialize_store(args.root, pp, force=args.force)
    print(f"sealed store initialized at {args.root}")
    return 0


def cmd_secrets_add(args: argparse.Namespace) -> int:
    pp = _get_passphrase()
    store = SealedStore.unlock(args.root, pp)
    value = _read_value(args.from_file, args.from_stdin)
    metadata = _parse_metadata(args.metadata)
    ref = store.add(
        args.name,
        value,
        metadata=metadata or None,
        rotate_by_days=args.rotate_by_days,
    )
    print(f"added  id={ref.id}  name={ref.name}")
    return 0


def cmd_secrets_list(args: argparse.Namespace) -> int:
    pp = _get_passphrase()
    store = SealedStore.unlock(args.root, pp)
    refs = store.list(include_tombstoned=args.all)
    if not refs:
        print("(no secrets)")
        return 0
    for r in refs:
        state = "tombstoned" if r.is_tombstoned else "live"
        rotate_by = r.rotate_by or "-"
        print(
            f"{r.id}  {r.name:<30}  [{state}]  created={r.created_at}  rotate_by={rotate_by}"
        )
    return 0


def cmd_secrets_rotate(args: argparse.Namespace) -> int:
    pp = _get_passphrase()
    store = SealedStore.unlock(args.root, pp)
    new_value = _read_value(args.from_file, args.from_stdin)
    ref = store.rotate(
        args.id, new_value, rotate_by_days=args.rotate_by_days
    )
    print(f"rotated  new_id={ref.id}  name={ref.name}  rotated_from={ref.rotated_from}")
    return 0


def cmd_secrets_revoke(args: argparse.Namespace) -> int:
    pp = _get_passphrase()
    store = SealedStore.unlock(args.root, pp)
    ref = store.revoke(args.id)
    print(f"revoked  id={ref.id}  name={ref.name}  at={ref.tombstoned_at}")
    return 0


def cmd_secrets_delete(args: argparse.Namespace) -> int:
    pp = _get_passphrase()
    store = SealedStore.unlock(args.root, pp)
    store.delete(args.id)
    print(f"deleted  id={args.id}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Print a one-glance status snapshot."""
    from . import status

    socket_path = os.environ.get(
        "DEMIURGE_SECURITY_SOCKET", "/run/demiurge/security.sock"
    )
    audit_dir = Path(
        os.environ.get("DEMIURGE_SECURITY_AUDIT_DIR", "/var/lib/demiurge/audit")
    )
    print(
        status.render_status(
            secrets_root=args.root,
            socket_path=socket_path,
            agents_yaml=args.agents_yaml,
            audit_dir=audit_dir,
        )
    )
    return 0


def cmd_channels_list(args: argparse.Namespace) -> int:
    """Print the channel registry — what's shipped vs planned + runbook paths."""
    from . import channels_list

    print(channels_list.render())
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    """Wipe local Stevens state for fresh-install testing.

    Default (no flags) is dry-run — prints what would be wiped and exits.
    --yes prompts for one final confirmation then executes.
    --force skips the prompt (for scripts).
    """
    import asyncio

    from .reset import build_plan, execute_plan, post_wipe_next_steps

    plan = build_plan(
        keep_sealed=args.keep_sealed,
        keep_audit=args.keep_audit,
        keep_agents=args.keep_agents,
        keep_janus_profile=args.keep_janus_profile,
        keep_keyring=args.keep_keyring,
        keep_postgres=args.keep_postgres,
        keep_pdf_corpus=args.keep_pdf_corpus,
    )
    print(plan.render())

    if not args.yes and not args.force:
        print("\n(this was a dry run — pass --yes to actually wipe, or --force to skip the confirm)")
        return 0

    if not args.force:
        ans = input("\nType 'wipe' to confirm: ").strip().lower()
        if ans != "wipe":
            print("aborted; nothing changed")
            return 1

    print("\nexecuting...")
    results = asyncio.run(execute_plan(plan))
    for line in results:
        print(line)
    print(post_wipe_next_steps())
    return 0


def cmd_janus_list(args: argparse.Namespace) -> int:
    """List registered Janus recipes with descriptions + prerequisites."""
    from .wizards.janus import get, known

    names = known()
    if not names:
        print("(no recipes registered)")
        return 0
    for n in names:
        r = get(n)
        avail = "OK" if r.available() else "playwright not installed"
        print(f"  {n:<28} [{avail}]")
        print(f"      {r.description}")
        for prereq in r.prerequisites:
            print(f"      - prereq: {prereq}")
    return 0


def cmd_janus_run(args: argparse.Namespace) -> int:
    """Run a Janus recipe end-to-end."""
    import asyncio

    from .wizards.janus import RecipeError, get
    from .wizards.janus.runner import execute_recipe

    try:
        recipe = get(args.recipe)
    except RecipeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if not recipe.available():
        print("error: playwright not installed. run:", file=sys.stderr)
        print("  uv pip install playwright && uv run playwright install chromium",
              file=sys.stderr)
        return 1

    pp = _get_passphrase()
    sealed = SealedStore.unlock(args.root, pp)

    # Sealed-store writer with --rotate semantics.
    async def write_secret(name: str, value: str, metadata: dict) -> None:
        if not args.rotate:
            try:
                sealed.get_by_name(name)
            except Exception:
                pass
            else:
                raise RuntimeError(
                    f"secret {name!r} already exists; pass --rotate to overwrite"
                )
        if args.rotate:
            try:
                old = sealed.ref_by_name(name)
                sealed.rotate(old.id, value.encode("utf-8"))
                return
            except Exception:
                pass
        sealed.add(name, value.encode("utf-8"), metadata=metadata)

    # Operator prompt callback.
    async def ask(message: str) -> bool:
        print(f"\n>>> {message}")
        ans = input("    Press Enter to continue, or type 'abort': ").strip().lower()
        return ans != "abort"

    recipe_kwargs = {}
    if args.project_id:
        recipe_kwargs["project_id"] = args.project_id

    from .wizards.janus.playwright_session import open_chromium

    async def _run() -> int:
        async with open_chromium(headless=args.headless) as session:
            result = await execute_recipe(
                recipe,
                session=session,
                ask_operator=ask,
                write_secret=write_secret,
                recipe_kwargs=recipe_kwargs,
            )
        if result.stored_secrets:
            print(f"\nrecipe {recipe.name!r} done. Stored secrets:")
            for s in result.stored_secrets:
                print(f"  - {s}")
        else:
            print(f"\nrecipe {recipe.name!r} done. (No secrets stored — recipe "
                  "completes the operator-side workflow without writing anything.)")
        return 0

    try:
        return asyncio.run(_run())
    except RecipeError as e:
        print(f"recipe failed: {e}", file=sys.stderr)
        return 1
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


def cmd_wizard_google(args: argparse.Namespace) -> int:
    """Run the Google Cloud onboarding wizard, then chain into demiurge onboard gmail."""
    from .wizards.google import WizardError, WizardInputs, run_wizard

    inputs = WizardInputs(
        project_id=args.project_id,
        project_name=args.project_name,
        push_endpoint=args.push_endpoint,
        downloads_dir=args.downloads_dir,
    )
    try:
        result = run_wizard(inputs)
    except WizardError as e:
        print(f"wizard error: {e}", file=sys.stderr)
        return 1
    print()
    print("Wizard complete. To finish onboarding a Gmail account, run:")
    print(
        f"  uv run demiurge onboard gmail --client-json {result.client_secret_path} "
        f"-- --id gmail.personal --name 'Sol personal'"
    )
    print()
    print("(Then `demiurge onboard calendar` for Calendar onboarding using the same client.)")
    return 0


def cmd_bootstrap(args: argparse.Namespace) -> int:
    """First-time setup — preflight + Postgres + migrations + systemd units."""
    from .bootstrap.cli_bootstrap import run_bootstrap

    return run_bootstrap(dry_run=args.dry_run, repo_root=args.repo_root)


def cmd_doctor(args: argparse.Namespace) -> int:
    """Run diagnostic checks. Returns non-zero if any non-info check fails."""
    from . import doctor

    socket_path = os.environ.get(
        "DEMIURGE_SECURITY_SOCKET", "/run/demiurge/security.sock"
    )
    capabilities_yaml = Path(
        os.environ.get(
            "DEMIURGE_SECURITY_POLICY", "security/policy/capabilities.yaml"
        )
    )
    report = doctor.run_doctor(
        secrets_root=args.root,
        socket_path=socket_path,
        agents_yaml=args.agents_yaml,
        capabilities_yaml=capabilities_yaml,
        agents_dir=args.agents_dir or default_agents_dir(),
    )
    print(doctor.format_report(report))
    return 0 if report.passed else 1


def cmd_audit_tail(args: argparse.Namespace) -> int:
    """Print the last N lines of today's audit log (or follow with -f)."""
    from . import audit_tail

    audit_dir = args.audit_dir or Path(
        os.environ.get("DEMIURGE_SECURITY_AUDIT_DIR", "/var/lib/demiurge/audit")
    )
    if args.follow:
        # Print last N first, then start following.
        audit_tail.print_tail(audit_dir, n=args.lines, out=sys.stdout, raw_mode=args.raw)
        try:
            audit_tail.follow(audit_dir, out=sys.stdout, raw_mode=args.raw)
        except KeyboardInterrupt:
            return 0
    else:
        audit_tail.print_tail(audit_dir, n=args.lines, out=sys.stdout, raw_mode=args.raw)
    return 0


def cmd_passphrase_remember(args: argparse.Namespace) -> int:
    """Verify the passphrase unlocks the store, then stash it in the keyring."""
    from . import keyring_passphrase

    pp = getpass.getpass("passphrase: ")
    pp_b = pp.encode("utf-8")
    # Verify it actually unlocks the store before remembering.
    SealedStore.unlock(args.root, pp_b)
    try:
        keyring_passphrase.set(pp_b)
    except keyring_passphrase.KeyringUnavailable as e:
        raise SystemExit(f"keyring not available: {e}")
    print("passphrase stashed in OS keyring — future operations unlock silently")
    return 0


def cmd_passphrase_forget(args: argparse.Namespace) -> int:
    """Remove the stored passphrase from the keyring."""
    from . import keyring_passphrase

    keyring_passphrase.clear()
    print("passphrase removed from OS keyring")
    return 0


def cmd_onboard(args: argparse.Namespace) -> int:
    """Run channel onboarding (ingest OAuth client if needed, then add_account)."""
    channel = args.channel
    pp = _get_passphrase()
    store = SealedStore.unlock(args.root, pp)

    if channel in ("gmail", "calendar"):
        if args.client_json:
            client_path = Path(args.client_json)
            if not client_path.exists():
                raise SystemExit(f"client JSON not found: {client_path}")
            payload = client_path.read_bytes()
            client = parse_google_client_json(payload)
            outcome = ingest_google_oauth_client(
                store,
                namespace=channel,
                client=client,
                rotate=args.rotate_client,
            )
            print(f"OAuth client: {outcome}")
            if outcome in ("ingested", "rotated"):
                # Source file held secrets — best-effort secure delete.
                shred_file(client_path)
                print(f"shredded {client_path}")

    elif channel == "whatsapp_cloud":
        if args.app_secret_stdin:
            secret = sys.stdin.buffer.read().strip()
            if not secret:
                raise SystemExit("--app-secret-stdin produced empty input")
            outcome = ingest_whatsapp_app_secret(
                store, app_secret=secret, rotate=args.rotate_client
            )
            print(f"WhatsApp Cloud app secret: {outcome}")

    elif channel == "signal":
        # Signal has no per-channel OAuth client to ingest — the
        # signal-cli-rest-api daemon pairs once per phone via QR code.
        # Anything operator-supplied lives in add_account_args (--phone, etc.).
        pass

    else:
        raise SystemExit(f"unknown channel: {channel!r}")

    # Pass through to add_account if the operator gave per-account flags.
    add_account_args = list(args.add_account_args or [])
    if not add_account_args:
        print("(no per-account flags given; skipping add_account)")
        return 0

    print(f"running {channel}_adapter.add_account ...")
    rc = run_add_account(channel, add_account_args)
    return rc


def cmd_agent_provision(args: argparse.Namespace) -> int:
    """Provision a new agent: keypair + register + apply preset + write .env."""
    capabilities_yaml = (
        args.capabilities_yaml
        or Path(
            os.environ.get(
                "DEMIURGE_SECURITY_POLICY", "security/policy/capabilities.yaml"
            )
        )
    )
    socket_path = os.environ.get(
        "DEMIURGE_SECURITY_SOCKET", "/run/demiurge/security.sock"
    )
    result = provision_agent(
        name=args.name,
        preset_name=args.preset,
        agents_yaml=args.agents_yaml,
        capabilities_yaml=capabilities_yaml,
        agents_dir=args.agents_dir,
        socket_path=socket_path,
        force=args.force,
    )
    print(f"provisioned agent {result.name!r}")
    print(f"  key file:        {result.key_path}  (chmod 0600)")
    print(f"  env profile:     {result.env_path}")
    print(f"  pubkey:          {result.pubkey_b64}")
    if result.preset_applied:
        verb = "applied" if result.preset_changed else "already up to date"
        print(f"  preset:          {result.preset_applied} ({verb})")
    print()
    print(f"ready — run with: demiurge agent run {result.name}")
    return 0


def cmd_agent_run(args: argparse.Namespace) -> int:
    """Start the agents runtime with the named agent's env profile loaded."""
    agents_dir = args.agents_dir or default_agents_dir()
    env_path = agents_dir / f"{args.name}.env"
    if not env_path.exists():
        raise SystemExit(
            f"no agent profile at {env_path} — run "
            f"`demiurge agent provision {args.name}` first"
        )

    env = dict(os.environ)
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        if not _:
            continue
        env[key] = value

    key_path = Path(env.get("DEMIURGE_PRIVATE_KEY_PATH", ""))
    if not key_path.exists():
        raise SystemExit(
            f"private key file not found: {key_path} — re-provision the agent"
        )
    if (key_path.stat().st_mode & 0o077) != 0:
        raise SystemExit(
            f"private key file {key_path} is group/world-readable; chmod 0600 first"
        )

    # Forward to the agents runtime. Use the same Python interpreter.
    argv = [sys.executable, "-m", "agents.runtime"]
    print(f"starting agent {args.name!r}: {' '.join(argv)}")
    os.execvpe(argv[0], argv, env)


def cmd_agent_register(args: argparse.Namespace) -> int:
    if args.pubkey_b64 and args.pubkey_file:
        raise SystemExit("cannot combine --pubkey-b64 and --pubkey-file")
    if args.pubkey_b64:
        pubkey_b64 = args.pubkey_b64
    elif args.pubkey_file:
        pubkey_b64 = Path(args.pubkey_file).read_text().strip()
    else:
        raise SystemExit("one of --pubkey-b64 or --pubkey-file is required")
    try:
        raw = base64.b64decode(pubkey_b64, validate=True)
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"pubkey_b64 is not valid base64: {e}")
    if len(raw) != 32:
        raise SystemExit(f"expected 32-byte Ed25519 pubkey, got {len(raw)} bytes")

    agents_path: Path = args.agents_yaml
    data: dict = {}
    if agents_path.exists():
        loaded = yaml.safe_load(agents_path.read_text()) or {}
        if isinstance(loaded, dict):
            data = loaded
    agents = data.get("agents") or []
    if any(e.get("name") == args.name for e in agents):
        raise SystemExit(f"agent {args.name!r} already registered in {agents_path}")
    agents.append({"name": args.name, "pubkey_b64": pubkey_b64})
    data["agents"] = agents
    agents_path.parent.mkdir(parents=True, exist_ok=True)
    agents_path.write_text(yaml.safe_dump(data, sort_keys=False))
    print(f"registered agent {args.name!r} in {agents_path}")
    return 0


# --- parser ---


def _add_root_flag(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--root",
        type=Path,
        default=None,
        help="sealed-store root (default: $DEMIURGE_SECURITY_SECRETS or /var/lib/demiurge/secrets)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="demiurge",
        description="Demiurge admin CLI — talks to Enkidu (the Security Agent).",
    )
    top = parser.add_subparsers(dest="cmd", required=True)

    # secrets
    secrets = top.add_parser("secrets", help="manage sealed-store secrets")
    ss = secrets.add_subparsers(dest="subcmd", required=True)

    sp = ss.add_parser("init", help="create a new sealed store")
    _add_root_flag(sp)
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(fn=cmd_secrets_init)

    sp = ss.add_parser("add", help="add a new secret")
    sp.add_argument("name")
    _add_root_flag(sp)
    sp.add_argument("--from-file", help="read value from this file (bytes)")
    sp.add_argument("--from-stdin", action="store_true", help="read value from stdin")
    sp.add_argument(
        "--metadata", nargs="*", help="key=value metadata (repeatable)",
    )
    sp.add_argument("--rotate-by-days", type=int)
    sp.set_defaults(fn=cmd_secrets_add)

    sp = ss.add_parser("list", help="list secrets")
    _add_root_flag(sp)
    sp.add_argument("--all", action="store_true", help="include tombstoned")
    sp.set_defaults(fn=cmd_secrets_list)

    sp = ss.add_parser("rotate", help="rotate a secret (creates new id, tombstones old)")
    sp.add_argument("id")
    _add_root_flag(sp)
    sp.add_argument("--from-file")
    sp.add_argument("--from-stdin", action="store_true")
    sp.add_argument("--rotate-by-days", type=int)
    sp.set_defaults(fn=cmd_secrets_rotate)

    sp = ss.add_parser("revoke", help="tombstone a secret (reversible via rotate)")
    sp.add_argument("id")
    _add_root_flag(sp)
    sp.set_defaults(fn=cmd_secrets_revoke)

    sp = ss.add_parser("delete", help="hard-delete a secret from the vault")
    sp.add_argument("id")
    _add_root_flag(sp)
    sp.set_defaults(fn=cmd_secrets_delete)

    # status
    sts = top.add_parser("status", help="one-glance status snapshot")
    _add_root_flag(sts)
    sts.add_argument("--agents-yaml", type=Path, default=None)
    sts.set_defaults(fn=cmd_status)

    # bootstrap
    boot = top.add_parser(
        "bootstrap",
        help="first-time setup — Postgres + migrations + systemd units",
        description=(
            "Idempotent first-time setup orchestrator. Wires up native Postgres, "
            "applies migrations, writes ~/.config/demiurge/env, generates systemd "
            "user units. Never runs sudo itself — prints the one block of "
            "elevated commands the operator needs and stops, then resumes on "
            "re-run."
        ),
    )
    boot.add_argument(
        "--dry-run",
        action="store_true",
        help="describe what would happen without making any changes",
    )
    boot.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="repo root for systemd unit ExecStart paths (default: inferred)",
    )
    boot.set_defaults(fn=cmd_bootstrap)

    # doctor
    doc = top.add_parser("doctor", help="run diagnostic checks on the install")
    _add_root_flag(doc)
    doc.add_argument("--agents-yaml", type=Path, default=None)
    doc.add_argument("--agents-dir", type=Path, default=None)
    doc.set_defaults(fn=cmd_doctor)

    # audit
    aud = top.add_parser("audit", help="audit log inspection")
    aud_sub = aud.add_subparsers(dest="subcmd", required=True)

    aud_tail = aud_sub.add_parser("tail", help="show recent audit log lines")
    aud_tail.add_argument(
        "--audit-dir",
        type=Path,
        default=None,
        help="audit dir (default: $DEMIURGE_SECURITY_AUDIT_DIR or /var/lib/demiurge/audit)",
    )
    aud_tail.add_argument(
        "-n", "--lines", type=int, default=20, help="number of trailing lines (default 20)"
    )
    aud_tail.add_argument(
        "-f", "--follow", action="store_true", help="poll for new lines forever"
    )
    aud_tail.add_argument(
        "--raw", action="store_true", help="emit raw JSONL (for piping into jq)"
    )
    aud_tail.set_defaults(fn=cmd_audit_tail)

    # passphrase
    pph = top.add_parser("passphrase", help="manage the sealed-store passphrase in the OS keyring")
    pp_sub = pph.add_subparsers(dest="subcmd", required=True)

    pp_remember = pp_sub.add_parser(
        "remember",
        help="stash the passphrase in the OS keyring so future calls don't prompt",
    )
    _add_root_flag(pp_remember)
    pp_remember.set_defaults(fn=cmd_passphrase_remember)

    pp_forget = pp_sub.add_parser("forget", help="clear the keyring entry")
    pp_forget.set_defaults(fn=cmd_passphrase_forget)

    # onboard
    onb = top.add_parser(
        "onboard",
        help="onboard a channel (ingest OAuth client + run per-account flow)",
    )
    onb.add_argument(
        "channel",
        choices=["gmail", "calendar", "whatsapp_cloud", "signal"],
        help="which channel to onboard",
    )
    _add_root_flag(onb)
    onb.add_argument(
        "--client-json",
        help="(gmail/calendar) Google Cloud Console OAuth-client JSON",
    )
    onb.add_argument(
        "--app-secret-stdin",
        action="store_true",
        help="(whatsapp_cloud) read Meta app secret from stdin",
    )
    onb.add_argument(
        "--rotate-client",
        action="store_true",
        help=(
            "rotate the OAuth client / app secret if already present "
            "(this invalidates existing accounts — opt in explicitly)"
        ),
    )
    onb.add_argument(
        "add_account_args",
        nargs=argparse.REMAINDER,
        help=(
            "all flags after `--` are forwarded to "
            "<channel>_adapter.add_account (e.g. --id gmail.personal --name 'Sol')"
        ),
    )
    onb.set_defaults(fn=cmd_onboard)

    # agent
    agent = top.add_parser("agent", help="manage agent identity registry")
    ag = agent.add_subparsers(dest="subcmd", required=True)

    ap = ag.add_parser("register", help="register an agent's public key")
    ap.add_argument("name")
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--pubkey-b64")
    group.add_argument("--pubkey-file")
    ap.add_argument("--agents-yaml", type=Path, default=None)
    ap.set_defaults(fn=cmd_agent_register)

    ap = ag.add_parser(
        "provision",
        help="generate keypair + register + apply preset + write .env (one command)",
    )
    ap.add_argument("name")
    ap.add_argument(
        "--preset",
        help="policy preset to apply (e.g. email_pm, subject_agent, interface)",
    )
    ap.add_argument("--agents-yaml", type=Path, default=None)
    ap.add_argument(
        "--capabilities-yaml",
        type=Path,
        default=None,
        help="path to capabilities.yaml (default: $DEMIURGE_SECURITY_POLICY)",
    )
    ap.add_argument(
        "--agents-dir",
        type=Path,
        default=None,
        help="where to write key + env files (default: ~/.config/demiurge/agents)",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="rotate an existing agent (regenerates key, old key file becomes useless)",
    )
    ap.set_defaults(fn=cmd_agent_provision)

    ap = ag.add_parser("run", help="start the agents runtime with this agent's profile")
    ap.add_argument("name")
    ap.add_argument(
        "--agents-dir",
        type=Path,
        default=None,
        help="where to look for the .env / .key files (default: ~/.config/demiurge/agents)",
    )
    ap.set_defaults(fn=cmd_agent_run)

    # approval / dep — registered in cli_approvals.
    from . import cli_approvals

    cli_approvals.add_approval_parser(top)
    cli_approvals.add_dep_parser(top)

    # wizard
    wiz = top.add_parser("wizard", help="multi-step setup wizards (Google, …)")
    wiz_sub = wiz.add_subparsers(dest="subcmd", required=True)

    wg = wiz_sub.add_parser("google", help="Google Cloud onboarding wizard")
    wg.add_argument("--project-id", required=True,
                    help="GCP project id to create or reuse (e.g. stevens-personal)")
    wg.add_argument("--project-name", default=None)
    wg.add_argument("--push-endpoint", default=None,
                    help="public push-receiver URL for Gmail webhook")
    wg.add_argument("--downloads-dir", type=Path, default=None,
                    help="where to watch for the downloaded client_secret*.json")
    wg.set_defaults(fn=cmd_wizard_google)

    # reset — wipe local Stevens state for fresh-install testing
    rst = top.add_parser(
        "reset",
        help="wipe local Stevens state (sealed store, audit, keyring, PG tables, "
             "Janus profile) for fresh-install testing. Defaults to dry-run.",
    )
    rst.add_argument("--yes", action="store_true",
                     help="actually execute the wipe (asks for one final confirm)")
    rst.add_argument("--force", action="store_true",
                     help="skip the final confirm (for scripts)")
    rst.add_argument("--keep-sealed", action="store_true")
    rst.add_argument("--keep-audit", action="store_true")
    rst.add_argument("--keep-agents", action="store_true")
    rst.add_argument("--keep-janus-profile", action="store_true")
    rst.add_argument("--keep-keyring", action="store_true")
    rst.add_argument("--keep-postgres", action="store_true")
    rst.add_argument("--keep-pdf-corpus", action="store_true")
    rst.set_defaults(fn=cmd_reset)

    # powers — v0.11 plugin-aware integration management
    from .cli_powers import add_powers_parser, cmd_channels_list_deprecated

    add_powers_parser(top)

    # channels — deprecated alias for `powers`. Kept through v0.11 for
    # muscle-memory continuity; v0.12 may drop it.
    chs = top.add_parser(
        "channels", help="discover channels (deprecated alias of `powers`)",
    )
    chs_sub = chs.add_subparsers(dest="subcmd", required=True)
    chs_list = chs_sub.add_parser("list", help="(deprecated) print the channel registry")
    chs_list.set_defaults(fn=cmd_channels_list_deprecated)

    # janus — operator-assisted browser automation
    cha = top.add_parser(
        "janus",
        help="operator-assisted browser automation for OAuth/config dances",
    )
    cha_sub = cha.add_subparsers(dest="subcmd", required=True)

    cha_list = cha_sub.add_parser("list", help="list available recipes")
    cha_list.set_defaults(fn=cmd_janus_list)

    cha_run = cha_sub.add_parser("run", help="run a recipe")
    cha_run.add_argument("recipe", help="recipe name (see `demiurge janus list`)")
    cha_run.add_argument(
        "--rotate", action="store_true",
        help="overwrite an existing sealed-store secret if the recipe writes one",
    )
    cha_run.add_argument(
        "--project-id", default=None,
        help="passed to recipes that need it (e.g. google_oauth_client)",
    )
    cha_run.add_argument(
        "--headless", action="store_true",
        help="hide the browser (advanced; default is headed so you can sign in)",
    )
    _add_root_flag(cha_run)
    cha_run.set_defaults(fn=cmd_janus_run)

    return parser


def _get_approval_store():
    """Pick the Postgres-backed store if DATABASE_URL is set, else in-memory."""
    if os.environ.get("DATABASE_URL"):
        from .approvals.store_postgres import PostgresApprovalStore

        return PostgresApprovalStore()
    from .approvals.store import InMemoryApprovalStore

    return InMemoryApprovalStore()


def _get_inventory():
    if os.environ.get("DATABASE_URL"):
        from .system_runtime_postgres import PostgresInventory

        return PostgresInventory()
    from .system_runtime import InMemoryInventory

    return InMemoryInventory()


async def _run_approval_handler(args) -> int:
    """Dispatch approval / dep subcommands, async because the handlers are."""
    handler = args._handler
    cmd = args.cmd
    if cmd == "approval":
        store = _get_approval_store()
        # Some approval commands (approve / reject) need to also notify
        # Enkidu via the admin capability so the in-memory matcher refreshes
        # and the replay path is unblocked. v0.3.2 leaves that wiring as a
        # follow-up — for now the operator restarts Enkidu after standing
        # changes, or calls the admin capability directly.
        return int(await handler(args, store) or 0)
    if cmd == "dep":
        if args.subcmd == "list":
            inventory = _get_inventory()
            return int(await handler(args, inventory) or 0)
        if args.subcmd == "ensure":
            # Publish a SystemDepRequestedEvent on the bus.
            from shared.events import SystemDepRequestedEvent
            from shared import bus

            async def request(pkg):
                await bus.publish(
                    SystemDepRequestedEvent(account_id="system", package=pkg)
                )

            return int(await handler(args, request=request) or 0)
    raise SystemExit(f"unhandled approval/dep subcommand: {cmd}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Fill in environment-based defaults for paths that the subparser
    # flag defaults didn't resolve (we deliberately leave them None so
    # the test suite can override the env mid-run).
    if getattr(args, "root", None) is None:
        args.root = _default_root()
    if hasattr(args, "agents_yaml") and args.agents_yaml is None:
        args.agents_yaml = _default_agents_yaml()

    try:
        # approval / dep handlers are async and take a store/inventory, so
        # they go through a separate dispatch path.
        if getattr(args, "cmd", None) in ("approval", "dep"):
            import asyncio

            return asyncio.run(_run_approval_handler(args))
        return int(args.fn(args) or 0)
    except (SealedStoreError, UnlockError, OnboardError, ProvisionError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
