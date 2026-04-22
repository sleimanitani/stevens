"""Gmail adapter.

Receives Gmail Pub/Sub push notifications, requests incremental history
from the Security Agent (``gmail.list_history`` + ``gmail.get_message``),
publishes ``EmailReceivedEvent`` to the bus. One adapter process handles
all configured Gmail accounts.

Endpoints:
  POST /gmail/push   — Pub/Sub webhook (public via Tailscale Funnel)
  GET  /health       — liveness check (local only)

Security boundary:
  ``/gmail/push`` verifies the JWT in the Authorization header against
  Google's public keys before processing. This is the only thing between
  the public internet and our event bus.

  The adapter never holds a Gmail OAuth token. Per-account refresh
  tokens live in the sealed store; the Security Agent is what exchanges
  them for access tokens and talks to Gmail. The adapter just asks
  "what's new for this account since historyId X?"
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, Header, HTTPException, Request
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from shared.accounts import get_account, list_accounts
from shared.bus import publish
from shared.db import close_pool, connection
from shared.events import EmailReceivedEvent
from shared.security_client import SecurityClient, SecurityClientError

log = logging.getLogger(__name__)
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))

EXPECTED_AUDIENCE = os.environ.get("GMAIL_PUBLIC_URL", "") + "/gmail/push"

_CLIENT: Optional[SecurityClient] = None


def _security_client() -> SecurityClient:
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    socket = os.environ.get("STEVENS_SECURITY_SOCKET", "/run/stevens/security.sock")
    caller = os.environ.get("STEVENS_CALLER_NAME", "gmail_adapter")
    key_path = os.environ["STEVENS_PRIVATE_KEY_PATH"]
    _CLIENT = SecurityClient.from_key_file(
        socket_path=socket,
        caller_name=caller,
        private_key_path=key_path,
    )
    return _CLIENT


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Eagerly load accounts on startup so the first push doesn't hit DB cold.
    async with connection() as conn:
        accounts = await list_accounts(conn, channel_type="gmail")
        log.info("gmail-adapter ready, %d active accounts", len(accounts))
    yield
    await close_pool()


app = FastAPI(lifespan=lifespan)


def verify_pubsub_jwt(authorization: str | None) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        claims = id_token.verify_oauth2_token(
            token,
            google_requests.Request(),
            audience=EXPECTED_AUDIENCE,
        )
    except ValueError as e:
        log.warning("pubsub jwt verification failed: %s", e)
        raise HTTPException(status_code=401, detail="invalid token")
    if claims.get("iss") not in (
        "https://accounts.google.com",
        "accounts.google.com",
    ):
        raise HTTPException(status_code=401, detail="invalid issuer")
    return claims


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/gmail/push")
async def gmail_push(
    request: Request, authorization: str | None = Header(None)
):
    verify_pubsub_jwt(authorization)

    body = await request.json()
    msg = body.get("message") or {}
    data_b64 = msg.get("data", "")
    if not data_b64:
        return {"ok": True}

    try:
        data = json.loads(base64.b64decode(data_b64))
    except Exception as e:  # noqa: BLE001
        log.warning("failed to decode push payload: %s", e)
        return {"ok": True}

    email_address = data.get("emailAddress")
    history_id = str(data.get("historyId", ""))

    log.info("push for %s historyId=%s", email_address, history_id)

    # Fan out the actual work so we can ACK Pub/Sub fast.
    asyncio.create_task(_handle_push(email_address, history_id))
    return {"ok": True}


async def _handle_push(email_address: str, history_id: str) -> None:
    account = await _account_for_email(email_address)
    if account is None:
        log.warning("no active account for email_address=%s", email_address)
        return
    if not account.uses_sealed_store:
        log.error(
            "account %s has no credentials_ref — sealed-store migration incomplete",
            account.account_id,
        )
        return

    cursor = account.metadata.get("history_id_cursor") or history_id
    try:
        history = await _security_client().call(
            "gmail.list_history",
            {
                "account_id": account.account_id,
                "history_id": cursor,
                "history_types": ["messageAdded"],
            },
        )
    except SecurityClientError as e:
        log.error("gmail.list_history failed for %s: %s", account.account_id, e)
        return

    new_cursor = history.get("historyId") or history_id

    for entry in history.get("history", []) or []:
        for added in entry.get("messagesAdded", []) or []:
            msg_id = (added.get("message") or {}).get("id")
            if not msg_id:
                continue
            await _process_message(account.account_id, msg_id)

    await _advance_cursor(account.account_id, str(new_cursor))


async def _process_message(account_id: str, message_id: str) -> None:
    try:
        msg = await _security_client().call(
            "gmail.get_message",
            {"account_id": account_id, "message_id": message_id, "format": "full"},
        )
    except SecurityClientError as e:
        log.error(
            "gmail.get_message failed account=%s msg=%s: %s",
            account_id,
            message_id,
            e,
        )
        return

    payload = msg.get("payload") or {}
    headers = {h["name"]: h["value"] for h in payload.get("headers", []) or []}

    body_text, body_html = _extract_bodies(payload)
    event = EmailReceivedEvent(
        account_id=account_id,
        message_id=msg.get("id", message_id),
        thread_id=msg.get("threadId", ""),
        from_=headers.get("From", ""),
        to=_split_addrs(headers.get("To")),
        cc=_split_addrs(headers.get("Cc")),
        subject=headers.get("Subject", ""),
        body_text=body_text,
        body_html=body_html,
        snippet=msg.get("snippet", ""),
        labels=msg.get("labelIds") or [],
        in_reply_to=headers.get("In-Reply-To"),
        raw_ref=f"gmail:messages/{msg.get('id', message_id)}",
    )
    await publish(event)


def _split_addrs(raw: Optional[str]) -> list[str]:
    if not raw:
        return []
    return [a.strip() for a in raw.split(",") if a.strip()]


def _extract_bodies(payload: dict) -> tuple[str, str]:
    """Walk a Gmail MIME payload tree, return (text, html)."""
    text = ""
    html = ""

    def _decode_data(data: Any) -> str:
        if not isinstance(data, str):
            return ""
        try:
            return base64.urlsafe_b64decode(data + "==").decode(
                "utf-8", errors="replace"
            )
        except Exception:  # noqa: BLE001
            return ""

    stack: list[dict] = [payload]
    while stack:
        part = stack.pop()
        mime = part.get("mimeType", "")
        body = part.get("body") or {}
        data = body.get("data")
        if mime == "text/plain" and not text:
            text = _decode_data(data)
        elif mime == "text/html" and not html:
            html = _decode_data(data)
        for child in part.get("parts") or []:
            stack.append(child)
    return text, html


async def _account_for_email(email_address: Optional[str]):
    if not email_address:
        return None
    async with connection() as conn:
        accounts = await list_accounts(conn, channel_type="gmail")
    for a in accounts:
        if a.metadata.get("email_address") == email_address:
            return a
    return None


async def _advance_cursor(account_id: str, new_cursor: str) -> None:
    async with connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE channel_accounts
                SET metadata = metadata || jsonb_build_object('history_id_cursor', %s::text),
                    updated_at = now()
                WHERE account_id = %s
                """,
                (new_cursor, account_id),
            )
        await conn.commit()
