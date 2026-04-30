"""Tests for skills.retrieval — trigger-match v1."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import pytest
import yaml

from skills.registry import Registry, PlaybookEntry, load_registry
from skills.retrieval import get_playbooks_for


@dataclass
class FakeEvent:
    topic: str
    subject: str = ""
    snippet: str = ""
    body: str = ""


_PB_TEMPLATE = """---
name: {name}
description: test
version: 1.0.0
author: test
license: proprietary
metadata:
  applies_to_topics: {topics}
  applies_to_agents: {agents}
  triggers:
{triggers_yaml}
  status: {status}
---
body
"""


def _write_pb(
    tmp_path: Path,
    *,
    name: str,
    topics: List[str],
    agents: List[str],
    regexes: List[str],
    status: str = "active",
) -> Path:
    triggers_yaml = "\n".join(f"    - regex: \"{r}\"" for r in regexes)
    if not triggers_yaml:
        triggers_yaml = "    []"
    body = _PB_TEMPLATE.format(
        name=name,
        topics=topics,
        agents=agents,
        triggers_yaml=triggers_yaml,
        status=status,
    )
    p = tmp_path / f"{name}.md"
    p.write_text(body)
    return p


def _build_registry(tmp_path: Path, entries: List[PlaybookEntry]) -> Registry:
    reg_yaml = tmp_path / "registry.yaml"
    yml = {
        "tools": [],
        "playbooks": [
            {
                "id": e.id,
                "path": str(e.path),
                "applies_to_topics": e.applies_to_topics,
                "applies_to_agents": e.applies_to_agents,
            }
            for e in entries
        ],
    }
    reg_yaml.write_text(yaml.safe_dump(yml))
    return load_registry(reg_yaml, root=tmp_path)


def test_agent_scope_filter(tmp_path: Path) -> None:
    p = _write_pb(
        tmp_path, name="email_only", topics=["email.received.*"],
        agents=["email_pm"], regexes=["meeting"],
    )
    reg = _build_registry(
        tmp_path,
        [PlaybookEntry(
            id="email/email_only", path=p,
            applies_to_topics=["email.received.*"],
            applies_to_agents=["email_pm"],
        )],
    )
    ev = FakeEvent(topic="email.received.gmail.personal", subject="meeting today")
    assert len(get_playbooks_for("email_pm", ev, registry=reg)) == 1
    assert get_playbooks_for("berwyn_deal", ev, registry=reg) == []


def test_topic_pattern_filter(tmp_path: Path) -> None:
    p = _write_pb(
        tmp_path, name="x", topics=["email.received.*"],
        agents=[], regexes=["foo"],
    )
    reg = _build_registry(
        tmp_path,
        [PlaybookEntry(
            id="x/x", path=p,
            applies_to_topics=["email.received.*"],
            applies_to_agents=[],
        )],
    )
    ev_match = FakeEvent(topic="email.received.gmail.x", subject="foo")
    ev_miss = FakeEvent(topic="whatsapp.message.received", subject="foo")
    assert len(get_playbooks_for("any", ev_match, registry=reg)) == 1
    assert get_playbooks_for("any", ev_miss, registry=reg) == []


def test_trigger_must_match(tmp_path: Path) -> None:
    p = _write_pb(
        tmp_path, name="meeting", topics=["email.received.*"],
        agents=[], regexes=[r"(?i)meeting|schedule"],
    )
    reg = _build_registry(
        tmp_path,
        [PlaybookEntry(
            id="x/x", path=p,
            applies_to_topics=["email.received.*"],
            applies_to_agents=[],
        )],
    )
    ev_match = FakeEvent(topic="email.received.x", subject="want to schedule a call")
    ev_miss = FakeEvent(topic="email.received.x", subject="invoice attached")
    assert len(get_playbooks_for("any", ev_match, registry=reg)) == 1
    assert get_playbooks_for("any", ev_miss, registry=reg) == []


def test_deprecated_playbook_excluded(tmp_path: Path) -> None:
    p = _write_pb(
        tmp_path, name="old", topics=[], agents=[],
        regexes=[".*"], status="deprecated",
    )
    reg = _build_registry(
        tmp_path,
        [PlaybookEntry(
            id="x/old", path=p, applies_to_topics=[], applies_to_agents=[],
        )],
    )
    ev = FakeEvent(topic="email.received.x", subject="anything")
    assert get_playbooks_for("any", ev, registry=reg) == []


def test_max_playbooks_cap(tmp_path: Path) -> None:
    entries = []
    for i in range(5):
        p = _write_pb(
            tmp_path, name=f"pb{i}", topics=[],
            agents=[], regexes=["match"],
        )
        entries.append(PlaybookEntry(
            id=f"x/pb{i}", path=p, applies_to_topics=[], applies_to_agents=[],
        ))
    reg = _build_registry(tmp_path, entries)
    ev = FakeEvent(topic="email.x", subject="match")
    assert len(get_playbooks_for("any", ev, max_playbooks=2, registry=reg)) == 2
    # Default cap is 3.
    assert len(get_playbooks_for("any", ev, registry=reg)) == 3


def test_specificity_ranks_longer_regex_first(tmp_path: Path) -> None:
    short = _write_pb(
        tmp_path, name="short", topics=[], agents=[], regexes=["call"],
    )
    long = _write_pb(
        tmp_path, name="long", topics=[], agents=[],
        regexes=[r"(?i)(call|meeting|schedule|appointment|calendly)"],
    )
    reg = _build_registry(
        tmp_path,
        [
            PlaybookEntry(
                id="x/short", path=short,
                applies_to_topics=[], applies_to_agents=[],
            ),
            PlaybookEntry(
                id="x/long", path=long,
                applies_to_topics=[], applies_to_agents=[],
            ),
        ],
    )
    ev = FakeEvent(topic="x", subject="want to schedule a call")
    out = get_playbooks_for("any", ev, registry=reg)
    assert len(out) == 2
    assert out[0].name == "long"


def test_env_var_max(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    entries = []
    for i in range(4):
        p = _write_pb(
            tmp_path, name=f"pb{i}", topics=[], agents=[], regexes=["match"],
        )
        entries.append(PlaybookEntry(
            id=f"x/pb{i}", path=p, applies_to_topics=[], applies_to_agents=[],
        ))
    reg = _build_registry(tmp_path, entries)
    ev = FakeEvent(topic="x", subject="match")
    monkeypatch.setenv("STEVENS_MAX_PLAYBOOKS", "1")
    assert len(get_playbooks_for("any", ev, registry=reg)) == 1
