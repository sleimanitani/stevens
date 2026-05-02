"""WhatsApp Cloud API capabilities.

Every capability routes through the Security Agent, which owns the per-
account access token and the shared app secret. Adapters and agents never
touch either.
"""

from __future__ import annotations

from typing import Any, Dict

from ..context import CapabilityContext
from ..identity import RegisteredAgent
from ..outbound.whatsapp_cloud import WhatsAppCloudClient
from .registry import capability


def _wac(ctx: CapabilityContext) -> WhatsAppCloudClient:
    outbound = ctx.outbound
    if outbound is None:
        raise RuntimeError("no outbound client configured")
    client = getattr(outbound, "whatsapp_cloud", None)
    if not isinstance(client, WhatsAppCloudClient):
        raise RuntimeError("outbound.whatsapp_cloud is not a WhatsAppCloudClient")
    return client


@capability("whatsapp.send_text")
async def wac_send_text(
    agent: RegisteredAgent, params: Dict[str, Any], context: CapabilityContext
) -> Dict[str, Any]:
    result = await _wac(context).send_text(
        params["account_id"],
        params["to"],
        params["body"],
        preview_url=bool(params.get("preview_url", False)),
    )
    # Keep just the non-sensitive ids in the response — no bodies echoed back.
    messages = result.get("messages") or []
    return {
        "messaging_product": result.get("messaging_product"),
        "contacts": result.get("contacts"),
        "message_ids": [m.get("id") for m in messages if isinstance(m, dict)],
    }


@capability(
    "whatsapp.send_template", clear_params=["name", "language_code"]
)
async def wac_send_template(
    agent: RegisteredAgent, params: Dict[str, Any], context: CapabilityContext
) -> Dict[str, Any]:
    result = await _wac(context).send_template(
        params["account_id"],
        params["to"],
        name=params["name"],
        language_code=params.get("language_code", "en_US"),
        components=params.get("components"),
    )
    messages = result.get("messages") or []
    return {
        "message_ids": [m.get("id") for m in messages if isinstance(m, dict)],
    }


@capability("whatsapp.mark_read")
async def wac_mark_read(
    agent: RegisteredAgent, params: Dict[str, Any], context: CapabilityContext
) -> Dict[str, Any]:
    return await _wac(context).mark_read(params["account_id"], params["message_id"])


@capability("whatsapp.get_media_url", clear_params=["media_id"])
async def wac_get_media_url(
    agent: RegisteredAgent, params: Dict[str, Any], context: CapabilityContext
) -> Dict[str, Any]:
    url = await _wac(context).get_media_url(
        params["account_id"], params["media_id"]
    )
    # The URL is sensitive (short-lived bearer-embedded), so we return it
    # only to callers that are about to fetch — which is a capability for
    # the adapter, not an agent. Surface it as "url" and let the caller
    # decide.
    return {"url": url}


@capability("whatsapp.verify_webhook_signature", clear_params=["signature"])
async def wac_verify_webhook_signature(
    agent: RegisteredAgent, params: Dict[str, Any], context: CapabilityContext
) -> Dict[str, Any]:
    """Broker-mediated HMAC-SHA256 signature check for inbound webhooks.

    The adapter hands us the ``X-Hub-Signature-256`` header and the raw
    request body; we verify against the sealed-store app secret. The
    adapter never sees the secret.
    """
    sig = params["signature"]
    raw = params.get("raw_body")
    if isinstance(raw, str):
        raw_bytes = raw.encode("utf-8")
    elif isinstance(raw, (bytes, bytearray)):
        raw_bytes = bytes(raw)
    else:
        raise RuntimeError("raw_body must be bytes or string")
    ok = await _wac(context).verify_webhook_signature(sig, raw_bytes)
    return {"valid": ok}
