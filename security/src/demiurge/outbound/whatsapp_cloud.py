"""WhatsApp Cloud API outbound client.

The Security Agent's WhatsApp channel (Meta/Facebook Cloud API edition).
Adapters and agents never hold the access token or the app secret — they
request capabilities (``whatsapp.send_text``, ``whatsapp.verify_webhook``,
...) and the Security Agent's process does the outbound HTTP + HMAC.

Sealed-store secret-naming convention:

- ``<account_id>.access_token``         — per-account long-lived access token
                                          (account_id is ``wac.<slug>`` per
                                          Stevens naming, e.g. ``wac.business1``)
- ``<account_id>.phone_number_id``      — per-account numeric id that Meta
                                          uses to address the phone
- ``whatsapp_cloud.app_secret``         — shared app secret for HMAC-SHA256
                                          verification of inbound webhooks

For webhook *registration* (the GET handshake at setup), the verify_token
lives in ``channel_accounts.metadata.verify_token`` — it's not a secret in
the cryptographic sense, just a registration nonce.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any, Dict, List, Optional

from .client import OutboundClient, OutboundError

_API_BASE = "https://graph.facebook.com/v20.0"


class WhatsAppCloudClient:
    """Authenticated Cloud API client, one logical phone per ``account_id``."""

    def __init__(self, outbound: OutboundClient) -> None:
        self._outbound = outbound

    async def _auth_headers(self, account_id: str) -> Dict[str, str]:
        token = self._outbound.sealed_store.get_by_name(
            f"{account_id}.access_token"
        ).decode("utf-8")
        return {"Authorization": f"Bearer {token}"}

    async def _phone_number_id(self, account_id: str) -> str:
        return self._outbound.sealed_store.get_by_name(
            f"{account_id}.phone_number_id"
        ).decode("utf-8")

    async def _api(
        self,
        method: str,
        account_id: str,
        path: str,
        *,
        json: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        headers = await self._auth_headers(account_id)
        phone_id = await self._phone_number_id(account_id)
        return await self._outbound.request(
            method,
            f"{_API_BASE}/{phone_id}{path}",
            headers=headers,
            params=params,
            json=json,
        )

    # --- public surface used by capabilities ---

    async def send_text(
        self, account_id: str, to: str, body: str, *, preview_url: bool = False
    ) -> Dict[str, Any]:
        """Send a free-form text message.

        ``to`` is an E.164 phone number (e.g. ``+15551234567``). Meta will
        reject messages outside the 24-hour customer-service window unless
        a template is used — see :meth:`send_template`.
        """
        return await self._api(
            "POST",
            account_id,
            "/messages",
            json={
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "text",
                "text": {"preview_url": preview_url, "body": body},
            },
        )

    async def send_template(
        self,
        account_id: str,
        to: str,
        *,
        name: str,
        language_code: str = "en_US",
        components: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Send a pre-approved template message.

        Templates are the only way to initiate a conversation with a user
        outside the 24-hour service window.
        """
        template: Dict[str, Any] = {
            "name": name,
            "language": {"code": language_code},
        }
        if components:
            template["components"] = components
        return await self._api(
            "POST",
            account_id,
            "/messages",
            json={
                "messaging_product": "whatsapp",
                "to": to,
                "type": "template",
                "template": template,
            },
        )

    async def mark_read(
        self, account_id: str, message_id: str
    ) -> Dict[str, Any]:
        return await self._api(
            "POST",
            account_id,
            "/messages",
            json={
                "messaging_product": "whatsapp",
                "status": "read",
                "message_id": message_id,
            },
        )

    async def get_media_url(self, account_id: str, media_id: str) -> str:
        """Resolve a media-id (from an inbound message) to a temporary download URL."""
        headers = await self._auth_headers(account_id)
        body = await self._outbound.request(
            "GET",
            f"{_API_BASE}/{media_id}",
            headers=headers,
        )
        url = body.get("url")
        if not isinstance(url, str):
            raise OutboundError(f"malformed media response: {body!r}")
        return url

    async def verify_webhook_signature(
        self, signature_header: str, raw_body: bytes
    ) -> bool:
        """Validate ``X-Hub-Signature-256`` against the app secret.

        The signature is ``sha256=<hex>`` where the HMAC key is the app
        secret and the message is the raw request body. Constant-time
        comparison — never return True on a partial match.
        """
        app_secret = self._outbound.sealed_store.get_by_name(
            "whatsapp_cloud.app_secret"
        )
        if not signature_header.startswith("sha256="):
            return False
        provided = signature_header[len("sha256="):]
        expected = hmac.new(app_secret, raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(provided, expected)
