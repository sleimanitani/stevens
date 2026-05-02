"""Google Calendar add-account CLI.

OAuth flow (browser-based) for a single Calendar account. Persists the
refresh token to the sealed store, calls ``events.watch`` on the primary
calendar to register a push channel, inserts a ``channel_accounts`` row.

Usage::

    uv run python -m calendar_adapter.add_account \\
        --id calendar.personal --name "Sol personal cal" \\
        --webhook-url https://stevens.example.ts.net/calendar/push

Prerequisites: ``calendar.oauth_client.id`` and ``calendar.oauth_client.secret``
already in the sealed store. See ``docs/runbooks/gmail-oauth-setup.md`` —
the same runbook applies; use the same Google OAuth client if you like,
just store the values under the calendar.* names too.
"""

from __future__ import annotations

import asyncio
import getpass
import json
import logging
import os
import secrets
import uuid
from pathlib import Path
from typing import Optional

import click
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

log = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/calendar"]


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
@click.option("--id", "account_id", required=True, help="Stable slug, e.g. calendar.personal")
@click.option("--name", "display_name", required=True)
@click.option(
    "--webhook-url",
    required=True,
    help="HTTPS URL that Google will POST to for push (must be reachable from Google).",
)
@click.option("--calendar-id", default="primary")
@click.option("--oauth-port", default=0, type=int)
def main(
    account_id: str,
    display_name: str,
    webhook_url: str,
    calendar_id: str,
    oauth_port: int,
) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    if not account_id.startswith("calendar."):
        raise click.UsageError("account_id must start with 'calendar.'")

    click.echo(f"Unlocking sealed store for {account_id} ({display_name})...")
    store = _open_store()

    try:
        client_id = store.get_by_name("calendar.oauth_client.id").decode("utf-8")
        client_secret = store.get_by_name("calendar.oauth_client.secret").decode("utf-8")
    except Exception as e:  # noqa: BLE001
        raise click.ClickException(
            "calendar.oauth_client.id/secret missing — see docs/runbooks/gmail-oauth-setup.md "
            f"and populate the calendar.* sealed-store entries. ({e})"
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
    click.echo("A browser window will open. Sign in to the Calendar account you're onboarding.")
    creds: Credentials = flow.run_local_server(port=oauth_port, open_browser=True)

    refresh_token = creds.refresh_token
    if not refresh_token:
        raise click.ClickException(
            "OAuth did not return a refresh token — revoke prior consent at "
            "https://myaccount.google.com/permissions and retry."
        )

    secret_name = f"{account_id}.refresh_token"
    store.add(
        secret_name,
        refresh_token.encode("utf-8"),
        metadata={"kind": "calendar_refresh_token"},
    )
    click.echo(f"Stored refresh token as {secret_name}.")

    # Call calendar.watch_events via SecurityClient — the CLI doesn't hold an
    # access token. We do this *after* the sealed-store write so even if the
    # watch call fails we don't lose the refresh token.
    from shared.security_client import SecurityClient

    sec_socket = os.environ.get(
        "STEVENS_SECURITY_SOCKET", "/run/stevens/security.sock"
    )
    sec_caller = os.environ.get("STEVENS_CALLER_NAME", "calendar_adapter")
    sec_keypath = os.environ.get("STEVENS_PRIVATE_KEY_PATH")
    if not sec_keypath:
        raise click.ClickException(
            "STEVENS_PRIVATE_KEY_PATH env required so the add_account CLI can "
            "call calendar.watch_events through the Security Agent."
        )

    client = SecurityClient.from_key_file(
        socket_path=sec_socket,
        caller_name=sec_caller,
        private_key_path=sec_keypath,
    )

    channel_id = f"stevens-{account_id}-{uuid.uuid4()}"
    channel_token = secrets.token_urlsafe(32)

    async def _register():
        return await client.call(
            "calendar.watch_events",
            {
                "account_id": account_id,
                "calendar_id": calendar_id,
                "channel_id": channel_id,
                "webhook_url": webhook_url,
                "channel_token": channel_token,
            },
        )

    watch_resp = asyncio.run(_register())
    resource_id = watch_resp.get("resourceId")
    expiration = watch_resp.get("expiration")
    click.echo(
        f"calendar.watch_events registered — channel_id={channel_id} "
        f"resourceId={resource_id} expiration={expiration}"
    )

    asyncio.run(
        _insert_channel_account(
            account_id=account_id,
            display_name=display_name,
            credentials_ref=secret_name,
            metadata={
                "calendar_id": calendar_id,
                "channel_id": channel_id,
                "resource_id": resource_id,
                "channel_token": channel_token,
                "watch_expiration": expiration,
            },
        )
    )
    click.echo(f"channel_accounts row for {account_id} inserted.")
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
                VALUES (%s, 'calendar', %s, '{}'::jsonb, %s, %s::jsonb, 'active')
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
