"""Anthropic Claude Haiku compressor.

Cheap + fast model is the right pick for compression: we're paying for
clean text-extraction-with-relevance-bias, not reasoning. Haiku is the
sweet spot.

API: https://docs.anthropic.com/en/api/messages
"""

from __future__ import annotations

import html
import re
from typing import Any, Dict, Optional

import httpx

from . import (
    CompressBackend,
    CompressError,
    CompressResult,
    register_backend,
)


_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_DEFAULT_MODEL = "claude-haiku-4-5-20251001"
_MAX_INPUT_CHARS = 200_000   # ~50k tokens; well under context limits


_SYSTEM_PROMPT = (
    "You are a content extractor. Given a web page or document and a query, "
    "produce a focused extract containing only the parts relevant to the "
    "query. Preserve facts, numbers, names, dates, URLs verbatim. Drop "
    "navigation, boilerplate, ads, footers. Use plain text or short markdown. "
    "Do not summarize beyond what's needed for relevance — preserve detail "
    "where the query suggests it matters. Cap output around the requested length."
)


def _strip_html(text: str) -> str:
    """Cheap HTML strip — enough for cleaner LLM input. Not a real parser."""
    if "<" not in text or ">" not in text:
        return text
    # Remove script/style blocks first.
    no_script = re.sub(r"<script\b[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
    no_style = re.sub(r"<style\b[^>]*>.*?</style>", "", no_script, flags=re.DOTALL | re.IGNORECASE)
    # Strip tags.
    stripped = re.sub(r"<[^>]+>", " ", no_style)
    # Collapse whitespace.
    stripped = re.sub(r"\s+", " ", stripped).strip()
    return html.unescape(stripped)


class AnthropicCompressor:
    name = "anthropic"

    def __init__(
        self,
        *,
        api_key: bytes,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        timeout_seconds: float = 30.0,
        model: str = _DEFAULT_MODEL,
    ) -> None:
        self._api_key = api_key.decode("utf-8") if isinstance(api_key, bytes) else api_key
        self._transport = transport
        self._timeout = timeout_seconds
        self._model = model

    async def compress(
        self,
        *,
        text: str,
        query: str,
        max_output_chars: int = 4000,
    ) -> CompressResult:
        if not isinstance(text, str) or not text:
            raise CompressError("text must be a non-empty string")
        cleaned = _strip_html(text)
        if len(cleaned) > _MAX_INPUT_CHARS:
            cleaned = cleaned[:_MAX_INPUT_CHARS]
        max_tokens = max(256, min(8192, int(max_output_chars / 3)))   # ~3 chars/token rough
        user_msg = (
            f"QUERY: {query}\n\n---\n\nCONTENT:\n{cleaned}\n\n---\n\n"
            f"Extract the parts of CONTENT relevant to QUERY. "
            f"Aim for under {max_output_chars} characters."
        )
        async with httpx.AsyncClient(
            transport=self._transport, timeout=self._timeout,
        ) as client:
            try:
                resp = await client.post(
                    _ANTHROPIC_URL,
                    json={
                        "model": self._model,
                        "max_tokens": max_tokens,
                        "system": _SYSTEM_PROMPT,
                        "messages": [{"role": "user", "content": user_msg}],
                    },
                    headers={
                        "x-api-key": self._api_key,
                        "anthropic-version": "2023-06-01",
                        "Content-Type": "application/json",
                    },
                )
            except httpx.HTTPError as e:
                raise CompressError(f"anthropic transport error: {e}") from e
        if resp.status_code == 401:
            raise CompressError("anthropic: 401 — check api key in compress.anthropic.api_key")
        if resp.status_code == 429:
            raise CompressError("anthropic: 429 — quota / rate limit hit")
        if resp.status_code >= 400:
            raise CompressError(f"anthropic: HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            raise CompressError(f"anthropic: malformed JSON: {e}") from e
        # Response shape: {"content": [{"type": "text", "text": "..."}], ...}
        blocks = data.get("content") or []
        text_out_parts = [
            b.get("text", "") for b in blocks
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        text_out = "".join(text_out_parts)
        return CompressResult(
            compressed_text=text_out,
            original_chars=len(text),
            compressed_chars=len(text_out),
            backend="anthropic",
        )


def _factory(*, api_key: bytes, transport: Optional[httpx.AsyncBaseTransport] = None) -> AnthropicCompressor:
    return AnthropicCompressor(api_key=api_key, transport=transport)


register_backend("anthropic", _factory)
