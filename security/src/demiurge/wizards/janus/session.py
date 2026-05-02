"""BrowserSession Protocol + FakeBrowserSession (tests)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Protocol, Tuple


class BrowserSession(Protocol):
    async def nav(self, url: str) -> None: ...
    async def wait(self, selector: str, *, timeout_s: float = 30.0) -> None: ...
    async def fill(self, selector: str, value: str) -> None: ...
    async def click(self, selector: str) -> None: ...
    async def extract(self, selector: str, *, use_value: bool = False) -> str: ...


@dataclass
class FakeBrowserSession:
    """Records every call; returns canned values from ``extracts``.

    Tests script the recipe by pre-populating ``extracts``: ``{selector: value}``.
    """

    calls: List[Tuple[str, Any]] = field(default_factory=list)
    extracts: Dict[str, str] = field(default_factory=dict)

    async def nav(self, url: str) -> None:
        self.calls.append(("nav", url))

    async def wait(self, selector: str, *, timeout_s: float = 30.0) -> None:
        self.calls.append(("wait", selector))

    async def fill(self, selector: str, value: str) -> None:
        self.calls.append(("fill", (selector, value)))

    async def click(self, selector: str) -> None:
        self.calls.append(("click", selector))

    async def extract(self, selector: str, *, use_value: bool = False) -> str:
        self.calls.append(("extract", selector))
        return self.extracts.get(selector, "")
