"""Web search — synchronous tool wrapping Enkidu's network.search capability.

Returns a normalized list of {title, url, snippet}. Backend defaults to
Brave (configurable per-call via the ``backend`` field, or process-wide
via the STEVENS_SEARCH_BACKEND env var read by Enkidu).
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

# Inline client helpers — the skills registry does dynamic file-import
# (no package context), so relative imports between sibling tool modules
# fail at load time. Duplicating the small client helper keeps each tool
# module self-contained.

_CLIENT: Optional[SecurityClient] = None


def _client() -> SecurityClient:
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    socket_path = os.environ.get("STEVENS_SECURITY_SOCKET", "/run/stevens/security.sock")
    caller = os.environ["STEVENS_CALLER_NAME"]
    key_path = os.environ["STEVENS_PRIVATE_KEY_PATH"]
    _CLIENT = SecurityClient.from_key_file(
        socket_path=socket_path,
        caller_name=caller,
        private_key_path=key_path,
    )
    return _CLIENT


def _set_client_for_tests(client: Optional[SecurityClient]) -> None:
    global _CLIENT
    _CLIENT = client


log = logging.getLogger(__name__)


TOOL_METADATA = {
    "id": "web.search",
    "version": "1.0.0",
    "scope": "shared",
    "safety_class": "read-only",
}


class SearchInput(BaseModel):
    query: str = Field(description="Search query")
    max_results: int = Field(default=10, ge=1, le=20, description="Number of results to return (1-20)")


def _search_sync(query: str, max_results: int = 10) -> str:
    async def _run() -> Dict[str, Any]:
        return await _client().call(
            "network.search",
            {"query": query, "max_results": max_results},
        )

    try:
        result = asyncio.run(_run())
    except (ResponseError, TransportError) as e:
        return json.dumps({"error": "broker_error", "detail": str(e)})

    if "error" in result:
        return json.dumps(result)

    return json.dumps({
        "backend": result.get("backend"),
        "results": result.get("results", []),
        "cache_hit": result.get("cache_hit", False),
    })


def build_tool() -> StructuredTool:
    return StructuredTool.from_function(
        func=_search_sync,
        name="web_search",
        description=(
            "Search the web via the Security Agent broker (default backend: Brave). "
            "Returns normalized [{title, url, snippet}, ...] regardless of provider. "
            "Use this for any web search; do not write your own."
        ),
        args_schema=SearchInput,
    )
