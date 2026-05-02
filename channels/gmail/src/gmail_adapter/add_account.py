"""Gmail add-account CLI.

Runs Google OAuth locally, extracts the refresh token, stores it in the
sealed store, calls ``users.watch()`` to register with Gmail Pub/Sub, and
inserts a ``channel_accounts`` row with ``credentials_ref`` pointing at
the sealed-store entry.

OAuth client id and secret come from the sealed store
(``gmail.oauth_client.id`` / ``gmail.oauth_client.secret``). See
``docs/runbooks/gmail-oauth-setup.md``.

Usage::

    uv run python -m gmail_adapter.add_account \\
        --id gmail.personal --name "Sol personal"

Requires:

- ``STEVENS_PASSPHRASE`` in env (one-shot unlock for the sealed store),
  or interactive prompt via ``getpass``.
- ``GMAIL_PUBSUB_TOPIC`` env (full ``projects/.../topics/...`` form).
- ``DATABASE_URL`` env for the channel_accounts insert.
"""

from __future__ import annotations

import asyncio
import getpass
import json
import logging
import os
from pathlib import Path
from typing import Optional

import click
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

log = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


def _load_passphrase() -> bytes:
    env = os.environ.get("STEVENS_PASSPHRASE")
    if env is not None:
        return env.encode("utf-8")
    return getpass.getpass("sealed-store passphrase: ").encode("utf-8")


def _open_store():
    # Local import so non-CLI imports of this module don't pull in the whole
    # security package.
    from demiurge.sealed_store import SealedStore

    root = Path(
        os.environ.get("STEVENS_SECURITY_SECRETS", "/var/lib/stevens/secrets")
    )
    return SealedStore.unlock(root, _load_passphrase())


@click.command()
@click.option("--id", "account_id", required=True, help="Stable slug, e.g. gmail.personal")
@click.option("--name", "display_name", required=True, help="Human-readable display name")
@click.option(
    "--oauth-port",
    default=0,
    type=int,
    help="Local callback port (0 picks a free one — default).",
)
def main(account_id: str, display_name: str, oauth_port: int) -> None:
    """Onboard a new Gmail account."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    if not account_id.startswith("gmail."):
        raise click.UsageError("account_id must start with 'gmail.'")

    pubsub_topic = os.environ.get("GMAIL_PUBSUB_TOPIC")
    if not pubsub_topic:
        raise click.UsageError("GMAIL_PUBSUB_TOPIC env required")

    click.echo(f"Unlocking sealed store for {account_id} ({display_name})...")
    store = _open_store()

    try:
        client_id = store.get_by_name("gmail.oauth_client.id").decode("utf-8")
        client_secret = store.get_by_name("gmail.oauth_client.secret").decode("utf-8")
    except Exception as e:  # noqa: BLE001
        raise click.ClickException(
            "OAuth client credentials missing from the sealed store — "
            "run the runbook in docs/runbooks/gmail-oauth-setup.md first. "
            f"({e})"
        )

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uris": ["http://localhost"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }
    flow = InstalledAppFlow.from_client_config(client_config, _SCOPES)
    click.echo("A browser window will open. Sign in to the Gmail account you're onboarding.")
    creds: Credentials = flow.run_local_server(port=oauth_port, open_browser=True)

    # Validate which Gmail account we actually got — catches the common
    # mistake of signing in with the wrong account.
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    profile = service.users().getProfile(userId="me").execute()
    email_address = profile.get("emailAddress")
    history_id = profile.get("historyId")
    click.echo(f"Authenticated as {email_address}.")

    refresh_token = creds.refresh_token
    if not refresh_token:
        raise click.ClickException(
            "OAuth response did not include a refresh token — usually this "
            "means Google already has consent on record for this account. "
            "Revoke prior consent at https://myaccount.google.com/permissions "
            "and retry."
        )

    # Stash the refresh token in the sealed store before doing anything else,
    # so even if the watch call or DB insert fails we don't lose it.
    secret_name = f"{account_id}.refresh_token"
    store.add(
        secret_name,
        refresh_token.encode("utf-8"),
        metadata={"kind": "gmail_refresh_token", "email_address": email_address},
    )
    click.echo(f"Stored refresh token as {secret_name}.")

    watch_resp = (
        service.users()
        .watch(
            userId="me",
            body={"topicName": pubsub_topic, "labelIds": ["INBOX"]},
        )
        .execute()
    )
    watch_expiration = watch_resp.get("expiration")
    click.echo(
        f"users.watch() registered — expiration={watch_expiration} "
        f"historyId={watch_resp.get('historyId')}."
    )

    # Database insert. Kept small so the deps are minimal.
    asyncio.run(
        _insert_channel_account(
            account_id=account_id,
            display_name=display_name,
            credentials_ref=secret_name,
            metadata={
                "email_address": email_address,
                "history_id_cursor": watch_resp.get("historyId") or history_id,
                "watch_expiration": watch_expiration,
            },
        )
    )
    click.echo(f"channel_accounts row for {account_id} inserted.")

    # Drop any in-memory creds we no longer need.
    del creds
    del refresh_token


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
                VALUES (%s, 'gmail', %s, '{}'::jsonb, %s, %s::jsonb, 'active')
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
