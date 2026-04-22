"""Gmail add-account CLI.

Usage:
    uv run python -m gmail_adapter.add_account --id gmail.personal --name "Sol personal"

Steps:
    1. Run OAuth flow (opens browser) with scope gmail.modify
    2. Store credentials in channel_accounts
    3. Call users.watch() to register with the Pub/Sub topic
    4. Store initial historyId as cursor in account metadata

This is a skeleton — full implementation lands day 2 once Google Cloud
project is provisioned and OAuth client is downloaded.
"""

from __future__ import annotations

import asyncio
import os

import click


@click.command()
@click.option("--id", "account_id", required=True, help="Stable slug, e.g. gmail.personal")
@click.option("--name", "display_name", required=True, help="Human-readable display name")
def main(account_id: str, display_name: str) -> None:
    """Onboard a new Gmail account."""
    if not account_id.startswith("gmail."):
        raise click.UsageError("account_id must start with 'gmail.'")

    click.echo(f"Onboarding {account_id} ({display_name})...")

    # TODO (day 2):
    #   from google_auth_oauthlib.flow import InstalledAppFlow
    #   flow = InstalledAppFlow.from_client_secrets_file(
    #       os.environ["GMAIL_OAUTH_CLIENT_SECRETS"],
    #       scopes=["https://www.googleapis.com/auth/gmail.modify"],
    #   )
    #   creds = flow.run_local_server(port=0)
    #
    #   # Verify the authenticated email matches what you'd expect
    #   service = build("gmail", "v1", credentials=creds)
    #   profile = service.users().getProfile(userId="me").execute()
    #   email_address = profile["emailAddress"]
    #
    #   # Start the watch
    #   watch_resp = service.users().watch(userId="me", body={
    #       "topicName": os.environ["GMAIL_PUBSUB_TOPIC"],
    #       "labelIds": ["INBOX"],
    #   }).execute()
    #
    #   # Insert into channel_accounts with:
    #   #   credentials = creds.to_json()
    #   #   metadata = {
    #   #     "email_address": email_address,
    #   #     "history_id_cursor": watch_resp["historyId"],
    #   #     "watch_expiration": watch_resp["expiration"],
    #   #   }

    click.echo("Stub — fill in day 2.")


if __name__ == "__main__":
    main()
