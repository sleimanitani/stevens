"""Channel onboarding — `stevens onboard <channel>`.

Wraps the per-channel ``add_account`` CLIs so the operator runs one
command per account instead of three (ingest OAuth client → shred file →
add_account). Subsequent calls for the same channel skip the
client-ingestion step.

Channels:
- ``gmail``: ingests ``client_id`` + ``client_secret`` from a Google
  Cloud Console JSON, then runs ``gmail_adapter.add_account``.
- ``calendar``: same as gmail (uses ``calendar_adapter.add_account``).
- ``whatsapp_cloud``: ingests the Meta app secret if not already present,
  then runs ``whatsapp_cloud_adapter.add_account``.

The client-ingestion step is idempotent: if the client values are already
in the sealed store, this module does nothing unless the operator passes
``--rotate-client`` — rotating the OAuth client invalidates every account
that uses it, so we make the operator opt in explicitly.
"""

from __future__ import annotations

import json
import os
import secrets as stdlib_secrets
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


class OnboardError(Exception):
    """Raised on configuration errors during onboarding."""


@dataclass(frozen=True)
class OAuthClient:
    client_id: str
    client_secret: str


# --- shred ---


def shred_file(path: Path, passes: int = 3) -> None:
    """Overwrite ``path`` with random bytes ``passes`` times then unlink.

    Best-effort secure delete. On filesystems that do copy-on-write or
    journalling (btrfs, ZFS), this won't actually destroy the original
    blocks — but for ext4 / xfs without snapshots, it's the standard
    `shred` semantics. The point is to make accidental recovery hard,
    not to defeat a forensic adversary.
    """
    if not path.exists():
        return
    size = path.stat().st_size
    with path.open("r+b") as f:
        for _ in range(passes):
            f.seek(0)
            # Write in 64KiB chunks so we don't load huge files in memory.
            remaining = size
            chunk = 64 * 1024
            while remaining > 0:
                n = min(chunk, remaining)
                f.write(stdlib_secrets.token_bytes(n))
                remaining -= n
            f.flush()
            os.fsync(f.fileno())
    path.unlink()


# --- OAuth client parsers ---


def parse_google_client_json(payload: bytes) -> OAuthClient:
    """Parse a Google Cloud Console OAuth-client JSON.

    The file Google produces has either an ``installed`` key (Desktop
    app) or a ``web`` key (Web app). Stevens uses Desktop. We tolerate
    both shapes for robustness — they have the same two load-bearing
    fields.
    """
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as e:
        raise OnboardError(f"client JSON is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise OnboardError("client JSON top-level must be an object")
    inner = data.get("installed") or data.get("web")
    if not isinstance(inner, dict):
        raise OnboardError(
            "client JSON missing 'installed' or 'web' key — was it really "
            "downloaded from Google Cloud Console?"
        )
    cid = inner.get("client_id")
    csec = inner.get("client_secret")
    if not isinstance(cid, str) or not cid:
        raise OnboardError("client JSON missing client_id")
    if not isinstance(csec, str) or not csec:
        raise OnboardError("client JSON missing client_secret")
    return OAuthClient(client_id=cid, client_secret=csec)


# --- ingestion (testable) ---


def ingest_google_oauth_client(
    store,
    *,
    namespace: str,  # "gmail" or "calendar"
    client: OAuthClient,
    rotate: bool,
) -> str:
    """Store a Google OAuth client in the sealed store.

    Returns one of ``"ingested"`` / ``"already_present"`` / ``"rotated"``.
    Raises ``OnboardError`` if the client is already present and ``rotate``
    is False — clobbering an OAuth client invalidates every account using it.
    """
    id_name = f"{namespace}.oauth_client.id"
    secret_name = f"{namespace}.oauth_client.secret"

    has_id = _has_secret(store, id_name)
    has_secret = _has_secret(store, secret_name)

    if has_id and has_secret and not rotate:
        return "already_present"

    if has_id and has_secret and rotate:
        # rotate() tombstones the old record and adds a new one carrying
        # the same name — preserves audit trail.
        old_id_ref = store.ref_by_name(id_name)
        old_secret_ref = store.ref_by_name(secret_name)
        store.rotate(old_id_ref.id, client.client_id.encode("utf-8"))
        store.rotate(old_secret_ref.id, client.client_secret.encode("utf-8"))
        return "rotated"

    if has_id != has_secret:
        # Half-installed — refuse to proceed.
        raise OnboardError(
            f"sealed store has only one of {id_name!r}/{secret_name!r}; "
            f"resolve manually with `stevens secrets list` before continuing"
        )

    store.add(
        id_name,
        client.client_id.encode("utf-8"),
        metadata={"kind": "oauth_client", "namespace": namespace},
    )
    store.add(
        secret_name,
        client.client_secret.encode("utf-8"),
        metadata={"kind": "oauth_client", "namespace": namespace},
    )
    return "ingested"


def ingest_whatsapp_app_secret(
    store, *, app_secret: bytes, rotate: bool
) -> str:
    """Store the Meta WhatsApp Cloud app secret in the sealed store."""
    name = "whatsapp_cloud.app_secret"
    has = _has_secret(store, name)
    if has and not rotate:
        return "already_present"
    if has and rotate:
        old = store.ref_by_name(name)
        store.rotate(old.id, app_secret)
        return "rotated"
    store.add(name, app_secret, metadata={"kind": "wac_app_secret"})
    return "ingested"


def _has_secret(store, name: str) -> bool:
    """True if ``name`` resolves to a live (non-tombstoned) secret."""
    try:
        store.get_by_name(name)
        return True
    except Exception:
        return False


# --- per-channel add_account dispatch ---


def add_account_argv(channel: str, args: List[str]) -> List[str]:
    """Return the argv that runs the per-channel add_account CLI."""
    module = {
        "gmail": "gmail_adapter.add_account",
        "calendar": "calendar_adapter.add_account",
        "whatsapp_cloud": "whatsapp_cloud_adapter.add_account",
    }.get(channel)
    if module is None:
        raise OnboardError(
            f"unknown channel {channel!r}; expected one of "
            f"gmail / calendar / whatsapp_cloud"
        )
    # Use the same Python interpreter we're running under so we don't
    # accidentally spawn into a different venv.
    return [sys.executable, "-m", module, *args]


def run_add_account(
    channel: str,
    args: List[str],
    *,
    env: Optional[dict] = None,
    stdin: Optional[bytes] = None,
) -> int:
    """Spawn the per-channel add_account CLI and forward stdin if provided.

    Returns the subprocess return code. The CLI's stdout/stderr stream
    directly to the operator's terminal (it's an interactive flow — we
    don't capture).
    """
    argv = add_account_argv(channel, args)
    full_env = {**os.environ, **(env or {})}
    return subprocess.run(
        argv,
        input=stdin,
        env=full_env,
    ).returncode
