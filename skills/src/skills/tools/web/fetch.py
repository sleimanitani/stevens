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


def _fetch_sync(url: str, follow_redirects: bool = True) -> str:
    async def _run() -> Dict[str, Any]:
        return await _client().call(
            "network.fetch",
            {"url": url, "follow_redirects": follow_redirects},
        )

    try:
        result = asyncio.run(_run())
    except (ResponseError, TransportError) as e:
        return json.dumps({"error": "broker_error", "detail": str(e)})

    if "error" in result:
        return json.dumps(result)

    body = result.get("body", b"")
    if isinstance(body, bytes):
        # Decode if it's text-shaped; otherwise return base64 so the LLM can
        # still see something meaningful.
        try:
            text = body.decode("utf-8")
            body_payload: Any = text
        except UnicodeDecodeError:
            import base64
            body_payload = "base64:" + base64.b64encode(body).decode("ascii")
    else:
        body_payload = body
    return json.dumps({
        "status": result.get("status"),
        "final_url": result.get("final_url"),
        "body": body_payload,
        "truncated": result.get("truncated", False),
        "cache_hit": result.get("cache_hit", False),
        "content_type": result.get("headers", {}).get("content-type", ""),
    })


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
