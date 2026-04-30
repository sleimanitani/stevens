"""``network.*`` capabilities — outbound HTTP and search through Enkidu.

Synchronous, broker-mediated. Both ReAct skills and Arachne (the async
agent) call these. The cache + rate limiter live in Enkidu so the two
paths share state by construction.

URL validator runs first (private-CIDR deny-list), then cache check,
then rate-limit gate, then the actual outbound call.

Audit semantics:
- ``network.fetch``: ``url`` hashed (full URL is privacy-relevant);
  ``host`` clear (routing-y, useful in logs).
- ``network.search``: ``query`` hashed (queries reveal intent); ``backend``
  clear.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from shared.outbound.search import (
    SearchError,
    SearchResults,
    get_backend,
    select_backend_name,
)
from shared.outbound.web import (
    FetchResult,
    UrlError,
    WebClient,
    validate_url,
)

from ..context import CapabilityContext
from ..outbound.web_state import DomainRateLimiter, TTLCache
from .registry import default_registry

log = logging.getLogger(__name__)


_FETCH_TTL_SECONDS = 60 * 60        # 1 hour
_SEARCH_TTL_SECONDS = 15 * 60       # 15 minutes


class WebState:
    """Bundle of cache + rate limiter, injected via context.extra['web_state']."""

    def __init__(
        self,
        *,
        fetch_cache: TTLCache,
        search_cache: TTLCache,
        rate_limiter: DomainRateLimiter,
        web_client: WebClient,
    ) -> None:
        self.fetch_cache = fetch_cache
        self.search_cache = search_cache
        self.rate_limiter = rate_limiter
        self.web_client = web_client


def _state(context: CapabilityContext) -> WebState:
    s = context.extra.get("web_state") if isinstance(context.extra, dict) else None
    if not isinstance(s, WebState):
        raise RuntimeError(
            "network.* capability invoked but no WebState configured in "
            "CapabilityContext.extra['web_state']"
        )
    return s


# --- network.fetch ---


@default_registry.capability(
    "network.fetch",
    clear_params=["host"],   # NB: not 'url' — full URL is hashed
)
async def network_fetch(agent, params, context):
    url = params.get("url")
    if not isinstance(url, str):
        return {"error": "url_required"}
    follow_redirects = bool(params.get("follow_redirects", True))

    try:
        validated = validate_url(url)
    except UrlError as e:
        return {"error": "url_rejected", "detail": str(e)}

    state = _state(context)
    cache_key = (validated.url, follow_redirects)
    cached = state.fetch_cache.get(cache_key)
    if cached is not None:
        return _fetch_result_to_dict(cached, cache_hit=True)

    if not state.rate_limiter.allow(validated.host):
        return {
            "error": "rate_limited",
            "host": validated.host,
            "detail": "per-domain rate limit exceeded",
        }

    try:
        result = await state.web_client.fetch(
            validated.url, follow_redirects=follow_redirects,
        )
    except Exception as e:  # noqa: BLE001
        return {"error": "transport_failed", "detail": str(e)}

    # Cache only successful 2xx responses; redirect status codes change
    # behavior across calls, so don't cache.
    if 200 <= result.status < 300:
        state.fetch_cache.put(cache_key, result, ttl_seconds=_FETCH_TTL_SECONDS)
    return _fetch_result_to_dict(result, cache_hit=False)


def _fetch_result_to_dict(result: FetchResult, *, cache_hit: bool) -> Dict[str, Any]:
    return {
        "status": result.status,
        "headers": dict(result.headers),
        "body": result.body,
        "final_url": result.final_url,
        "truncated": result.truncated,
        "cache_hit": cache_hit,
    }


# --- network.search ---


@default_registry.capability(
    "network.search",
    clear_params=["backend", "max_results"],
)
async def network_search(agent, params, context):
    query = params.get("query")
    if not isinstance(query, str) or not query.strip():
        return {"error": "query_required"}
    max_results = int(params.get("max_results") or 10)
    backend_name = params.get("backend") or select_backend_name()

    state = _state(context)
    cache_key = (backend_name, query, max_results)
    cached = state.search_cache.get(cache_key)
    if cached is not None:
        return {
            "backend": cached.backend,
            "query": cached.query,
            "results": [_result_to_dict(r) for r in cached.results],
            "cache_hit": True,
        }

    # Sealed-store-resolved API key.
    if context.sealed_store is None:
        return {"error": "sealed_store_unavailable"}
    api_key_name = f"web.{backend_name}.api_key"
    try:
        api_key = context.sealed_store.get_by_name(api_key_name)
    except Exception as e:  # noqa: BLE001
        return {
            "error": "api_key_missing",
            "detail": f"{api_key_name}: {e}",
        }
    try:
        factory = get_backend(backend_name)
    except SearchError as e:
        return {"error": "unknown_backend", "detail": str(e)}
    backend = factory(api_key=api_key)
    try:
        result: SearchResults = await backend.search(query, max_results=max_results)
    except SearchError as e:
        return {"error": "search_failed", "detail": str(e)}

    state.search_cache.put(cache_key, result, ttl_seconds=_SEARCH_TTL_SECONDS)
    return {
        "backend": result.backend,
        "query": result.query,
        "results": [_result_to_dict(r) for r in result.results],
        "cache_hit": False,
    }


def _result_to_dict(r) -> Dict[str, Any]:
    return {"title": r.title, "url": r.url, "snippet": r.snippet}
