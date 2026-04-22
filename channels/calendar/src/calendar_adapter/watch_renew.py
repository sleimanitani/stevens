"""Google Calendar push-channel renewal.

Google expires ``events.watch`` channels after a max TTL (often 7 days).
Cron this daily.

For each active Calendar account: stop the existing channel, start a new
one, update metadata.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import uuid
from typing import Optional

from shared.accounts import list_accounts
from shared.db import close_pool, connection
from shared.security_client import SecurityClient, SecurityClientError

log = logging.getLogger(__name__)

_CLIENT: Optional[SecurityClient] = None


def _security_client() -> SecurityClient:
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    _CLIENT = SecurityClient.from_key_file(
        socket_path=os.environ.get(
            "STEVENS_SECURITY_SOCKET", "/run/stevens/security.sock"
        ),
        caller_name=os.environ.get("STEVENS_CALLER_NAME", "calendar_adapter"),
        private_key_path=os.environ["STEVENS_PRIVATE_KEY_PATH"],
    )
    return _CLIENT


async def renew_all_channels() -> None:
    webhook_url = os.environ.get("CALENDAR_WEBHOOK_URL")
    if not webhook_url:
        raise RuntimeError("CALENDAR_WEBHOOK_URL env required")

    async with connection() as conn:
        accounts = await list_accounts(conn, channel_type="calendar", status="active")

    client = _security_client()
    for a in accounts:
        meta = a.metadata or {}
        old_channel = meta.get("channel_id")
        old_resource = meta.get("resource_id")

        # Best-effort stop of the prior channel (ignore errors).
        if old_channel and old_resource:
            try:
                await client.call(
                    "calendar.stop_channel",
                    {
                        "account_id": a.account_id,
                        "channel_id": old_channel,
                        "resource_id": old_resource,
                    },
                )
            except SecurityClientError as e:
                log.warning(
                    "stop_channel failed for %s (ignoring): %s",
                    a.account_id,
                    e,
                )

        new_channel_id = f"stevens-{a.account_id}-{uuid.uuid4()}"
        new_token = secrets.token_urlsafe(32)
        try:
            resp = await client.call(
                "calendar.watch_events",
                {
                    "account_id": a.account_id,
                    "calendar_id": meta.get("calendar_id", "primary"),
                    "channel_id": new_channel_id,
                    "webhook_url": webhook_url,
                    "channel_token": new_token,
                },
            )
        except SecurityClientError as e:
            log.error("watch_events failed for %s: %s", a.account_id, e)
            continue

        async with connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE channel_accounts
                    SET metadata = metadata || jsonb_build_object(
                        'channel_id', %s::text,
                        'resource_id', %s::text,
                        'channel_token', %s::text,
                        'watch_expiration', %s::text
                    ),
                        updated_at = now()
                    WHERE account_id = %s
                    """,
                    (
                        new_channel_id,
                        resp.get("resourceId", ""),
                        new_token,
                        str(resp.get("expiration", "")),
                        a.account_id,
                    ),
                )
            await conn.commit()
        log.info(
            "renewed %s — channel_id=%s expiration=%s",
            a.account_id,
            new_channel_id,
            resp.get("expiration"),
        )


async def _amain() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    try:
        await renew_all_channels()
    finally:
        await close_pool()


def main() -> int:
    asyncio.run(_amain())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
