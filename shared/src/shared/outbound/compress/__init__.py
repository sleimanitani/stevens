"""Modular content-compression backends.

A "compressor" takes a body of text (typically HTML or markdown) plus a
query / hint, and returns a much shorter extract focused on what's
relevant. Used by Arachne / web_compress / web_fetch(compress_with_query=…)
to keep agent context windows manageable.

Default backend: Anthropic Claude Haiku (small, cheap, fast).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Protocol


class CompressError(Exception):
    """Raised on compression-backend errors."""


@dataclass(frozen=True)
class CompressResult:
    compressed_text: str
    original_chars: int
    compressed_chars: int
    backend: str

    @property
    def ratio(self) -> float:
        if self.original_chars == 0:
            return 0.0
        return self.compressed_chars / self.original_chars


class CompressBackend(Protocol):
    name: str

    async def compress(
        self, *, text: str, query: str, max_output_chars: int = 4000,
    ) -> CompressResult: ...


BackendFactory = Callable[..., CompressBackend]
_BACKENDS: Dict[str, BackendFactory] = {}


def register_backend(name: str, factory: BackendFactory) -> None:
    if name in _BACKENDS:
        raise ValueError(f"compress backend {name!r} already registered")
    _BACKENDS[name] = factory


def get_backend(name: str) -> BackendFactory:
    if name not in _BACKENDS:
        raise CompressError(
            f"unknown compress backend {name!r}; registered: {sorted(_BACKENDS)}"
        )
    return _BACKENDS[name]


def select_backend_name() -> str:
    return os.environ.get("DEMIURGE_COMPRESS_BACKEND", "anthropic")


from . import anthropic  # noqa: E402, F401 — side-effect: registers itself
