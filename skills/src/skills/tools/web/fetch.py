"""Web fetch — synchronous tool wrapping Enkidu's network.fetch capability.

Used by ReAct/LangChain agents that need a tool call to return bytes.
Async-path consumers (event-driven workloads) should publish
``web.fetch.requested.*`` events for Arachne to handle instead.
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
    "id": "web.fetch",
    "version": "1.0.0",
    "scope": "shared",
    "safety_class": "read-only",
}


class FetchInput(BaseModel):
    url: str = Field(description="HTTPS URL to fetch (HTTP also accepted; private addresses rejected)")
    follow_redirects: bool = Field(
        default=True,
        description="Follow same-origin redirects up to a small depth (cross-origin redirects are never followed)",
    )
    compress_with_query: Optional[str] = Field(
        default=None,
        description=(
            "If set, the fetched body is piped through network.compress with this "
            "query and the compressed extract is returned in the `compressed_body` "
            "field alongside the raw body. Use this for long pages where you only "
            "need the parts relevant to a specific question."
        ),
    )


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


def _fetch_sync(url: str, follow_redirects: bool = True, compress_with_query: Optional[str] = None) -> str:
    async def _fetch() -> Dict[str, Any]:
        return await _client().call(
            "network.fetch",
            {"url": url, "follow_redirects": follow_redirects},
        )

    async def _compress(text: str, query: str) -> Dict[str, Any]:
        return await _client().call(
            "network.compress",
            {"text": text, "query": query},
        )

    try:
        result = asyncio.run(_fetch())
    except (ResponseError, TransportError) as e:
        return json.dumps({"error": "broker_error", "detail": str(e)})

    if "error" in result:
        return json.dumps(result)

    body = result.get("body", b"")
    body_text: Optional[str] = None
    if isinstance(body, bytes):
        try:
            body_text = body.decode("utf-8")
            body_payload: Any = body_text
        except UnicodeDecodeError:
            import base64
            body_payload = "base64:" + base64.b64encode(body).decode("ascii")
    else:
        body_text = body if isinstance(body, str) else None
        body_payload = body

    out: Dict[str, Any] = {
        "status": result.get("status"),
        "final_url": result.get("final_url"),
        "body": body_payload,
        "truncated": result.get("truncated", False),
        "cache_hit": result.get("cache_hit", False),
        "content_type": result.get("headers", {}).get("content-type", ""),
    }

    if compress_with_query and body_text:
        try:
            comp = asyncio.run(_compress(body_text, compress_with_query))
        except (ResponseError, TransportError) as e:
            out["compress_error"] = f"broker_error: {e}"
        else:
            if "error" in comp:
                out["compress_error"] = f"{comp['error']}: {comp.get('detail', '')}"
            else:
                out["compressed_body"] = comp.get("compressed_text")
                out["compressed_ratio"] = comp.get("ratio")
    return json.dumps(out)


def build_tool() -> StructuredTool:
    return StructuredTool.from_function(
        func=_fetch_sync,
        name="web_fetch",
        description=(
            "Fetch a URL via the Security Agent broker. Returns the response "
            "status, body, final URL, and a cache_hit flag. Private / loopback / "
            "link-local destinations are rejected. 50 MiB body cap; truncated "
            "responses indicate this. Use this for any HTTP GET — do not write "
            "your own fetcher."
        ),
        args_schema=FetchInput,
    )
