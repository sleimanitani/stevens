"""Email PM is wired through the skills registry — end-to-end integration check.

These tests don't run the LLM or hit Gmail. They verify:
  1. ``get_tools_for_agent('email_pm')`` returns tools from the registry,
     including the email_pm-restricted ``followups.log_followup``.
  2. ``get_playbooks_for('email_pm', event)`` matches at least one
     starter playbook for a triggering event.
  3. The Email PM agent module's ``_render_playbooks_block`` injects the
     matched playbook body into the system prompt.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

import pytest

from skills.registry import get_tools_for_agent
from skills.retrieval import get_playbooks_for


@dataclass
class FakeEmailEvent:
    topic: str
    subject: str = ""
    snippet: str = ""
    body: str = ""


def test_email_pm_sees_gmail_and_followups_tools() -> None:
    tools = get_tools_for_agent("email_pm", excludes=["security.*"])
    names = {t.name for t in tools}
    assert "gmail_search" in names
    assert "gmail_get_thread" in names
    assert "gmail_create_draft" in names
    assert "gmail_add_label" in names
    assert "log_followup" in names  # restricted to email_pm
    # PDF reader is shared → also visible
    assert "read_pdf" in names


def test_other_agent_does_not_see_restricted_followup_tool() -> None:
    tools = get_tools_for_agent("interface")
    names = {t.name for t in tools}
    assert "log_followup" not in names
    # Shared tools still visible.
    assert "read_pdf" in names


def test_appointment_request_playbook_matches_meeting_email() -> None:
    ev = FakeEmailEvent(
        topic="email.received.gmail.personal",
        subject="Can we schedule a call this week?",
        snippet="Want to find a time that works for both of us.",
    )
    matched = get_playbooks_for("email_pm", ev)
    names = [p.name for p in matched]
    assert "email-appointment-request" in names


def test_marketing_filter_matches_unsubscribe_email() -> None:
    ev = FakeEmailEvent(
        topic="email.received.gmail.personal",
        subject="Limited time exclusive deal!",
        snippet="Click to unsubscribe at the bottom of this email.",
    )
    matched = get_playbooks_for("email_pm", ev)
    names = [p.name for p in matched]
    assert "email-marketing-filter" in names


def test_sensitive_escalation_overrides_for_legal_email() -> None:
    ev = FakeEmailEvent(
        topic="email.received.gmail.personal",
        subject="Subpoena re: case 12345",
        snippet="Legal matter requiring response by Friday.",
    )
    matched = get_playbooks_for("email_pm", ev)
    names = [p.name for p in matched]
    assert "email-sensitive-escalation" in names


def test_render_playbooks_block_injects_into_system_prompt() -> None:
    from agents.email_pm.agent import _build_state_modifier
    from skills.playbooks.loader import load_playbook
    from pathlib import Path

    pb = load_playbook(
        Path(__file__).resolve().parents[2]
        / "skills"
        / "src"
        / "skills"
        / "playbooks"
        / "email"
        / "appointment_request.md"
    )
    rendered = _build_state_modifier([pb])
    assert "email-appointment-request" in rendered
    assert "Calendly" in rendered  # body content from the playbook


def test_no_match_returns_just_system_prompt() -> None:
    """Email PM with no matching playbook still gets the base system prompt."""
    from agents.email_pm.agent import _build_state_modifier, SYSTEM_PROMPT

    rendered = _build_state_modifier([])
    assert rendered == SYSTEM_PROMPT
