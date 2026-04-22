"""Email PM agent.

Exposes a single entry point, handle(event, config), called by the runtime
for each matching bus event.

Architecture:
  - Model: local Qwen3-30B via Ollama (langchain_ollama.ChatOllama).
  - Tools: Gmail (via tool_factory) + email PM–specific tools.
  - Graph: LangGraph's built-in ReAct agent (create_react_agent).
  - Memory: LangGraph Postgres checkpointer, keyed on account_id+thread_id
            so each thread has its own conversation state.

v0.1 non-goals (deliberate):
  - No cross-thread memory beyond followups table. Each thread is stateless.
  - No escalation to remote models. Local only.
  - No Langfuse setup here — done at runtime level (env-var based).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from langchain_core.messages import HumanMessage
from langchain_ollama import ChatOllama
from langgraph.prebuilt import create_react_agent

from shared.events import BaseEvent, EmailReceivedEvent

from ..tool_factory import get_gmail_tools
from .prompts import SYSTEM_PROMPT
from .tools import get_email_pm_tools


log = logging.getLogger(__name__)


def _build_agent():
    """Construct the LangGraph ReAct agent.

    Built once at module load, reused across events. The underlying
    ChatOllama client is thread-safe for the request volumes we expect.
    """
    model = ChatOllama(
        base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
        model=os.environ.get("OLLAMA_MODEL", "qwen3:30b-a3b-instruct"),
        temperature=0.2,
    )
    tools = [*get_gmail_tools(), *get_email_pm_tools()]
    return create_react_agent(model, tools, state_modifier=SYSTEM_PROMPT)


_AGENT = None


def _agent():
    global _AGENT
    if _AGENT is None:
        _AGENT = _build_agent()
    return _AGENT


def _event_to_prompt(event: EmailReceivedEvent) -> str:
    """Render the event into a prompt for the agent."""
    return f"""A new email arrived on account {event.account_id}.

From: {event.from_}
To: {', '.join(event.to)}
Subject: {event.subject}
Thread ID: {event.thread_id}
Snippet: {event.snippet}

Triage this thread. Call gmail_get_thread with account_id={event.account_id!r} and
thread_id={event.thread_id!r} to see the full context before deciding.
"""


async def handle(event: BaseEvent, config: dict[str, Any]) -> None:
    """Entry point called by the runtime for each email.received.* event.

    The runtime handles account filtering before this is called, so we can
    assume the event is one we should process.
    """
    if not isinstance(event, EmailReceivedEvent):
        log.warning("email_pm got non-email event: %s", type(event).__name__)
        return

    log.info(
        "email_pm handling account=%s thread=%s from=%s",
        event.account_id, event.thread_id, event.from_,
    )

    prompt = _event_to_prompt(event)
    agent = _agent()

    # LangGraph's ainvoke is async; run it.
    result = await agent.ainvoke(
        {"messages": [HumanMessage(content=prompt)]},
        config={
            "configurable": {
                "thread_id": f"{event.account_id}::{event.thread_id}",
            },
            "recursion_limit": 20,
        },
    )

    # For now, just log the final message. Observability via Langfuse
    # (set LANGFUSE_* env vars and LangGraph auto-traces).
    final = result["messages"][-1].content if result.get("messages") else ""
    log.info("email_pm done thread=%s final=%s", event.thread_id, str(final)[:200])
