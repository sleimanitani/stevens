"""Playbook loader — Markdown + YAML frontmatter parser.

Frontmatter follows the agentskills.io standard for top-level fields, with
our extensions under ``metadata``::

    ---
    name: email-appointment-request
    description: Triage incoming meeting/call requests on email
    version: 1.0.0
    author: email_pm
    license: proprietary
    metadata:
      applies_to_topics: [email.received.*]
      applies_to_agents: [email_pm]
      triggers:
        - regex: "(?i)(meeting|call|schedule|available|calendly)"
      status: active                # proposed | active | deprecated
      supersedes: null
    ---

    ## When to apply
    ...

The loader is strict on shape: missing required top-level fields, malformed
yaml, invalid regex, or unknown ``status`` all raise :class:`PlaybookError`.
Fail-closed — a half-formed playbook in the search path could silently change
agent behavior.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Pattern

import yaml


class PlaybookError(Exception):
    """Raised on malformed playbook files."""


_VALID_STATUSES = {"proposed", "active", "deprecated"}
_REQUIRED_TOP_LEVEL = ("name", "description", "version", "author", "license")
_FRONTMATTER_RE = re.compile(
    r"^---\n(.*?)\n---\n(.*)$", re.DOTALL
)


@dataclass(frozen=True)
class Trigger:
    regex: Pattern[str]


@dataclass(frozen=True)
class Playbook:
    """One playbook, parsed and validated."""

    name: str
    description: str
    version: str
    author: str
    license: str
    body: str
    # extension fields, all optional with sensible defaults
    applies_to_topics: List[str] = field(default_factory=list)
    applies_to_agents: List[str] = field(default_factory=list)
    triggers: List[Trigger] = field(default_factory=list)
    status: str = "proposed"
    supersedes: Optional[str] = None
    path: Optional[Path] = None

    @property
    def is_active(self) -> bool:
        return self.status == "active"


def _parse_triggers(raw: Any, ctx: str) -> List[Trigger]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise PlaybookError(f"{ctx}: 'triggers' must be a list, got {type(raw).__name__}")
    out: List[Trigger] = []
    for entry in raw:
        if not isinstance(entry, dict) or "regex" not in entry:
            raise PlaybookError(f"{ctx}: each trigger needs a 'regex' field; got {entry!r}")
        pat = entry["regex"]
        if not isinstance(pat, str):
            raise PlaybookError(f"{ctx}: trigger regex must be a string, got {type(pat).__name__}")
        try:
            compiled = re.compile(pat)
        except re.error as e:
            raise PlaybookError(f"{ctx}: invalid trigger regex {pat!r}: {e}") from e
        out.append(Trigger(regex=compiled))
    return out


def _split_frontmatter(text: str, path: Path) -> tuple[Dict[str, Any], str]:
    m = _FRONTMATTER_RE.match(text)
    if m is None:
        raise PlaybookError(f"{path}: no YAML frontmatter found (must start with `---`)")
    try:
        front = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        raise PlaybookError(f"{path}: invalid yaml in frontmatter: {e}") from e
    if not isinstance(front, dict):
        raise PlaybookError(f"{path}: frontmatter must be a mapping")
    return front, m.group(2)


def load_playbook(path: Path) -> Playbook:
    """Parse and validate a single playbook file."""
    text = path.read_text(encoding="utf-8")
    front, body = _split_frontmatter(text, path)

    # Required top-level (agentskills.io shape).
    for k in _REQUIRED_TOP_LEVEL:
        if k not in front:
            raise PlaybookError(f"{path}: missing required top-level field {k!r}")
        if not isinstance(front[k], (str, int, float)):
            raise PlaybookError(
                f"{path}: top-level {k!r} must be a scalar, got {type(front[k]).__name__}"
            )

    # Stringify version specifically (yaml may parse "1.0" as float).
    version = str(front["version"])

    metadata = front.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise PlaybookError(f"{path}: 'metadata' must be a mapping")

    applies_to_topics = metadata.get("applies_to_topics") or []
    if not isinstance(applies_to_topics, list) or not all(
        isinstance(x, str) for x in applies_to_topics
    ):
        raise PlaybookError(f"{path}: metadata.applies_to_topics must be a list of strings")

    applies_to_agents = metadata.get("applies_to_agents") or []
    if not isinstance(applies_to_agents, list) or not all(
        isinstance(x, str) for x in applies_to_agents
    ):
        raise PlaybookError(f"{path}: metadata.applies_to_agents must be a list of strings")

    triggers = _parse_triggers(metadata.get("triggers"), str(path))

    status = metadata.get("status", "proposed")
    if status not in _VALID_STATUSES:
        raise PlaybookError(
            f"{path}: status must be one of {sorted(_VALID_STATUSES)}, got {status!r}"
        )

    supersedes = metadata.get("supersedes")
    if supersedes is not None and not isinstance(supersedes, str):
        raise PlaybookError(f"{path}: metadata.supersedes must be a string or null")

    return Playbook(
        name=str(front["name"]),
        description=str(front["description"]),
        version=version,
        author=str(front["author"]),
        license=str(front["license"]),
        body=body,
        applies_to_topics=list(applies_to_topics),
        applies_to_agents=list(applies_to_agents),
        triggers=triggers,
        status=status,
        supersedes=supersedes,
        path=path,
    )


def load_all(directory: Path) -> List[Playbook]:
    """Recursively load every ``*.md`` file under ``directory``."""
    if not directory.exists():
        return []
    out: List[Playbook] = []
    for p in sorted(directory.rglob("*.md")):
        out.append(load_playbook(p))
    return out
