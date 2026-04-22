"""Gmail adapter.

Receives Gmail Pub/Sub push notifications, fetches new messages via the
Gmail history API, and publishes EmailReceivedEvent to the bus. One adapter
process handles all configured Gmail accounts.

Endpoints:
  POST /gmail/push   — Pub/Sub webhook (public via Tailscale Funnel)
  GET  /health       — liveness check (local only)

Security:
  /gmail/push MUST verify the JWT in the Authorization header against Google's
  public keys before processing. See verify_pubsub_jwt().
"""

from __future__ import annotations

import base64
import json
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from shared.db import close_pool

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# The service account that Google uses to send push notifications.
# Set this in your Pub/Sub push subscription config so the JWT audience matches.
EXPECTED_AUDIENCE = os.environ.get("GMAIL_PUBLIC_URL", "") + "/gmail/push"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: load accounts, start watch-renewal background tasks.
    # (Implemented in follow-up — see add_account.py and watch_renew.py)
    yield
    await close_pool()


app = FastAPI(lifespan=lifespan)


def verify_pubsub_jwt(authorization: str | None) -> dict:
    """Verify the Pub/Sub JWT and return the decoded claims.

    Reject anything that doesn't look right. This is the only thing
    standing between the public internet and our event bus.
    """
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
    # Google also requires checking the issuer is accounts.google.com
    if claims.get("iss") not in ("https://accounts.google.com", "accounts.google.com"):
        raise HTTPException(status_code=401, detail="invalid issuer")
    return claims


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/gmail/push")
async def gmail_push(request: Request, authorization: str | None = Header(None)):
    """Receive a Gmail Pub/Sub push notification.

    Payload shape:
      { "message": { "data": "<base64 json>", "messageId": "...", ... },
        "subscription": "..." }

    The decoded data JSON contains:
      { "emailAddress": "sol@example.com", "historyId": "1234567" }

    We look up the account by emailAddress, call users.history.list(
    startHistoryId=cursor), fetch each added message, and publish events.

    NOTE: this is a skeleton. The TODO sections are filled in by
    channels/gmail/src/gmail_adapter/processor.py (see next commit).
    """
    verify_pubsub_jwt(authorization)

    body = await request.json()
    msg = body.get("message", {})
    data_b64 = msg.get("data", "")
    if not data_b64:
        # Ack empty messages so Pub/Sub doesn't retry.
        return {"ok": True}

    try:
        data = json.loads(base64.b64decode(data_b64))
    except Exception as e:
        log.warning("failed to decode push payload: %s", e)
        return {"ok": True}

    email_address = data.get("emailAddress")
    history_id = data.get("historyId")

    log.info("push for %s historyId=%s", email_address, history_id)

    # TODO (day 2):
    #   1. Look up channel_account by metadata->>'email_address' = email_address
    #   2. Build Gmail service from account.credentials
    #   3. Call users.history.list(startHistoryId=account.metadata.history_id_cursor)
    #   4. For each messagesAdded: fetch message, build EmailReceivedEvent, publish
    #   5. Update account.metadata.history_id_cursor

    return {"ok": True}
