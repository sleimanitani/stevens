"""WhatsApp Cloud API webhook handler.

Endpoints:
  GET  /whatsapp/webhook   — Meta's setup handshake (hub.challenge verification).
  POST /whatsapp/webhook   — inbound events (messages, statuses) from Meta.
  GET  /health             — liveness.

Security:
  The POST endpoint verifies ``X-Hub-Signature-256`` **via the Security
  Agent's whatsapp.verify_webhook_signature capability**. The app secret
  lives only in the sealed store; this adapter never touches it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response

from shared.accounts import list_accounts
from shared.bus import publish
from shared.db import close_pool, connection
from shared.events import WhatsAppMessageEvent
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
            "DEMIURGE_SECURITY_SOCKET", "/run/demiurge/security.sock"
        ),
        caller_name=os.environ.get("DEMIURGE_CALLER_NAME", "whatsapp_cloud_adapter"),
        private_key_path=os.environ["DEMIURGE_PRIVATE_KEY_PATH"],
    )
    return _CLIENT


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with connection() as conn:
        accounts = await list_accounts(conn, channel_type="whatsapp_cloud")
        log.info(
            "whatsapp-cloud-adapter ready, %d active accounts", len(accounts)
        )
    yield
    await close_pool()


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/whatsapp/webhook")
async def webhook_setup(
    hub_mode: str = Query("", alias="hub.mode"),
    hub_challenge: str = Query("", alias="hub.challenge"),
    hub_verify_token: str = Query("", alias="hub.verify_token"),
) -> Response:
    """Meta's subscription handshake.

    Match the presented ``hub_verify_token`` against the one(s) stored in
    ``channel_accounts.metadata.verify_token`` for any active account. Any
    match = confirmation. We don't care *which* account — Meta calls this
    once per app, not once per phone.
    """
    if hub_mode != "subscribe":
        raise HTTPException(status_code=400, detail="bad hub.mode")
    async with connection() as conn:
        accounts = await list_accounts(conn, channel_type="whatsapp_cloud")
    expected = {
        (a.metadata or {}).get("verify_token") for a in accounts if a.metadata
    }
    if hub_verify_token and hub_verify_token in expected:
        return Response(content=hub_challenge, media_type="text/plain")
    raise HTTPException(status_code=403, detail="verify token mismatch")


@app.post("/whatsapp/webhook")
async def webhook_event(
    request: Request,
    x_hub_signature_256: Optional[str] = Header(None),
):
    raw_body = await request.body()
    if x_hub_signature_256 is None:
        raise HTTPException(status_code=401, detail="missing signature header")

    try:
        sig_result = await _security_client().call(
            "whatsapp.verify_webhook_signature",
            {"signature": x_hub_signature_256, "raw_body": raw_body},
        )
    except SecurityClientError as e:
        log.error("signature verification errored: %s", e)
        raise HTTPException(status_code=500, detail="sig check failed")
    if not sig_result.get("valid"):
        raise HTTPException(status_code=401, detail="bad signature")

    try:
        payload = json.loads(raw_body)
    except Exception as e:  # noqa: BLE001
        log.warning("invalid webhook json: %s", e)
        return {"ok": True}

    asyncio.create_task(_process_payload(payload))
    return {"ok": True}


async def _process_payload(payload: dict) -> None:
    """Walk Meta's nested webhook shape and publish one event per message."""
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            phone_id = (value.get("metadata") or {}).get("phone_number_id")
            account = await _account_for_phone_id(phone_id)
            if account is None:
                log.warning(
                    "no account for phone_number_id=%s — ignoring", phone_id
                )
                continue
            for msg in value.get("messages") or []:
                await _publish_message(account.account_id, value, msg)


async def _account_for_phone_id(phone_id: Optional[str]):
    if not phone_id:
        return None
    async with connection() as conn:
        accounts = await list_accounts(conn, channel_type="whatsapp_cloud")
    for a in accounts:
        if (a.metadata or {}).get("phone_number_id") == phone_id:
            return a
    return None


async def _publish_message(account_id: str, value: dict, msg: dict) -> None:
    msg_type = msg.get("type", "text")
    text_body = ""
    media_ref: Optional[str] = None
    quoted_id: Optional[str] = None
    if msg_type == "text":
        text_body = (msg.get("text") or {}).get("body", "")
    elif msg_type in ("image", "audio", "video", "document", "sticker"):
        media = msg.get(msg_type) or {}
        media_id = media.get("id")
        if media_id:
            media_ref = f"wac:media/{media_id}"
        text_body = media.get("caption", "") or ""
    if msg.get("context"):
        quoted_id = msg["context"].get("id")

    from_jid = str(msg.get("from", ""))
    contacts = value.get("contacts") or []
    push_name: Optional[str] = None
    for c in contacts:
        if c.get("wa_id") == from_jid.lstrip("+"):
            push_name = (c.get("profile") or {}).get("name")
            break

    event = WhatsAppMessageEvent(
        account_id=account_id,
        msg_id=msg.get("id", ""),
        chat_id=from_jid,
        from_jid=from_jid,
        from_push_name=push_name,
        is_group=False,
        group_id=None,
        text=text_body,
        media_ref=media_ref,
        quoted_msg_id=quoted_id,
        raw_ref=f"wac:message/{msg.get('id', '')}",
    )
    await publish(event)
