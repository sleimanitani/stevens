"""Sphinx — the PDF agent.

Subscribes to ``pdf.parse.requested.*``. For each request:
  1. Resolves the PDF path (only local paths in v0.4; URL-fetch via
     network.fetch is a future enhancement).
  2. Runs ``skills.tools.pdf.dispatcher.dispatch`` under a worker
     semaphore (default 4).
  3. Publishes a paired ``PDFParseResponseEvent``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from shared import bus
from shared.events import (
    BaseEvent,
    PDFParseRequestedEvent,
    PDFParseResponseEvent,
)


log = logging.getLogger(__name__)


_DEFAULT_WORKERS = 4
_SEMAPHORE: Optional[asyncio.Semaphore] = None


def _semaphore() -> asyncio.Semaphore:
    global _SEMAPHORE
    if _SEMAPHORE is not None:
        return _SEMAPHORE
    workers = int(os.environ.get("DEMIURGE_PDF_WORKERS", _DEFAULT_WORKERS))
    _SEMAPHORE = asyncio.Semaphore(workers)
    return _SEMAPHORE


def _set_semaphore_for_tests(sem: Optional[asyncio.Semaphore]) -> None:
    global _SEMAPHORE
    _SEMAPHORE = sem


async def _publish(event: BaseEvent) -> None:
    try:
        await bus.publish(event)
    except Exception:  # noqa: BLE001
        log.exception("sphinx failed to publish %s", event.topic)


def _do_dispatch(req: PDFParseRequestedEvent) -> Dict[str, Any]:
    """Run the dispatcher synchronously. Called via run_in_executor since
    Docling and pytesseract are CPU-bound."""
    from skills.tools.pdf.dispatcher import dispatch

    return dispatch(
        Path(req.path),
        mode=req.mode,
        hint=req.request_hint,
        prefer=req.prefer_strategy,
    )


async def _handle_request(req: PDFParseRequestedEvent) -> None:
    sem = _semaphore()
    async with sem:
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(None, _do_dispatch, req)
        except Exception as e:  # noqa: BLE001
            await _publish(PDFParseResponseEvent(
                account_id="system", request_id=req.request_id,
                error="dispatch_crashed", error_detail=f"{type(e).__name__}: {e}",
            ))
            return

        if "error" in result:
            await _publish(PDFParseResponseEvent(
                account_id="system", request_id=req.request_id,
                error=result["error"],
                error_detail=str(result.get("detail", "")),
            ))
            return

        await _publish(PDFParseResponseEvent(
            account_id="system",
            request_id=req.request_id,
            text=result.get("text"),
            tables=list(result.get("tables") or []),
            pages=int(result.get("pages") or 0),
            used_ocr=bool(result.get("used_ocr", False)),
            strategy_used=result.get("strategy_used"),
            decision_reason=result.get("decision_reason"),
            warnings=list(result.get("warnings") or []),
        ))


async def handle(event: BaseEvent, config: Dict[str, Any]) -> None:
    if isinstance(event, PDFParseRequestedEvent):
        await _handle_request(event)
    else:
        log.debug("sphinx ignoring %s", type(event).__name__)
