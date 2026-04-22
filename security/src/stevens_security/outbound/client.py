"""Base outbound HTTP client used by all capability adapters.

``OutboundClient`` wraps a single ``httpx.AsyncClient`` so the Security
Agent can make authenticated outbound calls. Service-specific logic
(Gmail, Anthropic, payments, ...) extends this — subclasses ask the
sealed store for credentials, attach the right auth header, and parse
the service's response shape.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import httpx


class OutboundError(Exception):
    """Any failure in the outbound HTTP path — connect, timeout, non-2xx, parse."""


class OutboundClient:
    """Holds a shared httpx.AsyncClient and a reference to the sealed store.

    Not meant to be called directly from capabilities — use a
    service-specific subclass (e.g. :class:`GmailClient`) that knows how
    to attach credentials.
    """

    def __init__(
        self,
        *,
        sealed_store: Any,  # SealedStore — avoid the circular import
        transport: Optional[httpx.AsyncBaseTransport] = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._sealed_store = sealed_store
        self._client = httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(timeout_seconds),
        )

    @property
    def sealed_store(self) -> Any:
        return self._sealed_store

    @property
    def http(self) -> httpx.AsyncClient:
        return self._client

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        try:
            resp = await self._client.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json,
                data=data,
            )
        except httpx.RequestError as e:
            raise OutboundError(f"transport error: {e}") from e
        if resp.status_code >= 400:
            raise OutboundError(
                f"http {resp.status_code} from {url}: {resp.text[:200]}"
            )
        if not resp.content:
            return {}
        try:
            parsed = resp.json()
        except ValueError as e:
            raise OutboundError(f"response is not JSON: {e}") from e
        if not isinstance(parsed, dict):
            raise OutboundError("response is not a JSON object")
        return parsed

    async def aclose(self) -> None:
        await self._client.aclose()
