"""Playbook retrieval — v1 trigger-match.

Public signature is fixed so a v2 (pgvector / cheap-classifier) drops in by
replacing the body. Callers go through ``skills.registry.get_playbooks_for``
which delegates here.

Match conditions (ALL must hold):

1. Agent gate: ``agent_name in playbook.applies_to_agents`` OR the list is
   empty/missing → applies to any agent.
2. Topic gate: ``event.topic`` matches at least one of
   ``applies_to_topics`` (fnmatch glob — ``email.received.*`` matches
   ``email.received.gmail.personal``).
3. Trigger gate: at least one ``triggers[].regex`` matches the event's
   content (subject + snippet + body, in that priority order).
4. Active: ``status == "active"``.

Returns up to ``max_playbooks`` playbooks, ranked by trigger specificity
(more specific patterns — longer regex — first). v2 will replace
specificity ranking with a real relevance score.

Cap defaults to 3 (lower than the spec's 5) to leave headroom in
Qwen3-30B's context window. Override via ``$DEMIURGE_MAX_PLAYBOOKS``.
"""

from __future__ import annotations

import fnmatch
import os
from typing import Any, List, Optional

from .playbooks.loader import Playbook, load_playbook


def _event_topic(event: Any) -> Optional[str]:
    """Best-effort extraction of a topic string from any event-shaped object."""
    for attr in ("topic", "_topic"):
        v = getattr(event, attr, None)
        if isinstance(v, str):
            return v
    if isinstance(event, dict):
        v = event.get("topic")
        if isinstance(v, str):
            return v
    return None


def _event_text(event: Any) -> str:
    """Concatenate the searchable fields of an event into one string."""
    parts: List[str] = []
    for attr in ("subject", "snippet", "body", "text", "content"):
        v = getattr(event, attr, None)
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(event, dict) and isinstance(event.get(attr), str):
            parts.append(event[attr])
    return "\n".join(parts)


def _topic_matches(event_topic: Optional[str], patterns: List[str]) -> bool:
    if not patterns:
        # An empty applies_to_topics is treated as "applies to any topic" —
        # a permissive default. Authors who want strict scoping must list at
        # least one pattern.
        return True
    if event_topic is None:
        return False
    return any(fnmatch.fnmatchcase(event_topic, p) for p in patterns)


def _agent_matches(agent_name: str, agents: List[str]) -> bool:
    if not agents:
        return True
    return agent_name in agents


def _max_from_env(default: int) -> int:
    raw = os.environ.get("DEMIURGE_MAX_PLAYBOOKS")
    if raw is None:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def get_playbooks_for(
    agent_name: str,
    event: Any,
    *,
    max_playbooks: Optional[int] = None,
    registry=None,
) -> List[Playbook]:
    """Return matching active playbooks, capped, ranked by trigger specificity."""
    from .registry import load_registry

    reg = registry if registry is not None else load_registry()
    cap = max_playbooks if max_playbooks is not None else _max_from_env(3)

    topic = _event_topic(event)
    text = _event_text(event)

    matches: List[tuple[int, Playbook]] = []
    for entry in reg.playbooks:
        # Quick pre-checks at the entry level (cheap).
        if not _agent_matches(agent_name, entry.applies_to_agents):
            continue
        if not _topic_matches(topic, entry.applies_to_topics):
            continue
        # Load the file to consult triggers + status. Could cache later.
        if not entry.path.exists():
            continue
        pb = load_playbook(entry.path)
        if not pb.is_active:
            continue
        # Specificity: length of the longest matching regex pattern source.
        best = -1
        for trig in pb.triggers:
            if trig.regex.search(text):
                best = max(best, len(trig.regex.pattern))
        if best < 0:
            # No trigger matched — this playbook does not apply.
            continue
        matches.append((best, pb))

    matches.sort(key=lambda pair: -pair[0])  # higher specificity first
    return [pb for _, pb in matches[:cap]]
