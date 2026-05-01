"""Exa neural-search API backend.

API: https://docs.exa.ai/reference/search
Endpoint: POST https://api.exa.ai/search
Header: x-api-key: <api_key>
Body: {query, numResults, type: "neural"|"keyword"|"auto"}
"""

from __future__ import annotations

from typing import List, Optional

import httpx

from . import SearchBackend, SearchError, SearchResult, SearchResults, register_backend


_EXA_URL = "https://api.exa.ai/search"


class ExaBackend:
    name = "exa"

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
        max_results = max(1, min(25, int(max_results)))
        async with httpx.AsyncClient(
            transport=self._transport, timeout=self._timeout,
        ) as client:
            try:
                resp = await client.post(
                    _EXA_URL,
                    json={"query": query, "numResults": max_results, "type": "auto"},
                    headers={
                        "Accept": "application/json",
                        "x-api-key": self._api_key,
                    },
                )
            except httpx.HTTPError as e:
                raise SearchError(f"exa transport error: {e}") from e
        if resp.status_code == 401:
            raise SearchError("exa: 401 — check api key in web.exa.api_key")
        if resp.status_code == 429:
            raise SearchError("exa: 429 — quota / rate limit hit")
        if resp.status_code >= 400:
            raise SearchError(f"exa: HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            raise SearchError(f"exa: malformed JSON: {e}") from e
        results: List[SearchResult] = []
        for item in data.get("results") or []:
            if not isinstance(item, dict):
                continue
            results.append(SearchResult(
                title=item.get("title", "") or "",
                url=item.get("url", "") or "",
                snippet=item.get("text", "") or item.get("highlight", "") or "",
            ))
        return SearchResults(backend="exa", query=query, results=results)


def _factory(*, api_key: bytes, transport: Optional[httpx.AsyncBaseTransport] = None) -> ExaBackend:
    return ExaBackend(api_key=api_key, transport=transport)


register_backend("exa", _factory)
