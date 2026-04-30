"""Arachne agent — async-path web fetch / search handler."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
from typing import Any, Dict, Optional

from shared import bus
from shared.events import (
    BaseEvent,
    WebFetchRequestedEvent,
    WebFetchResponseEvent,
    WebSearchRequestedEvent,
    WebSearchResponseEvent,
)
from shared.security_client import (
    ResponseError,
    SecurityClient,
    SecurityClientError,
    TransportError,
)


log = logging.getLogger(__name__)


_DEFAULT_WORKERS = 4


_CLIENT: Optional[SecurityClient] = None
_SEMAPHORE: Optional[asyncio.Semaphore] = None


def _client() -> SecurityClient:
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    socket_path = os.environ.get("STEVENS_SECURITY_SOCKET", "/run/stevens/security.sock")
    caller = os.environ.get("STEVENS_CALLER_NAME", "arachne")
    key_path = os.environ.get("STEVENS_PRIVATE_KEY_PATH")
    if not key_path:
        raise RuntimeError(
            "STEVENS_PRIVATE_KEY_PATH must be set for the arachne agent"
        )
    _CLIENT = SecurityClient.from_key_file(
        socket_path=socket_path,
        caller_name=caller,
        private_key_path=key_path,
    )
    return _CLIENT


def _set_client_for_tests(client: Optional[SecurityClient]) -> None:
    global _CLIENT
    _CLIENT = client


def _semaphore() -> asyncio.Semaphore:
    global _SEMAPHORE
    if _SEMAPHORE is not None:
        return _SEMAPHORE
    workers = int(os.environ.get("STEVENS_WEB_WORKERS", _DEFAULT_WORKERS))
    _SEMAPHORE = asyncio.Semaphore(workers)
    return _SEMAPHORE


def _set_semaphore_for_tests(sem: Optional[asyncio.Semaphore]) -> None:
    global _SEMAPHORE
    _SEMAPHORE = sem


async def _publish(event: BaseEvent) -> None:
    try:
        await bus.publish(event)
    except Exception:  # noqa: BLE001
        log.exception("arachne failed to publish %s", event.topic)


async def _handle_fetch(req: WebFetchRequestedEvent) -> None:
    sem = _semaphore()
    async with sem:
        try:
            result = await _client().call(
                "network.fetch",
                {"url": req.url, "follow_redirects": req.follow_redirects},
            )
        except (ResponseError, TransportError) as e:
            await _publish(WebFetchResponseEvent(
                account_id="system", request_id=req.request_id,
                error="broker_error", error_detail=str(e),
            ))
            return

        if "error" in result:
            await _publish(WebFetchResponseEvent(
                account_id="system", request_id=req.request_id,
                error=result["error"],
                error_detail=str(result.get("detail", "")),
            ))
            return

        body = result.get("body", b"")
        body_text: Optional[str] = None
        body_b64: Optional[str] = None
        if isinstance(body, bytes):
            try:
                body_text = body.decode("utf-8")
            except UnicodeDecodeError:
                body_b64 = base64.b64encode(body).decode("ascii")
        elif isinstance(body, str):
            body_text = body
        else:
            body_b64 = base64.b64encode(bytes(body)).decode("ascii")

        await _publish(WebFetchResponseEvent(
            account_id="system",
            request_id=req.request_id,
            status=result.get("status"),
            body_text=body_text,
            body_b64=body_b64,
            final_url=result.get("final_url"),
            truncated=bool(result.get("truncated", False)),
            cache_hit=bool(result.get("cache_hit", False)),
        ))


async def _handle_search(req: WebSearchRequestedEvent) -> None:
    sem = _semaphore()
    async with sem:
        try:
            result = await _client().call(
                "network.search",
                {"query": req.query, "max_results": req.max_results},
            )
        except (ResponseError, TransportError) as e:
            await _publish(WebSearchResponseEvent(
                account_id="system", request_id=req.request_id,
                error="broker_error", error_detail=str(e),
            ))
            return

        if "error" in result:
            await _publish(WebSearchResponseEvent(
                account_id="system", request_id=req.request_id,
                error=result["error"],
                error_detail=str(result.get("detail", "")),
            ))
            return

        await _publish(WebSearchResponseEvent(
            account_id="system",
            request_id=req.request_id,
            backend=result.get("backend"),
            results=list(result.get("results") or []),
            cache_hit=bool(result.get("cache_hit", False)),
        ))


async def handle(event: BaseEvent, config: Dict[str, Any]) -> None:
    if isinstance(event, WebFetchRequestedEvent):
        await _handle_fetch(event)
    elif isinstance(event, WebSearchRequestedEvent):
        await _handle_search(event)
    else:
        log.debug("arachne ignoring %s", type(event).__name__)
