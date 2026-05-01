"""Prompt-injection scanner for user-supplied markdown.

Borrowed pattern from Hermes's ``agent/prompt_builder.py``: any text that
will be injected into an agent's prompt context (CLAUDE.md, SOUL.md,
USER.md, project AGENTS.md, fetched web content used in-prompt) goes
through this first. Detection is regex + structural — cheap, deterministic,
caught at file-load time.

Three severity levels:
- ``ok``: nothing suspicious; pass through.
- ``warn``: low-confidence markers; redact and proceed (caller decides
  whether to log / surface).
- ``refuse``: high-confidence injection; raise. Caller must not load.

The scanner is conservative: false-positives are preferable to false-
negatives at this layer (the cost of a wrong "refuse" is the operator
re-checks a doc; the cost of a missed injection is everything else).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Tuple


_REFUSE_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("ignore_previous_instructions",
     re.compile(r"(?i)ignore\s+(all\s+)?(previous|prior|above)\s+(instructions|directives|prompt)")),
    ("system_prompt_impersonation",
     re.compile(r"(?i)(</?system>|<\|system\|>|\[SYSTEM\]|^\s*SYSTEM\s*:)", re.MULTILINE)),
    ("tool_call_injection",
     re.compile(r"<\s*(tool_call|function_call|tool_use|invoke)\b", re.IGNORECASE)),
    ("credential_read_request",
     re.compile(
         r"(?i)(show|print|reveal|disclose|tell\s+me)\s+(your|the)\s+"
         r"(api[_\s-]?key|password|secret|token|credential|env|environment)"
     )),
]

_WARN_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("hidden_html_div",
     re.compile(
         r"<\s*div[^>]*style\s*=\s*[\"'][^\"']*display\s*:\s*none[^\"']*[\"']",
         re.IGNORECASE,
     )),
    ("hidden_html_visibility",
     re.compile(
         r"<\s*[^>]+style\s*=\s*[\"'][^\"']*visibility\s*:\s*hidden[^\"']*[\"']",
         re.IGNORECASE,
     )),
    ("suspicious_long_base64",
     # 200+ contiguous base64-shaped chars (no whitespace) — likely a payload.
     re.compile(r"[A-Za-z0-9+/=]{200,}")),
    ("override_keyword",
     re.compile(r"(?i)\b(override|disregard|forget)\s+(?:all\s+)?(?:your\s+)?(instructions|rules|guidelines)")),
]


@dataclass(frozen=True)
class ScanResult:
    severity: str            # "ok" | "warn" | "refuse"
    markers: List[Tuple[str, str]] = field(default_factory=list)
    redacted_text: str = ""


class InjectionRefused(Exception):
    """Raised by safe_load_user_markdown when scanner returns 'refuse'."""

    def __init__(self, path, markers: List[Tuple[str, str]]) -> None:
        super().__init__(
            f"refused to load {path}: prompt-injection markers found "
            f"({', '.join(name for name, _ in markers)})"
        )
        self.path = path
        self.markers = markers


def scan_for_injection(text: str) -> ScanResult:
    """Return a ScanResult with severity, markers, and redacted text.

    Detection order: refuse-patterns first; if any match, severity=refuse
    and the result is returned immediately (no need to keep scanning).
    """
    markers: List[Tuple[str, str]] = []

    for name, pattern in _REFUSE_PATTERNS:
        for m in pattern.finditer(text):
            snippet = m.group(0)[:100]
            markers.append((name, snippet))
    if markers:
        # We still produce a redacted version for telemetry / logs.
        redacted = text
        for name, _ in markers:
            pat = dict(_REFUSE_PATTERNS)[name]
            redacted = pat.sub(f"[REDACTED:{name}]", redacted)
        return ScanResult(severity="refuse", markers=markers, redacted_text=redacted)

    for name, pattern in _WARN_PATTERNS:
        for m in pattern.finditer(text):
            snippet = m.group(0)[:100]
            markers.append((name, snippet))
    if markers:
        redacted = text
        for name, _ in markers:
            pat = dict(_WARN_PATTERNS)[name]
            redacted = pat.sub(f"[REDACTED:{name}]", redacted)
        return ScanResult(severity="warn", markers=markers, redacted_text=redacted)

    return ScanResult(severity="ok", markers=[], redacted_text=text)


_FRONTMATTER_RE = re.compile(r"^---\n.*?\n---\n", re.DOTALL)


def strip_frontmatter(text: str) -> str:
    """Strip a leading YAML frontmatter block. No-op if none."""
    return _FRONTMATTER_RE.sub("", text, count=1)


def safe_load_user_markdown(path: Path) -> str:
    """Load a markdown file, strip frontmatter, scan for injection.

    Returns the (possibly redacted, if severity=warn) text. Raises
    ``InjectionRefused`` on severity=refuse. Raises ``FileNotFoundError``
    if the path doesn't exist.
    """
    text = path.read_text(encoding="utf-8")
    text = strip_frontmatter(text)
    result = scan_for_injection(text)
    if result.severity == "refuse":
        raise InjectionRefused(path, result.markers)
    return result.redacted_text
