"""Brave Search API backend.

Default search provider. Reads ``web.brave.api_key`` from the sealed store
(passed in at construction; this module doesn't open the store itself —
that's the capability handler's job).

API: https://api.search.brave.com/res/v1/web/search?q=<query>
Header: X-Subscription-Token: <api_key>
Free tier: 2k req/mo.
"""

from __future__ import annotations

from typing import Any, List, Optional

import httpx

from . import SearchBackend, SearchError, SearchResult, SearchResults, register_backend


_BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"


class BraveBackend:
    name = "brave"

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
                resp = await client.get(
                    _BRAVE_URL,
                    params={"q": query, "count": max_results},
                    headers={
                        "Accept": "application/json",
                        "X-Subscription-Token": self._api_key,
                    },
                )
            except httpx.HTTPError as e:
                raise SearchError(f"brave transport error: {e}") from e
        if resp.status_code == 401:
            raise SearchError("brave: 401 — check api key in web.brave.api_key")
        if resp.status_code == 429:
            raise SearchError("brave: 429 — quota / rate limit hit")
        if resp.status_code >= 400:
            raise SearchError(f"brave: HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            raise SearchError(f"brave: malformed JSON: {e}") from e
        results: List[SearchResult] = []
        for item in (data.get("web") or {}).get("results") or []:
            if not isinstance(item, dict):
                continue
            title = item.get("title") or ""
            url = item.get("url") or ""
            snippet = item.get("description") or ""
            if isinstance(title, str) and isinstance(url, str):
                results.append(
                    SearchResult(title=title, url=url, snippet=snippet)
                )
        return SearchResults(backend="brave", query=query, results=results)


def _factory(*, api_key: bytes, transport: Optional[httpx.AsyncBaseTransport] = None) -> BraveBackend:
    return BraveBackend(api_key=api_key, transport=transport)


register_backend("brave", _factory)
