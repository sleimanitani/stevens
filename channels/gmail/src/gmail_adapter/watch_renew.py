"""Gmail users.watch() renewal.

Google expires Gmail push watches after 7 days. This script renews every
active Gmail account's watch. Run it from cron once a day.

Implementation: asks the Security Agent to call users.watch() for each
account — it holds the access token, not us. The returned historyId /
expiration land back in ``channel_accounts.metadata``.

Usage::

    uv run python -m gmail_adapter.watch_renew
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from shared.accounts import list_accounts
from shared.bus import publish  # unused but confirms shared is importable
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
        caller_name=os.environ.get("STEVENS_CALLER_NAME", "gmail_adapter"),
        private_key_path=os.environ["STEVENS_PRIVATE_KEY_PATH"],
    )
    return _CLIENT


async def renew_all_watches() -> None:
    topic = os.environ.get("GMAIL_PUBSUB_TOPIC")
    if not topic:
        raise RuntimeError("GMAIL_PUBSUB_TOPIC env required")

    async with connection() as conn:
        accounts = await list_accounts(conn, channel_type="gmail", status="active")

    for account in accounts:
        try:
            resp = await _security_client().call(
                "gmail.watch",
                {
                    "account_id": account.account_id,
                    "topic_name": topic,
                    "label_ids": ["INBOX"],
                },
            )
        except SecurityClientError as e:
            log.error(
                "renewal failed for %s: %s",
                account.account_id,
                e,
            )
            continue
        history_id = resp.get("historyId")
        expiration = resp.get("expiration")
        async with connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE channel_accounts
                    SET metadata = metadata
                        || jsonb_build_object(
                            'history_id_cursor', %s::text,
                            'watch_expiration', %s::text
                        ),
                        updated_at = now()
                    WHERE account_id = %s
                    """,
                    (str(history_id), str(expiration), account.account_id),
                )
            await conn.commit()
        log.info(
            "renewed %s — historyId=%s expiration=%s",
            account.account_id,
            history_id,
            expiration,
        )


async def _amain() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    try:
        await renew_all_watches()
    finally:
        await close_pool()


def main() -> int:
    asyncio.run(_amain())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
