"""Add a WhatsApp Cloud API account to Stevens.

Usage::

    uv run python -m whatsapp_cloud_adapter.add_account \\
        --id wac.business1 --name "Work WhatsApp" \\
        --phone-number-id 999888777 \\
        --access-token-stdin \\
        --verify-token "some-long-random-nonce-you-used-in-meta-webhook-config"

Populates the sealed store with:

- ``wac.business1.access_token``       (from stdin — never on argv, never in shell history)
- ``wac.business1.phone_number_id``    (from --phone-number-id)

And inserts a ``channel_accounts`` row with ``channel_type='whatsapp_cloud'``,
``credentials_ref='wac.business1.access_token'``, metadata carrying the
``phone_number_id``, ``verify_token``, and display-friendly ``display_phone_number``.

One-time prerequisite: store the shared app secret::

    echo -n "$APP_SECRET" | uv run stevens secrets add whatsapp_cloud.app_secret --from-stdin

This is the key used to HMAC-verify inbound webhooks. Same value for all
accounts under one Meta app.
"""

from __future__ import annotations

import asyncio
import getpass
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

import click

log = logging.getLogger(__name__)


def _load_passphrase() -> bytes:
    env = os.environ.get("STEVENS_PASSPHRASE")
    if env is not None:
        return env.encode("utf-8")
    return getpass.getpass("sealed-store passphrase: ").encode("utf-8")


def _open_store():
    from demiurge.sealed_store import SealedStore

    root = Path(
        os.environ.get("STEVENS_SECURITY_SECRETS", "/var/lib/stevens/secrets")
    )
    return SealedStore.unlock(root, _load_passphrase())


@click.command()
@click.option("--id", "account_id", required=True, help="Stable slug, e.g. wac.business1")
@click.option("--name", "display_name", required=True)
@click.option("--phone-number-id", required=True, help="Meta's numeric phone-number id")
@click.option(
    "--display-phone-number",
    default="",
    help="Human-readable phone number for display (e.g. +1-555-1234)",
)
@click.option(
    "--access-token-stdin",
    is_flag=True,
    help="Read the access token from stdin (preferred — never on argv)",
)
@click.option(
    "--access-token",
    default=None,
    help=(
        "Access token as a flag. Discouraged — shows up in shell history and "
        "`ps`. Use --access-token-stdin instead."
    ),
)
@click.option(
    "--verify-token",
    required=True,
    help="Meta webhook verify token (the nonce you set in the webhook config)",
)
def main(
    account_id: str,
    display_name: str,
    phone_number_id: str,
    display_phone_number: str,
    access_token_stdin: bool,
    access_token: Optional[str],
    verify_token: str,
) -> None:
    """Onboard a new WhatsApp Cloud API account."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    if not account_id.startswith("wac."):
        raise click.UsageError("account_id must start with 'wac.'")

    if access_token_stdin and access_token:
        raise click.UsageError(
            "combine --access-token-stdin and --access-token exclusively"
        )
    if access_token_stdin:
        token = sys.stdin.read().strip().encode("utf-8")
    elif access_token:
        token = access_token.encode("utf-8")
    else:
        raise click.UsageError(
            "one of --access-token-stdin or --access-token is required"
        )

    click.echo(f"Unlocking sealed store for {account_id} ({display_name})...")
    store = _open_store()

    token_ref = f"{account_id}.access_token"
    phone_ref = f"{account_id}.phone_number_id"

    store.add(token_ref, token, metadata={"kind": "wac_access_token"})
    store.add(phone_ref, phone_number_id.encode("utf-8"), metadata={"kind": "wac_phone_number_id"})
    click.echo(f"Stored {token_ref} and {phone_ref}.")

    asyncio.run(
        _insert_channel_account(
            account_id=account_id,
            display_name=display_name,
            credentials_ref=token_ref,
            metadata={
                "phone_number_id": phone_number_id,
                "display_phone_number": display_phone_number,
                "verify_token": verify_token,
            },
        )
    )
    click.echo(f"channel_accounts row for {account_id} inserted.")
    del token


async def _insert_channel_account(
    *,
    account_id: str,
    display_name: str,
    credentials_ref: str,
    metadata: dict,
) -> None:
    from shared.db import connection

    async with connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO channel_accounts
                    (account_id, channel_type, display_name, credentials, credentials_ref, metadata, status)
                VALUES (%s, 'whatsapp_cloud', %s, '{}'::jsonb, %s, %s::jsonb, 'active')
                ON CONFLICT (account_id) DO UPDATE
                SET display_name = EXCLUDED.display_name,
                    credentials_ref = EXCLUDED.credentials_ref,
                    metadata = channel_accounts.metadata || EXCLUDED.metadata,
                    updated_at = now()
                """,
                (
                    account_id,
                    display_name,
                    credentials_ref,
                    json.dumps(metadata),
                ),
            )
        await conn.commit()


if __name__ == "__main__":
    main()
