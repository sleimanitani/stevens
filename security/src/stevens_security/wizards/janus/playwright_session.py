"""Playwright-backed BrowserSession.

Lazy-imports playwright so the security package can be imported without
playwright installed (operators who never run Janus don't pay the
~150MB install cost).

Persistent context dir: ``~/.config/stevens/janus-profile/`` — keeps
cookies + sign-ins across runs so the operator doesn't re-auth each
time.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator


log = logging.getLogger(__name__)


def _profile_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "stevens" / "janus-profile"


class PlaywrightSession:
    """Wraps a playwright Page. Constructed via ``open_chromium``."""

    def __init__(self, page) -> None:
        self._page = page

    async def nav(self, url: str) -> None:
        log.info("nav: %s", url)
        await self._page.goto(url, wait_until="domcontentloaded")

    async def wait(self, selector: str, *, timeout_s: float = 30.0) -> None:
        await self._page.wait_for_selector(selector, timeout=timeout_s * 1000)

    async def fill(self, selector: str, value: str) -> None:
        await self._page.fill(selector, value)

    async def click(self, selector: str) -> None:
        await self._page.click(selector)

    async def extract(self, selector: str, *, use_value: bool = False) -> str:
        if use_value:
            return await self._page.input_value(selector)
        return (await self._page.inner_text(selector)).strip()


@asynccontextmanager
async def open_chromium(*, headless: bool = False) -> AsyncIterator[PlaywrightSession]:
    """Open a Chromium browser with a persistent profile dir.

    Operator typically runs Janus headed (default); headless=True is
    for advanced uses where the operator pre-signed-in and just wants
    the rest of the recipe to play.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError as e:
        raise RuntimeError(
            "playwright not installed. Run `uv pip install playwright` "
            "and `uv run playwright install chromium`."
        ) from e

    profile = _profile_dir()
    profile.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=headless,
        )
        try:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            yield PlaywrightSession(page)
        finally:
            await ctx.close()
