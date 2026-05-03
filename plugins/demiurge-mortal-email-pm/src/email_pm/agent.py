"""Email PM agent.

Exposes a single entry point, handle(event, config), called by the runtime
for each matching bus event.

Architecture:
  - Model: local Qwen3-30B via Ollama (langchain_ollama.ChatOllama).
  - Tools: resolved via ``skills.registry.get_tools_for_agent("email_pm")``.
  - Playbooks: resolved per-event via
    ``skills.registry.get_playbooks_for("email_pm", event)`` — situation-
    specific procedures injected into the prompt at runtime.
  - Graph: LangGraph's built-in ReAct agent (create_react_agent).
  - Memory: LangGraph Postgres checkpointer, keyed on account_id+thread_id
            so each thread has its own conversation state.

Note: the ReAct agent is rebuilt per-event so freshly-matched playbooks can
be injected into ``state_modifier``. Construction is cheap (the underlying
ChatOllama and tool list are reused via module-level caches).
"""

from __future__ import annotations

import logging
import os
from typing import Any, List

from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool
from langchain_ollama import ChatOllama
from langgraph.prebuilt import create_react_agent

from shared.events import BaseEvent, EmailReceivedEvent
from skills.playbooks.loader import Playbook
from skills.registry import get_playbooks_for, get_tools_for_agent

from .prompts import SYSTEM_PROMPT


log = logging.getLogger(__name__)


_MODEL = None
_TOOLS: List[BaseTool] | None = None


def _model():
    global _MODEL
    if _MODEL is None:
        _MODEL = ChatOllama(
            base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
            model=os.environ.get("OLLAMA_MODEL", "qwen3:30b-a3b-instruct"),
            temperature=0.2,
        )
    return _MODEL


def _tools() -> List[BaseTool]:
    global _TOOLS
    if _TOOLS is None:
        _TOOLS = get_tools_for_agent(
            "email_pm",
            excludes=["security.*"],
            safety_max="read-write",
        )
    return _TOOLS


def _render_playbooks_block(playbooks: List[Playbook]) -> str:
    """Format a set of matched playbooks as a single prompt block.

    Each playbook contributes its body verbatim under a heading. Cap is
    enforced by retrieval; here we just render what we got.
    """
    if not playbooks:
        return ""
    parts = ["# Situation-specific playbooks (matched for this event)"]
    for pb in playbooks:
        parts.append(f"\n## {pb.name}")
        parts.append(f"_{pb.description}_")
        parts.append(pb.body.strip())
    return "\n".join(parts)


def _build_state_modifier(playbooks: List[Playbook]) -> str:
    block = _render_playbooks_block(playbooks)
    if not block:
        return SYSTEM_PROMPT
    return f"{SYSTEM_PROMPT}\n\n---\n\n{block}"


def _event_to_prompt(event: EmailReceivedEvent) -> str:
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
    """Entry point called by the runtime for each email.received.* event."""
    if not isinstance(event, EmailReceivedEvent):
        log.warning("email_pm got non-email event: %s", type(event).__name__)
        return

    log.info(
        "email_pm handling account=%s thread=%s from=%s",
        event.account_id, event.thread_id, event.from_,
    )

    matched = get_playbooks_for("email_pm", event)
    log.info(
        "email_pm matched %d playbook(s): %s",
        len(matched), [p.name for p in matched],
    )

    agent = create_react_agent(
        _model(),
        _tools(),
        state_modifier=_build_state_modifier(matched),
    )

    prompt = _event_to_prompt(event)
    result = await agent.ainvoke(
        {"messages": [HumanMessage(content=prompt)]},
        config={
            "configurable": {
                "thread_id": f"{event.account_id}::{event.thread_id}",
            },
            "recursion_limit": 20,
        },
    )

    final = result["messages"][-1].content if result.get("messages") else ""
    log.info("email_pm done thread=%s final=%s", event.thread_id, str(final)[:200])
