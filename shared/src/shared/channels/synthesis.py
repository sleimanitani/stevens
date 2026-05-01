"""Synthesis — downgrade an unsupported content kind to the closest supported one.

Fallback chains:
- block → markdown → plain_text
- markdown → plain_text (best-effort: leave markdown punctuation in place)
- chunked → plain_text (one piece) OR list of plain_texts when caller iterates
- approval_prompt with rich support → block; otherwise → markdown / plain_text
  with a structured /approve <id> line
- typing_start / typing_stop → no-op if unsupported
"""

from __future__ import annotations

from typing import List, Sequence

from .capabilities import AdapterCapabilities
from .content import (
    ApprovalPrompt,
    Block,
    Chunked,
    Content,
    ContentKind,
    Markdown,
    PlainText,
    TypingStart,
    TypingStop,
)


def synthesize(content: Content, capabilities: AdapterCapabilities) -> Content:
    """Return the closest-supported version of ``content``.

    For kinds that the adapter can drop entirely (typing indicators), returns
    a TypingStop with the original kind preserved on extra — caller checks
    ``capabilities.supports`` to decide whether to actually send.
    """
    if capabilities.supports(content.kind):
        return content

    if isinstance(content, Block):
        if capabilities.supports(ContentKind.MARKDOWN):
            return Markdown(text=content.fallback_markdown or _blocks_to_markdown(content.blocks))
        return PlainText(text=content.fallback_markdown or _blocks_to_plain(content.blocks))

    if isinstance(content, Markdown):
        if capabilities.supports(ContentKind.PLAIN_TEXT):
            return PlainText(text=content.text)
        return content  # nothing usable; caller will hit no-supported path

    if isinstance(content, Chunked):
        if capabilities.supports(ContentKind.PLAIN_TEXT):
            return PlainText(text=content.text)
        return content

    if isinstance(content, ApprovalPrompt):
        if capabilities.supports(ContentKind.BLOCK):
            return Block(
                blocks=_approval_to_blocks(content),
                fallback_markdown=_approval_to_markdown(content),
            )
        if capabilities.supports(ContentKind.MARKDOWN):
            return Markdown(text=_approval_to_markdown(content))
        if capabilities.supports(ContentKind.PLAIN_TEXT):
            return PlainText(text=_approval_to_plain(content))
        return content

    if isinstance(content, (TypingStart, TypingStop)):
        # Typing indicators are best-effort — the caller checks supports().
        return content

    return content


def split_chunked(content: Chunked, capabilities: AdapterCapabilities) -> List[PlainText]:
    """Helper: split a Chunked into max_chunk_chars-sized PlainText pieces."""
    text = content.text
    cap = capabilities.max_chunk_chars
    if len(text) <= cap:
        return [PlainText(text=text)]
    return [PlainText(text=text[i:i + cap]) for i in range(0, len(text), cap)]


def _blocks_to_markdown(blocks) -> str:
    parts = []
    for b in blocks:
        if isinstance(b, dict):
            t = b.get("text") or b.get("plain_text") or ""
            if t:
                parts.append(str(t))
    return "\n\n".join(parts)


def _blocks_to_plain(blocks) -> str:
    return _blocks_to_markdown(blocks)


def _approval_to_blocks(p: ApprovalPrompt):
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*Approval requested* — {p.summary}"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": (p.rationale or "_no rationale_")}},
        {"type": "actions", "elements": [
            {"type": "button", "text": "Approve", "value": f"approve:{p.request_id}"},
            {"type": "button", "text": "Reject", "value": f"reject:{p.request_id}"},
        ]},
    ]


def _approval_to_markdown(p: ApprovalPrompt) -> str:
    rationale = f"\n_{p.rationale}_" if p.rationale else ""
    return (
        f"**Approval requested** — {p.summary}{rationale}\n\n"
        f"Reply `{p.approve_command} {p.request_id}` to approve, "
        f"or `{p.reject_command} {p.request_id}` to reject."
    )


def _approval_to_plain(p: ApprovalPrompt) -> str:
    rationale = f" ({p.rationale})" if p.rationale else ""
    return (
        f"Approval requested: {p.summary}{rationale}. "
        f"Reply '{p.approve_command} {p.request_id}' to approve, "
        f"or '{p.reject_command} {p.request_id}' to reject."
    )
