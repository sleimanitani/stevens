"""Event schemas.

Every event on the bus is a Pydantic model defined here. Adding a channel
means adding a schema here FIRST, then implementing the adapter.

Topic convention: <channel>.<event_type>.<account_id>
Example: email.received.gmail.personal, whatsapp.message.received.wa.us

Rules:
- Never remove a field. Add only.
- Agents must tolerate unknown fields (use `model_config = {"extra": "allow"}`
  on consumers, never on the canonical event models here).
- account_id is always both in the topic AND in the payload. Never rely on
  topic parsing alone.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class BaseEvent(BaseModel):
    """All events share these fields."""

    event_id: UUID = Field(default_factory=uuid4)
    ts: datetime = Field(default_factory=lambda: datetime.utcnow())
    source: str
    account_id: str

    @property
    def topic(self) -> str:
        """Subclasses override this to return the canonical topic string."""
        raise NotImplementedError


class EmailReceivedEvent(BaseEvent):
    source: Literal["gmail"] = "gmail"
    message_id: str
    thread_id: str
    from_: str = Field(alias="from")  # 'from' is a Python keyword
    to: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    subject: str = ""
    body_text: str = ""
    body_html: str = ""
    snippet: str = ""
    labels: list[str] = Field(default_factory=list)
    in_reply_to: Optional[str] = None
    raw_ref: str

    model_config = {"populate_by_name": True}

    @property
    def topic(self) -> str:
        return f"email.received.{self.account_id}"


class WhatsAppMessageEvent(BaseEvent):
    source: Literal["whatsapp"] = "whatsapp"
    msg_id: str
    chat_id: str
    from_jid: str
    from_push_name: Optional[str] = None
    is_group: bool = False
    group_id: Optional[str] = None
    text: str = ""
    media_ref: Optional[str] = None
    quoted_msg_id: Optional[str] = None
    raw_ref: str

    @property
    def topic(self) -> str:
        return f"whatsapp.message.received.{self.account_id}"


class CalendarEventChangedEvent(BaseEvent):
    """Emitted when a Google Calendar push arrives and we sync in changes.

    One of these per changed event. When Google just says "sync" with no
    specific event change, the adapter walks ``events.list`` with the
    last ``syncToken`` and emits one of these per result.
    """

    source: Literal["calendar"] = "calendar"
    calendar_id: str
    gcal_event_id: str
    status: str = "confirmed"  # "confirmed" | "tentative" | "cancelled"
    summary: str = ""
    start: Optional[str] = None  # ISO 8601, or all-day YYYY-MM-DD
    end: Optional[str] = None
    organizer: Optional[str] = None
    attendees: list[str] = Field(default_factory=list)
    html_link: Optional[str] = None
    raw_ref: str

    @property
    def topic(self) -> str:
        return f"calendar.event.changed.{self.account_id}"


class SystemDepRequestedEvent(BaseEvent):
    """The operator (or another agent) asked the installer to ensure a dep
    is installed. The installer subscribes; nothing else should publish to
    this topic without good reason."""

    source: Literal["operator"] = "operator"
    package: str
    mechanism: str = "apt"
    rationale: Optional[str] = None
    # account_id is unused for this event family; we set "system" to satisfy
    # the BaseEvent contract since dep events are host-scoped, not account-scoped.

    @property
    def topic(self) -> str:
        return f"system.dep.requested.{self.package}"


class SystemDepInstalledEvent(BaseEvent):
    source: Literal["installer"] = "installer"
    package: str
    plan_id: Optional[str] = None
    inventory_id: Optional[str] = None
    reason: Optional[str] = None      # e.g. "already_installed"
    version: Optional[str] = None

    @property
    def topic(self) -> str:
        return f"system.dep.installed.{self.package}"


class SystemDepAwaitingApprovalEvent(BaseEvent):
    source: Literal["installer"] = "installer"
    package: str
    plan_id: str
    approval_request_id: str
    rationale: Optional[str] = None

    @property
    def topic(self) -> str:
        return f"system.dep.awaiting_approval.{self.package}"


class SystemDepFailedEvent(BaseEvent):
    source: Literal["installer"] = "installer"
    package: str
    reason: str
    detail: Optional[str] = None

    @property
    def topic(self) -> str:
        return f"system.dep.failed.{self.package}"


class WebFetchRequestedEvent(BaseEvent):
    """Async-path request: please fetch this URL on my behalf.

    Arachne (the web agent) subscribes to ``web.fetch.requested.*`` and
    publishes a paired ``WebFetchResponseEvent`` to
    ``web.fetch.response.<request_id>`` when the fetch completes.
    """

    source: Literal["agent"] = "agent"
    request_id: str
    url: str
    follow_redirects: bool = True
    requested_by: str   # the requesting agent's name; for audit + debug

    @property
    def topic(self) -> str:
        return f"web.fetch.requested.{self.request_id}"


class WebFetchResponseEvent(BaseEvent):
    source: Literal["arachne"] = "arachne"
    request_id: str
    status: Optional[int] = None
    body_text: Optional[str] = None
    body_b64: Optional[str] = None
    final_url: Optional[str] = None
    truncated: bool = False
    cache_hit: bool = False
    error: Optional[str] = None
    error_detail: Optional[str] = None

    @property
    def topic(self) -> str:
        return f"web.fetch.response.{self.request_id}"


class WebSearchRequestedEvent(BaseEvent):
    source: Literal["agent"] = "agent"
    request_id: str
    query: str
    max_results: int = 10
    requested_by: str

    @property
    def topic(self) -> str:
        return f"web.search.requested.{self.request_id}"


class WebSearchResponseEvent(BaseEvent):
    source: Literal["arachne"] = "arachne"
    request_id: str
    backend: Optional[str] = None
    results: list[dict] = Field(default_factory=list)
    cache_hit: bool = False
    error: Optional[str] = None
    error_detail: Optional[str] = None

    @property
    def topic(self) -> str:
        return f"web.search.response.{self.request_id}"


class PDFParseRequestedEvent(BaseEvent):
    """Async-path PDF parse request.

    Sphinx subscribes to ``pdf.parse.requested.*`` and publishes a paired
    ``PDFParseResponseEvent`` to ``pdf.parse.response.<request_id>``.
    """

    source: Literal["agent"] = "agent"
    request_id: str
    path: str
    requested_by: str
    mode: str = "both"
    prefer_strategy: Optional[str] = None
    request_hint: Optional[str] = None

    @property
    def topic(self) -> str:
        return f"pdf.parse.requested.{self.request_id}"


class PDFParseResponseEvent(BaseEvent):
    source: Literal["sphinx"] = "sphinx"
    request_id: str
    text: Optional[str] = None
    tables: list = Field(default_factory=list)
    pages: int = 0
    used_ocr: bool = False
    strategy_used: Optional[str] = None
    decision_reason: Optional[str] = None
    warnings: list = Field(default_factory=list)
    error: Optional[str] = None
    error_detail: Optional[str] = None

    @property
    def topic(self) -> str:
        return f"pdf.parse.response.{self.request_id}"


class ApprovalRequestedEvent(BaseEvent):
    """Per-call approval enqueued by Enkidu — published per matching forwarding target.

    A future "approval forwarder" agent subscribes to ``approval.requested.*``
    and routes the human-readable summary to the target chat channel
    (Gmail draft, WhatsApp message, etc.) so Sol can decide without
    sitting at the CLI. v0.3.3 only emits the event; the forwarder is a
    later milestone.
    """

    source: Literal["enkidu"] = "enkidu"
    request_id: str
    capability: str
    caller: str
    params_summary: str
    rationale: Optional[str] = None
    target_channel: str         # e.g. "gmail" / "whatsapp_cloud" / "session"
    target_account_id: str
    target_thread_id: Optional[str] = None

    @property
    def topic(self) -> str:
        return f"approval.requested.{self.request_id}"


# Registry of all known event types, keyed by topic pattern prefix.
# Used by the bus to deserialize events into the right Pydantic model.
EVENT_REGISTRY: dict[str, type[BaseEvent]] = {
    "email.received": EmailReceivedEvent,
    "whatsapp.message.received": WhatsAppMessageEvent,
    "calendar.event.changed": CalendarEventChangedEvent,
    "system.dep.requested": SystemDepRequestedEvent,
    "system.dep.installed": SystemDepInstalledEvent,
    "system.dep.awaiting_approval": SystemDepAwaitingApprovalEvent,
    "system.dep.failed": SystemDepFailedEvent,
    "web.fetch.requested": WebFetchRequestedEvent,
    "web.fetch.response": WebFetchResponseEvent,
    "web.search.requested": WebSearchRequestedEvent,
    "web.search.response": WebSearchResponseEvent,
    "approval.requested": ApprovalRequestedEvent,
    "pdf.parse.requested": PDFParseRequestedEvent,
    "pdf.parse.response": PDFParseResponseEvent,
}


def parse_event(topic: str, payload: dict) -> BaseEvent:
    """Deserialize a raw payload into the correct event subclass based on topic."""
    for prefix, cls in EVENT_REGISTRY.items():
        if topic.startswith(prefix):
            return cls.model_validate(payload)
    raise ValueError(f"No event class registered for topic: {topic}")
