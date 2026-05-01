"""AdapterCapabilities — what an adapter can do.

In its own module so both ``adapter.py`` and ``session.py`` can depend
on it without circular imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import FrozenSet

from .content import ContentKind


@dataclass(frozen=True)
class AdapterCapabilities:
    supported_kinds: FrozenSet[ContentKind] = field(default_factory=frozenset)
    max_chunk_chars: int = 2000
    supports_edits: bool = False
    supports_threads: bool = False
    supports_modals: bool = False

    def supports(self, kind: ContentKind) -> bool:
        return kind in self.supported_kinds
