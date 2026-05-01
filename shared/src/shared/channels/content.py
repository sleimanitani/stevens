"""Content payloads — what an adapter is being asked to send.

A content payload is one of seven kinds. Adapters declare which kinds
they support via ``AdapterCapabilities``; the framework's ``synthesize``
function picks the closest supported one when an unsupported kind is
requested.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union


class ContentKind(str, enum.Enum):
    PLAIN_TEXT = "plain_text"
    MARKDOWN = "markdown"
    CHUNKED = "chunked"           # one logical message that may span multiple sends
    BLOCK = "block"               # rich UI (Slack blocks, Discord embeds, etc.)
    APPROVAL_PROMPT = "approval_prompt"
    TYPING_START = "typing_start"
    TYPING_STOP = "typing_stop"


@dataclass(frozen=True)
class PlainText:
    kind: ContentKind = ContentKind.PLAIN_TEXT
    text: str = ""


@dataclass(frozen=True)
class Markdown:
    kind: ContentKind = ContentKind.MARKDOWN
    text: str = ""


@dataclass(frozen=True)
class Chunked:
    kind: ContentKind = ContentKind.CHUNKED
    text: str = ""    # the full text; the adapter / synthesis decides how to split


@dataclass(frozen=True)
class Block:
    kind: ContentKind = ContentKind.BLOCK
    blocks: List[Dict[str, Any]] = field(default_factory=list)
    fallback_markdown: str = ""    # used when synthesizing down to markdown / plain_text


@dataclass(frozen=True)
class ApprovalPrompt:
    kind: ContentKind = ContentKind.APPROVAL_PROMPT
    request_id: str = ""
    summary: str = ""              # human-readable "this is what's being asked"
    rationale: Optional[str] = None
    approve_command: str = "/approve"
    reject_command: str = "/reject"


@dataclass(frozen=True)
class TypingStart:
    kind: ContentKind = ContentKind.TYPING_START


@dataclass(frozen=True)
class TypingStop:
    kind: ContentKind = ContentKind.TYPING_STOP


Content = Union[
    PlainText, Markdown, Chunked, Block, ApprovalPrompt, TypingStart, TypingStop,
]
