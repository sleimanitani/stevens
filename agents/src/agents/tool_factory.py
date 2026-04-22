"""Account-aware tool factory.

LangChain's GmailToolkit is bound to a single credentials object at creation
time. For a multi-account system, we need to produce account-specific tools
on demand.

This module wraps LangChain's tools so that account_id is an explicit
argument — the agent calls `gmail_search(account_id="gmail.atheer", query=...)`
instead of picking from a menu of per-account tools.

Why the wrap and not just N toolkit instances:
  - With 5 accounts and 6 Gmail tools each, binding per-account gives the
    agent 30 tool choices. The wrap gives it 6.
  - Making account_id explicit in the signature forces the prompt to be
    explicit too, which makes "always reply from the account that received
    the message" enforceable.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from shared.accounts import get_account
from shared.db import connection


# ---------------------------------------------------------------------------
# Credential loading
# ---------------------------------------------------------------------------
async def _get_gmail_credentials(account_id: str):
    """Load and refresh Gmail OAuth credentials for an account."""
    from google.oauth2.credentials import Credentials

    async with connection() as conn:
        account = await get_account(conn, account_id)
    if not account:
        raise ValueError(f"unknown account: {account_id}")
    if account.channel_type != "gmail":
        raise ValueError(f"{account_id} is not a Gmail account")

    creds_data = account.credentials
    creds = Credentials.from_authorized_user_info(creds_data)
    # google-auth handles refresh automatically on use, but if you want to
    # persist refreshed tokens back to DB, do it here.
    return creds


def _build_gmail_service(account_id: str):
    """Build a Gmail API client for an account."""
    from googleapiclient.discovery import build
    import asyncio

    # This function is called from sync tool handlers; get creds synchronously.
    loop = asyncio.new_event_loop()
    try:
        creds = loop.run_until_complete(_get_gmail_credentials(account_id))
    finally:
        loop.close()
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Tool implementations (sync for LangGraph compatibility)
# ---------------------------------------------------------------------------
def _gmail_search(account_id: str, query: str) -> str:
    svc = _build_gmail_service(account_id)
    resp = svc.users().messages().list(userId="me", q=query, maxResults=10).execute()
    messages = resp.get("messages", [])
    return json.dumps([{"id": m["id"], "threadId": m["threadId"]} for m in messages])


def _gmail_get_thread(account_id: str, thread_id: str) -> str:
    svc = _build_gmail_service(account_id)
    thread = svc.users().threads().get(userId="me", id=thread_id, format="full").execute()
    # Return a lean representation — full Gmail payloads are huge.
    messages = []
    for msg in thread.get("messages", []):
        headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
        messages.append({
            "id": msg["id"],
            "from": headers.get("From"),
            "to": headers.get("To"),
            "subject": headers.get("Subject"),
            "date": headers.get("Date"),
            "snippet": msg.get("snippet", ""),
        })
    return json.dumps({"thread_id": thread_id, "messages": messages})


def _gmail_create_draft(account_id: str, thread_id: str, body: str) -> str:
    """Create a draft reply in an existing thread.

    v0.1 hard rule: this is the ONLY way agents produce outbound email.
    There is deliberately no send_email tool exposed to agents.
    """
    import base64
    from email.mime.text import MIMEText

    svc = _build_gmail_service(account_id)
    # Fetch thread to get original subject + message-id for proper threading.
    thread = svc.users().threads().get(userId="me", id=thread_id, format="metadata",
                                        metadataHeaders=["Subject", "Message-ID", "From"]).execute()
    messages = thread.get("messages", [])
    if not messages:
        return json.dumps({"error": "thread has no messages"})

    last = messages[-1]
    headers = {h["name"]: h["value"] for h in last["payload"].get("headers", [])}
    subject = headers.get("Subject", "")
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"

    msg = MIMEText(body)
    msg["To"] = headers.get("From", "")
    msg["Subject"] = subject
    msg["In-Reply-To"] = headers.get("Message-ID", "")
    msg["References"] = headers.get("Message-ID", "")

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    draft = svc.users().drafts().create(
        userId="me",
        body={"message": {"raw": raw, "threadId": thread_id}}
    ).execute()
    return json.dumps({"draft_id": draft["id"]})


def _gmail_add_label(account_id: str, thread_id: str, label: str) -> str:
    svc = _build_gmail_service(account_id)
    # Find-or-create the label
    labels = svc.users().labels().list(userId="me").execute().get("labels", [])
    label_id = next((l["id"] for l in labels if l["name"] == label), None)
    if not label_id:
        created = svc.users().labels().create(
            userId="me", body={"name": label, "labelListVisibility": "labelShow"}
        ).execute()
        label_id = created["id"]
    svc.users().threads().modify(
        userId="me", id=thread_id, body={"addLabelIds": [label_id]}
    ).execute()
    return json.dumps({"ok": True, "label": label})


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------
def get_gmail_tools() -> list[BaseTool]:
    """Return the set of Gmail tools, all account-parameterized."""
    return [
        StructuredTool.from_function(
            func=_gmail_search,
            name="gmail_search",
            description="Search Gmail messages. Query uses Gmail search syntax.",
            args_schema=SearchInput,
        ),
        StructuredTool.from_function(
            func=_gmail_get_thread,
            name="gmail_get_thread",
            description="Get the full contents of a Gmail thread by thread_id.",
            args_schema=ThreadInput,
        ),
        StructuredTool.from_function(
            func=_gmail_create_draft,
            name="gmail_create_draft",
            description=(
                "Create a DRAFT reply to a thread. The draft is saved to Gmail's "
                "Drafts folder for Sol to review and send. You cannot send directly."
            ),
            args_schema=DraftInput,
        ),
        StructuredTool.from_function(
            func=_gmail_add_label,
            name="gmail_add_label",
            description="Add a label to a Gmail thread. Creates the label if needed.",
            args_schema=LabelInput,
        ),
    ]
