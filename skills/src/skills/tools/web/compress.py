"""Web compress — synchronous tool wrapping Enkidu's network.compress.

Given a chunk of text and a query, returns a compressed extract focused on
what's relevant to the query. Used by ReAct agents that fetched a long
page and want to fit it back into context. Backend defaults to Anthropic
Claude Haiku.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Dict, Optional

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from shared.security_client import (
    ResponseError,
    SecurityClient,
    TransportError,
)


log = logging.getLogger(__name__)


TOOL_METADATA = {
    "id": "web.compress",
    "version": "1.0.0",
    "scope": "shared",
    "safety_class": "read-only",
}


class CompressInput(BaseModel):
    text: str = Field(description="The text to compress (HTML or plain)")
    query: str = Field(description="What you're looking for — the compressor focuses extract on this")
    max_output_chars: int = Field(default=4000, ge=200, le=20000)


_CLIENT: Optional[SecurityClient] = None


def _client() -> SecurityClient:
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    socket_path = os.environ.get("STEVENS_SECURITY_SOCKET", "/run/stevens/security.sock")
    caller = os.environ["STEVENS_CALLER_NAME"]
    key_path = os.environ["STEVENS_PRIVATE_KEY_PATH"]
    _CLIENT = SecurityClient.from_key_file(
        socket_path=socket_path, caller_name=caller, private_key_path=key_path,
    )
    return _CLIENT


def _set_client_for_tests(client: Optional[SecurityClient]) -> None:
    global _CLIENT
    _CLIENT = client


def _compress_sync(text: str, query: str, max_output_chars: int = 4000) -> str:
    async def _run() -> Dict[str, Any]:
        return await _client().call(
            "network.compress",
            {"text": text, "query": query, "max_output_chars": max_output_chars},
        )

    try:
        result = asyncio.run(_run())
    except (ResponseError, TransportError) as e:
        return json.dumps({"error": "broker_error", "detail": str(e)})

    if "error" in result:
        return json.dumps(result)
    return json.dumps({
        "compressed_text": result.get("compressed_text"),
        "ratio": result.get("ratio"),
        "backend": result.get("backend"),
    })


def build_tool() -> StructuredTool:
    return StructuredTool.from_function(
        func=_compress_sync,
        name="web_compress",
        description=(
            "Compress a long page or document down to just the parts relevant "
            "to a specific query. Returns {compressed_text, ratio, backend}. "
            "Use this any time you fetched a page and want to fit it back into "
            "context — much cheaper than feeding the raw HTML to your model."
        ),
        args_schema=CompressInput,
    )
