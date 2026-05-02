"""Account-aware tool factory — Security-Agent-mediated edition.

**This module no longer holds or sees Gmail OAuth tokens.** Every tool here
is a thin wrapper around a capability call to the Security Agent. The
Security Agent loads the refresh token from its sealed store, exchanges
it for an access token inside its own process, makes the Gmail API call,
and returns the non-sensitive result back to us over a Unix domain socket.

This is the step-20 rewrite of the prior tool_factory, which pulled
credentials out of Postgres and bound them to a google-auth Credentials
object inside the agent process. That was the "web of things accessing
secrets" anti-pattern Sol called out at project kickoff.

Environment variables required:

- ``DEMIURGE_SECURITY_SOCKET``   default ``/run/demiurge/security.sock``
- ``DEMIURGE_CALLER_NAME``       e.g. ``email_pm`` — matches a name in
                                ``security/policy/agents.yaml``
- ``DEMIURGE_PRIVATE_KEY_PATH``  path to this agent's Ed25519 private key
                                file (mode 0o600)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Optional

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from shared.security_client import (
    AuthError,
    DenyError,
    NotFoundError,
    ResponseError,
    SecurityClient,
    TransportError,
)

log = logging.getLogger(__name__)


# --- client singleton ---


_CLIENT: Optional[SecurityClient] = None


def _client() -> SecurityClient:
    """Return the process-wide SecurityClient, lazily constructed from env."""
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    socket = os.environ.get("DEMIURGE_SECURITY_SOCKET", "/run/demiurge/security.sock")
    caller = os.environ["DEMIURGE_CALLER_NAME"]
    key_path = os.environ["DEMIURGE_PRIVATE_KEY_PATH"]
    _CLIENT = SecurityClient.from_key_file(
        socket_path=socket,
        caller_name=caller,
        private_key_path=key_path,
    )
    return _CLIENT


def _call_sync(capability: str, params: dict[str, Any]) -> dict[str, Any]:
    """Drive the async SecurityClient from a sync tool handler.

    LangChain's StructuredTool wants sync callables. We run the client's
    async call inside a fresh event loop — each tool invocation is short
    enough that the cost of loop setup is dominated by the UDS round-trip.
    """
    async def _run() -> dict[str, Any]:
        return await _client().call(capability, params)

    try:
        return asyncio.run(_run())
    except AuthError as e:
        log.error("security auth error calling %s: %s", capability, e)
        return {"error": "auth", "detail": str(e)}
    except DenyError as e:
        log.error("security policy denied %s: %s", capability, e)
        return {"error": "denied", "detail": str(e)}
    except NotFoundError as e:
        log.error("security capability not found %s: %s", capability, e)
        return {"error": "notfound", "detail": str(e)}
    except (ResponseError, TransportError) as e:
        log.error("security call failed %s: %s", capability, e)
        return {"error": "failed", "detail": str(e)}


# --- tool schemas (unchanged from previous incarnation — stable agent surface) ---


class SearchInput(BaseModel):
    account_id: str = Field(description="Account slug, e.g. 'gmail.personal'")
    query: str = Field(description="Gmail search query, same syntax as the Gmail web UI")


class ThreadInput(BaseModel):
    account_id: str
    thread_id: str


class DraftInput(BaseModel):
    account_id: str
    thread_id: str
    body: str = Field(description="Plain-text body of the draft reply")


class LabelInput(BaseModel):
    account_id: str
    thread_id: str
    label: str = Field(description="Label name (e.g. 'pm/urgent')")


# --- tool implementations ---


def _gmail_search(account_id: str, query: str) -> str:
    result = _call_sync(
        "gmail.search",
        {"account_id": account_id, "query": query, "max_results": 10},
    )
    if "error" in result:
        return json.dumps(result)
    threads = [{"id": t.get("id")} for t in (result.get("threads") or [])]
    return json.dumps(threads)


def _gmail_get_thread(account_id: str, thread_id: str) -> str:
    result = _call_sync(
        "gmail.get_thread",
        {"account_id": account_id, "thread_id": thread_id},
    )
    if "error" in result:
        return json.dumps(result)
    # Distill to the lean representation agents actually reason about.
    messages = []
    for msg in result.get("messages", []):
        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
        messages.append(
            {
                "id": msg.get("id"),
                "from": headers.get("From"),
                "to": headers.get("To"),
                "subject": headers.get("Subject"),
                "date": headers.get("Date"),
                "snippet": msg.get("snippet", ""),
                "message_id": headers.get("Message-ID"),
            }
        )
    return json.dumps({"thread_id": thread_id, "messages": messages})


def _gmail_create_draft(account_id: str, thread_id: str, body: str) -> str:
    """Create a draft reply in a thread. Draft only — never sends.

    Builds the MIME locally (agent-side) then ships the raw bytes through
    the Security Agent. Agent-side MIME assembly is fine — no secrets
    needed to format an email.
    """
    # Fetch thread metadata so we thread correctly.
    thread_result = _call_sync(
        "gmail.get_thread",
        {"account_id": account_id, "thread_id": thread_id},
    )
    if "error" in thread_result:
        return json.dumps(thread_result)
    messages = thread_result.get("messages") or []
    if not messages:
        return json.dumps({"error": "empty_thread"})

    last = messages[-1]
    headers = {h["name"]: h["value"] for h in last.get("payload", {}).get("headers", [])}
    subject = headers.get("Subject", "")
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"

    from email.mime.text import MIMEText

    mime = MIMEText(body)
    mime["To"] = headers.get("From", "")
    mime["Subject"] = subject
    mime["In-Reply-To"] = headers.get("Message-ID", "")
    mime["References"] = headers.get("Message-ID", "")
    raw_bytes = mime.as_bytes()

    result = _call_sync(
        "gmail.create_draft",
        {"account_id": account_id, "thread_id": thread_id, "raw_rfc822": raw_bytes},
    )
    if "error" in result:
        return json.dumps(result)
    return json.dumps({"draft_id": result.get("id")})


def _gmail_add_label(account_id: str, thread_id: str, label: str) -> str:
    # The Security Agent takes a label_id, not a label name. We let the
    # capability resolve the name→id or we require label_ids from the
    # registry-controlled mapping. For v0.1 we pass the name verbatim as
    # label_id; callers that know better can prepopulate.
    result = _call_sync(
        "gmail.add_label",
        {"account_id": account_id, "thread_id": thread_id, "label_id": label},
    )
    if "error" in result:
        return json.dumps(result)
    return json.dumps({"ok": True, "label": label})


# --- per-tool builders (used by skills/src/skills/tools/gmail/* wrappers) ---


def build_gmail_search_tool() -> BaseTool:
    return StructuredTool.from_function(
        func=_gmail_search,
        name="gmail_search",
        description="Search Gmail messages via the Security Agent broker.",
        args_schema=SearchInput,
    )


def build_gmail_get_thread_tool() -> BaseTool:
    return StructuredTool.from_function(
        func=_gmail_get_thread,
        name="gmail_get_thread",
        description="Get the full contents of a Gmail thread by thread_id.",
        args_schema=ThreadInput,
    )


def build_gmail_create_draft_tool() -> BaseTool:
    return StructuredTool.from_function(
        func=_gmail_create_draft,
        name="gmail_create_draft",
        description=(
            "Create a DRAFT reply to a thread. The draft is saved to Gmail's "
            "Drafts folder for Sol to review and send. You cannot send directly — "
            "no gmail_send tool exists in this agent's toolkit."
        ),
        args_schema=DraftInput,
    )


def build_gmail_add_label_tool() -> BaseTool:
    return StructuredTool.from_function(
        func=_gmail_add_label,
        name="gmail_add_label",
        description="Add a label to a Gmail thread.",
        args_schema=LabelInput,
    )


# --- factory (compat — Email PM transitions to skills.registry) ---


def get_gmail_tools() -> list[BaseTool]:
    return [
        build_gmail_search_tool(),
        build_gmail_get_thread_tool(),
        build_gmail_create_draft_tool(),
        build_gmail_add_label_tool(),
    ]
