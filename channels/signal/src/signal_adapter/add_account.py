"""Signal add-account CLI.

Operator runs::

    uv run python -m signal_adapter.add_account \\
        --id signal.personal --name "Sol personal" \\
        --phone +15555551234 \\
        --daemon-url http://localhost:8080

What happens:

1. Stores ``signal.personal.daemon_url`` and ``signal.personal.phone`` in
   the sealed store (so Enkidu and the adapter can resolve them).
2. Calls ``GET /v1/qrcodelink/Stevens?number=<phone>`` to get a QR PNG.
3. Saves the PNG to a temp path and prints the path. Operator opens it,
   scans with the Signal app on their phone (Settings → Linked devices →
   Link new device).
4. Polls until ``GET /v1/about`` and a ``GET /v1/receive/{phone}`` succeed
   (i.e. the daemon now considers the phone linked).
5. Inserts a ``channel_accounts`` row with ``channel_type='signal'``,
   ``credentials_ref='signal.personal.phone'``.
"""

from __future__ import annotations

import asyncio
import getpass
import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Optional

import click

from .client import SignalCliClient, SignalCliError


log = logging.getLogger(__name__)


def _load_passphrase() -> bytes:
    env = os.environ.get("DEMIURGE_PASSPHRASE")
    if env is not None:
        return env.encode("utf-8")
    return getpass.getpass("sealed-store passphrase: ").encode("utf-8")


def _open_store():
    from demiurge.sealed_store import SealedStore

    root = Path(
        os.environ.get("DEMIURGE_SECURITY_SECRETS", "/var/lib/demiurge/secrets")
    )
    return SealedStore.unlock(root, _load_passphrase())


@click.command()
@click.option("--id", "account_id", required=True, help="Stable slug, e.g. signal.personal")
@click.option("--name", "display_name", required=True)
@click.option("--phone", required=True, help="Phone number with country code, e.g. +15551234567")
@click.option("--daemon-url", required=True,
              help="signal-cli-rest-api base URL, e.g. http://localhost:8080")
@click.option("--device-name", default="Stevens", show_default=True)
@click.option("--link-timeout-s", default=300, show_default=True,
              help="how long to wait for the link before giving up")
def main(
    account_id: str, display_name: str, phone: str,
    daemon_url: str, device_name: str, link_timeout_s: int,
) -> None:
    """Onboard a new Signal account."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    if not account_id.startswith("signal."):
        raise click.UsageError("account_id must start with 'signal.'")

    click.echo(f"Unlocking sealed store for {account_id}...")
    store = _open_store()
    store.add(
        f"{account_id}.daemon_url",
        daemon_url.encode("utf-8"),
        metadata={"kind": "signal_daemon_url"},
    )
    store.add(
        f"{account_id}.phone",
        phone.encode("utf-8"),
        metadata={"kind": "signal_phone", "display_name": display_name},
    )
    click.echo(f"Stored {account_id}.daemon_url and {account_id}.phone")

    asyncio.run(_link_and_register(
        account_id=account_id, display_name=display_name,
        phone=phone, daemon_url=daemon_url,
        device_name=device_name, link_timeout_s=link_timeout_s,
    ))


async def _link_and_register(
    *, account_id: str, display_name: str, phone: str,
    daemon_url: str, device_name: str, link_timeout_s: int,
) -> None:
    client = SignalCliClient(base_url=daemon_url)
    try:
        await client.health()
    except SignalCliError as e:
        raise click.ClickException(
            f"signal-cli-rest-api at {daemon_url} not reachable: {e}"
        )

    click.echo("Fetching link QR from daemon...")
    try:
        png = await client.qr_link(phone=phone, device_name=device_name)
    except SignalCliError as e:
        raise click.ClickException(f"failed to fetch QR: {e}")

    qr_path = Path(tempfile.gettempdir()) / f"signal-link-{account_id}.png"
    qr_path.write_bytes(png)
    click.echo(f"QR saved to {qr_path}")
    click.echo("Open it, then on your phone: Signal → Settings → Linked devices → Link new device.")
    click.echo(f"Waiting up to {link_timeout_s}s for link to complete...")

    deadline = asyncio.get_event_loop().time() + link_timeout_s
    linked = False
    while asyncio.get_event_loop().time() < deadline:
        try:
            await client.receive(phone=phone)
            linked = True
            break
        except SignalCliError:
            await asyncio.sleep(2.0)
    if not linked:
        raise click.ClickException(
            "link did not complete in time; rerun add_account once you've scanned"
        )
    click.echo(f"Linked. Inserting channel_accounts row for {account_id}.")

    await _insert_channel_account(
        account_id=account_id,
        display_name=display_name,
        credentials_ref=f"{account_id}.phone",
        metadata={"phone": phone, "daemon_url": daemon_url, "device_name": device_name},
    )
    click.echo(f"Done. {account_id} is ready.")


async def _insert_channel_account(
    *, account_id: str, display_name: str,
    credentials_ref: str, metadata: dict,
) -> None:
    from shared.db import connection

    async with connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO channel_accounts
                    (account_id, channel_type, display_name, credentials, credentials_ref, metadata, status)
                VALUES (%s, 'signal', %s, '{}'::jsonb, %s, %s::jsonb, 'active')
                ON CONFLICT (account_id) DO UPDATE
                SET display_name = EXCLUDED.display_name,
                    credentials_ref = EXCLUDED.credentials_ref,
                    metadata = channel_accounts.metadata || EXCLUDED.metadata,
                    updated_at = now()
                """,
                (
                    account_id, display_name, credentials_ref,
                    json.dumps(metadata),
                ),
            )
        await conn.commit()


if __name__ == "__main__":
    main()
