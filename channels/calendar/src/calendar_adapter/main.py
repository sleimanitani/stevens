"""Google Calendar webhook handler.

Endpoints:
  POST /calendar/push  — Google's push channel notification (headers only, no body)
  GET  /health

Security:
  Google sends ``X-Goog-Channel-Token`` in every push. We set this token
  when we register the channel (``calendar.watch_events``) and store it in
  ``channel_accounts.metadata.channel_token``. The handler rejects any
  push whose token doesn't match a known account. It also verifies the
  channel-id and resource-id match the expected pair (defense in depth).

  The channel_token is a shared secret, not a signing key — it's
  equivalent to the Meta webhook ``verify_token``. Compromise means
  spoofed "please resync" nudges, not access to event data (that requires
  the OAuth token, which lives only in the Security Agent).
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, Header, HTTPException, Response

from shared.accounts import list_accounts
from shared.bus import publish
from shared.db import close_pool, connection
from shared.events import CalendarEventChangedEvent
from shared.security_client import SecurityClient, SecurityClientError

log = logging.getLogger(__name__)
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with connection() as conn:
        accounts = await list_accounts(conn, channel_type="calendar")
        log.info(
            "calendar-adapter ready, %d active accounts", len(accounts)
        )
    yield
    await close_pool()


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/calendar/push")
async def calendar_push(
    x_goog_channel_id: Optional[str] = Header(None),
    x_goog_resource_id: Optional[str] = Header(None),
    x_goog_resource_state: Optional[str] = Header(None),
    x_goog_channel_token: Optional[str] = Header(None),
):
    if not x_goog_channel_id or not x_goog_resource_id or not x_goog_channel_token:
        raise HTTPException(status_code=400, detail="missing X-Goog-* headers")

    account = await _account_for_channel(x_goog_channel_id, x_goog_resource_id)
    if account is None:
        raise HTTPException(status_code=404, detail="no account for channel")

    expected_token = (account.metadata or {}).get("channel_token")
    if not expected_token or expected_token != x_goog_channel_token:
        log.warning(
            "channel_token mismatch for account=%s channel_id=%s",
            account.account_id,
            x_goog_channel_id,
        )
        raise HTTPException(status_code=401, detail="bad channel token")

    # Google sends one "sync" state immediately on channel creation — no changes.
    if x_goog_resource_state == "sync":
        return Response(status_code=200)

    # Fan out so we can ack the push quickly.
    asyncio.create_task(
        _drain_changes(account.account_id, (account.metadata or {}))
    )
    return Response(status_code=200)


async def _account_for_channel(channel_id: str, resource_id: str):
    async with connection() as conn:
        accounts = await list_accounts(conn, channel_type="calendar")
    for a in accounts:
        m = a.metadata or {}
        if m.get("channel_id") == channel_id and m.get("resource_id") == resource_id:
            return a
    return None


async def _drain_changes(account_id: str, metadata: dict) -> None:
    calendar_id = metadata.get("calendar_id", "primary")
    sync_token = metadata.get("sync_token")
    next_sync_token: Optional[str] = None
    page_token: Optional[str] = None

    while True:
        params: dict = {
            "account_id": account_id,
            "calendar_id": calendar_id,
            "single_events": True,
        }
        if sync_token:
            params["sync_token"] = sync_token
        if page_token:
            params["page_token"] = page_token

        try:
            resp = await _security_client().call("calendar.list_events", params)
        except SecurityClientError as e:
            log.error("calendar.list_events failed for %s: %s", account_id, e)
            return

        for item in resp.get("items") or []:
            await _publish_change(account_id, calendar_id, item)

        page_token = resp.get("nextPageToken")
        next_sync_token = resp.get("nextSyncToken") or next_sync_token
        if not page_token:
            break

    if next_sync_token:
        await _save_sync_token(account_id, next_sync_token)


async def _publish_change(account_id: str, calendar_id: str, item: dict) -> None:
    status = item.get("status", "confirmed")
    summary = item.get("summary", "") or ""
    start = item.get("start") or {}
    end = item.get("end") or {}
    attendees = [
        a.get("email") for a in (item.get("attendees") or []) if a.get("email")
    ]
    organizer = (item.get("organizer") or {}).get("email")

    event = CalendarEventChangedEvent(
        account_id=account_id,
        calendar_id=calendar_id,
        gcal_event_id=item.get("id", ""),
        status=status,
        summary=summary,
        start=start.get("dateTime") or start.get("date"),
        end=end.get("dateTime") or end.get("date"),
        organizer=organizer,
        attendees=attendees,
        html_link=item.get("htmlLink"),
        raw_ref=f"calendar:events/{item.get('id', '')}",
    )
    await publish(event)


async def _save_sync_token(account_id: str, sync_token: str) -> None:
    async with connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE channel_accounts
                SET metadata = metadata
                    || jsonb_build_object('sync_token', %s::text),
                    updated_at = now()
                WHERE account_id = %s
                """,
                (sync_token, account_id),
            )
        await conn.commit()
