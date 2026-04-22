"""Stevens admin CLI — sealed-store operations and agent registration.

Invoked as ``uv run stevens`` (via the ``[project.scripts]`` entry point in
``security/pyproject.toml``) or ``python -m stevens_security.cli``.

Subcommands::

    stevens secrets init     [--root PATH] [--force]
    stevens secrets add      NAME [--from-file PATH | --from-stdin]
                             [--metadata K=V ...] [--rotate-by-days N]
    stevens secrets list     [--root PATH] [--all]
    stevens secrets rotate   ID [--from-file PATH | --from-stdin]
                             [--rotate-by-days N]
    stevens secrets revoke   ID
    stevens secrets delete   ID
    stevens agent register   NAME (--pubkey-b64 B64 | --pubkey-file PATH)
                             [--agents-yaml PATH]

Passphrase source: if ``STEVENS_PASSPHRASE`` is set in the environment,
it's used (intended for tests and supervised automation). Otherwise the
CLI prompts via ``getpass``. ``init`` confirms the passphrase.

Default paths come from environment:
- ``STEVENS_SECURITY_SECRETS`` (default ``/var/lib/stevens/secrets``)
- ``STEVENS_SECURITY_AGENTS`` (default ``security/policy/agents.yaml``)
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

from .sealed_store import (
    SealedStore,
    SealedStoreError,
    UnlockError,
    initialize_store,
)


def _default_root() -> Path:
    return Path(
        os.environ.get("STEVENS_SECURITY_SECRETS", "/var/lib/stevens/secrets")
    )


def _default_agents_yaml() -> Path:
    return Path(
        os.environ.get("STEVENS_SECURITY_AGENTS", "security/policy/agents.yaml")
    )


def _get_passphrase(*, confirm: bool = False) -> bytes:
    env = os.environ.get("STEVENS_PASSPHRASE")
    if env is not None:
        return env.encode("utf-8")
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
        help="sealed-store root (default: $STEVENS_SECURITY_SECRETS or /var/lib/stevens/secrets)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stevens",
        description="Stevens Security Agent admin CLI.",
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

    return parser


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
        return int(args.fn(args) or 0)
    except (SealedStoreError, UnlockError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
