"""Firecrawl Search API backend.

API: https://docs.firecrawl.dev/api-reference/endpoint/search
Endpoint: POST https://api.firecrawl.dev/v1/search
Header: Authorization: Bearer <api_key>
"""

from __future__ import annotations

from typing import List, Optional

import httpx

from . import SearchBackend, SearchError, SearchResult, SearchResults, register_backend


_FIRECRAWL_URL = "https://api.firecrawl.dev/v1/search"


class FirecrawlBackend:
    name = "firecrawl"

    def __init__(
        self,
        *,
        api_key: bytes,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._api_key = api_key.decode("utf-8") if isinstance(api_key, bytes) else api_key
        self._transport = transport
        self._timeout = timeout_seconds

    async def search(self, query: str, *, max_results: int = 10) -> SearchResults:
        if not isinstance(query, str) or not query.strip():
            raise SearchError("query must be a non-empty string")
        max_results = max(1, min(20, int(max_results)))
        async with httpx.AsyncClient(
            transport=self._transport, timeout=self._timeout,
        ) as client:
            try:
                resp = await client.post(
                    _FIRECRAWL_URL,
                    json={"query": query, "limit": max_results},
                    headers={
                        "Accept": "application/json",
                        "Authorization": f"Bearer {self._api_key}",
                    },
                )
            except httpx.HTTPError as e:
                raise SearchError(f"firecrawl transport error: {e}") from e
        if resp.status_code == 401:
            raise SearchError("firecrawl: 401 — check api key in web.firecrawl.api_key")
        if resp.status_code == 429:
            raise SearchError("firecrawl: 429 — quota / rate limit hit")
        if resp.status_code >= 400:
            raise SearchError(f"firecrawl: HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            raise SearchError(f"firecrawl: malformed JSON: {e}") from e
        results: List[SearchResult] = []
        for item in data.get("data") or []:
            if not isinstance(item, dict):
                continue
            results.append(SearchResult(
                title=item.get("title", "") or "",
                url=item.get("url", "") or "",
                snippet=item.get("description", "") or item.get("markdown", "")[:200] or "",
            ))
        return SearchResults(backend="firecrawl", query=query, results=results)


def _factory(*, api_key: bytes, transport: Optional[httpx.AsyncBaseTransport] = None) -> FirecrawlBackend:
    return FirecrawlBackend(api_key=api_key, transport=transport)


register_backend("firecrawl", _factory)
