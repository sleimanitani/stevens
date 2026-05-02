"""Gmail capabilities — the surface agents use to touch a Gmail account.

Every capability here is routed through the Security Agent, which owns
the OAuth credentials. Calling agents receive only the non-sensitive
result (thread metadata, the draft id, the applied label) — never a
token, client id, or client secret.

The handlers expect ``context.outbound.gmail`` to be a
:class:`demiurge.outbound.gmail.GmailClient`. If it isn't, the
capability fails cleanly rather than blowing up cryptically.

Naming: ``account_id`` is a clear-text routing label. Everything else
(query, body, draft contents) is treated as sensitive by audit.
"""

from __future__ import annotations

from typing import Any, Dict

from ..context import CapabilityContext
from ..identity import RegisteredAgent
from ..outbound.gmail import GmailClient
from .registry import capability


def _gmail(ctx: CapabilityContext) -> GmailClient:
    outbound = ctx.outbound
    if outbound is None:
        raise RuntimeError("no outbound client configured")
    client = getattr(outbound, "gmail", None)
    if not isinstance(client, GmailClient):
        raise RuntimeError("outbound.gmail is not a GmailClient")
    return client


@capability("gmail.search", clear_params=["max_results"])
async def gmail_search(
    agent: RegisteredAgent, params: Dict[str, Any], context: CapabilityContext
) -> Dict[str, Any]:
    account_id = params["account_id"]
    query = params.get("query", "")
    max_results = int(params.get("max_results", 20))
    return await _gmail(context).search(account_id, query, max_results=max_results)


@capability("gmail.get_thread")
async def gmail_get_thread(
    agent: RegisteredAgent, params: Dict[str, Any], context: CapabilityContext
) -> Dict[str, Any]:
    return await _gmail(context).get_thread(params["account_id"], params["thread_id"])


@capability("gmail.create_draft")
async def gmail_create_draft(
    agent: RegisteredAgent, params: Dict[str, Any], context: CapabilityContext
) -> Dict[str, Any]:
    raw = params.get("raw_rfc822")
    if isinstance(raw, str):
        raw_bytes = raw.encode("utf-8")
    elif isinstance(raw, (bytes, bytearray)):
        raw_bytes = bytes(raw)
    else:
        raise RuntimeError("raw_rfc822 must be bytes or string")
    result = await _gmail(context).create_draft(
        params["account_id"], params["thread_id"], raw_bytes
    )
    # Return only the non-sensitive metadata — the draft's full message
    # body (which we just sent) doesn't need to round-trip back through
    # the UDS.
    return {
        "id": result.get("id"),
        "message_id": (result.get("message") or {}).get("id"),
        "thread_id": (result.get("message") or {}).get("threadId"),
    }


@capability("gmail.add_label", clear_params=["label_id"])
async def gmail_add_label(
    agent: RegisteredAgent, params: Dict[str, Any], context: CapabilityContext
) -> Dict[str, Any]:
    return await _gmail(context).add_label(
        params["account_id"], params["thread_id"], params["label_id"]
    )


@capability("gmail.remove_label", clear_params=["label_id"])
async def gmail_remove_label(
    agent: RegisteredAgent, params: Dict[str, Any], context: CapabilityContext
) -> Dict[str, Any]:
    return await _gmail(context).remove_label(
        params["account_id"], params["thread_id"], params["label_id"]
    )


@capability("gmail.list_history", clear_params=["history_id", "history_types"])
async def gmail_list_history(
    agent: RegisteredAgent, params: Dict[str, Any], context: CapabilityContext
) -> Dict[str, Any]:
    return await _gmail(context).list_history(
        params["account_id"],
        str(params["history_id"]),
        history_types=params.get("history_types"),
    )


@capability("gmail.get_message", clear_params=["format"])
async def gmail_get_message(
    agent: RegisteredAgent, params: Dict[str, Any], context: CapabilityContext
) -> Dict[str, Any]:
    return await _gmail(context).get_message(
        params["account_id"],
        params["message_id"],
        fmt=params.get("format", "full"),
    )


@capability("gmail.watch", clear_params=["topic_name"])
async def gmail_watch(
    agent: RegisteredAgent, params: Dict[str, Any], context: CapabilityContext
) -> Dict[str, Any]:
    return await _gmail(context).watch(
        params["account_id"],
        params["topic_name"],
        label_ids=params.get("label_ids"),
    )


@capability("gmail.get_profile")
async def gmail_get_profile(
    agent: RegisteredAgent, params: Dict[str, Any], context: CapabilityContext
) -> Dict[str, Any]:
    return await _gmail(context).get_profile(params["account_id"])
