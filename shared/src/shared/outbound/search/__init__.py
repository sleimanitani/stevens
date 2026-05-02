"""Modular search-backend layer.

A backend implements ``SearchBackend.search(query, max_results) -> SearchResults``
returning the normalized result shape Stevens uses regardless of provider.

Default backend: Brave (``brave.py``). Add Tavily / SearXNG / DuckDuckGo
as new modules implementing the same Protocol; the selector reads
``DEMIURGE_SEARCH_BACKEND`` from the environment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Protocol


class SearchError(Exception):
    """Raised on search-backend errors (auth, quota, malformed response)."""


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchResults:
    backend: str
    query: str
    results: List[SearchResult]


class SearchBackend(Protocol):
    name: str

    async def search(
        self, query: str, *, max_results: int = 10,
    ) -> SearchResults: ...


# Lookup of known backend factories. Each factory takes (sealed_store, transport_or_none).
BackendFactory = Callable[..., SearchBackend]
_BACKENDS: Dict[str, BackendFactory] = {}


def register_backend(name: str, factory: BackendFactory) -> None:
    if name in _BACKENDS:
        raise ValueError(f"backend {name!r} already registered")
    _BACKENDS[name] = factory


def get_backend(name: str) -> BackendFactory:
    if name not in _BACKENDS:
        raise SearchError(
            f"unknown search backend {name!r}; registered: {sorted(_BACKENDS)}"
        )
    return _BACKENDS[name]


def select_backend_name() -> str:
    return os.environ.get("DEMIURGE_SEARCH_BACKEND", "brave")


# Register built-in backends. Imported for the side effect.
from . import brave  # noqa: E402, F401
from . import exa  # noqa: E402, F401
from . import firecrawl  # noqa: E402, F401
from . import tavily  # noqa: E402, F401
