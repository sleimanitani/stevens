"""Tavily Search API backend.

API: https://docs.tavily.com/docs/rest-api/api-reference
Endpoint: POST https://api.tavily.com/search
Body: {api_key, query, max_results, search_depth, include_answer, ...}
"""

from __future__ import annotations

from typing import List, Optional

import httpx

from . import SearchBackend, SearchError, SearchResult, SearchResults, register_backend


_TAVILY_URL = "https://api.tavily.com/search"


class TavilyBackend:
    name = "tavily"

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
                    _TAVILY_URL,
                    json={
                        "api_key": self._api_key,
                        "query": query,
                        "max_results": max_results,
                        "search_depth": "basic",
                    },
                    headers={"Accept": "application/json"},
                )
            except httpx.HTTPError as e:
                raise SearchError(f"tavily transport error: {e}") from e
        if resp.status_code == 401:
            raise SearchError("tavily: 401 — check api key in web.tavily.api_key")
        if resp.status_code == 429:
            raise SearchError("tavily: 429 — quota / rate limit hit")
        if resp.status_code >= 400:
            raise SearchError(f"tavily: HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            raise SearchError(f"tavily: malformed JSON: {e}") from e
        results: List[SearchResult] = []
        for item in data.get("results") or []:
            if not isinstance(item, dict):
                continue
            results.append(SearchResult(
                title=item.get("title", "") or "",
                url=item.get("url", "") or "",
                snippet=item.get("content", "") or "",
            ))
        return SearchResults(backend="tavily", query=query, results=results)


def _factory(*, api_key: bytes, transport: Optional[httpx.AsyncBaseTransport] = None) -> TavilyBackend:
    return TavilyBackend(api_key=api_key, transport=transport)


register_backend("tavily", _factory)
