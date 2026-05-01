"""PDF dispatcher — inspect, route, run.

Cheap inspection step probes the PDF without extracting (page count, has
text layer on the first page, file size, encryption check). The router
picks a strategy. The strategy runs. The result carries
``strategy_used`` and ``decision_reason`` for observability.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from .strategies import (
    PDFInspection,
    StrategyError,
    select_strategy,
)


# Cap mirrors what's in read_pdf.py; we re-declare here so the dispatcher
# can short-circuit before instantiating a strategy.
MAX_PAGES = 500


def inspect(path: Path) -> PDFInspection:
    """Cheap probe. Open + check metadata + first-page text length only.

    Never raises — returns ``open_failed`` field with the error string instead.
    """
    if not path.exists():
        return PDFInspection(path=path, open_failed=f"not_found: {path}")
    try:
        size = path.stat().st_size
    except OSError as e:
        return PDFInspection(path=path, open_failed=f"stat_failed: {e}")
    try:
        import pdfplumber
    except ImportError as e:  # pragma: no cover — pdfplumber is a hard dep
        return PDFInspection(path=path, file_size=size, open_failed=f"pdfplumber missing: {e}")

    try:
        pdf = pdfplumber.open(str(path))
    except Exception as e:
        # Encrypted detection — same shape as read_pdf.py
        candidates = [str(e).lower()]
        for arg in getattr(e, "args", ()):
            candidates.append(type(arg).__name__.lower())
            candidates.append(str(arg).lower())
        if any("password" in c or "encrypted" in c for c in candidates):
            return PDFInspection(path=path, file_size=size, encrypted=True)
        return PDFInspection(path=path, file_size=size, open_failed=str(e) or type(e).__name__)

    try:
        pages = pdf.pages
        page_count = len(pages)
        has_text_layer = False
        text_chars = 0
        has_images = False
        if page_count > 0:
            first = pages[0]
            try:
                t = first.extract_text() or ""
                text_chars = len(t.strip())
                has_text_layer = text_chars > 0
            except Exception:  # noqa: BLE001
                pass
            try:
                has_images = bool(getattr(first, "images", None))
            except Exception:  # noqa: BLE001
                pass
    finally:
        pdf.close()

    return PDFInspection(
        path=path,
        page_count=page_count,
        file_size=size,
        has_text_layer=has_text_layer,
        text_layer_chars=text_chars,
        has_images=has_images,
    )


def dispatch(
    path: Path,
    *,
    mode: str = "both",
    hint: Optional[str] = None,
    prefer: Optional[str] = None,
    options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Inspect the PDF, pick a strategy, run it, return augmented result.

    Returns the existing read_pdf result shape with two added fields:
      ``strategy_used``: name of the strategy that ran
      ``decision_reason``: why the router picked it
    or an ``error`` field for early-exit cases (encrypted, missing, etc.).
    """
    insp = inspect(path)
    if insp.open_failed and not insp.encrypted:
        return {"error": "open_failed", "detail": insp.open_failed}
    if insp.encrypted:
        return {
            "error": "encrypted",
            "detail": "PDF is encrypted; provide a decrypted copy",
        }

    warnings = []
    if insp.page_count > MAX_PAGES:
        warnings.append(
            f"PDF has {insp.page_count} pages; strategies may cap at {MAX_PAGES}"
        )

    try:
        strategy, reason = select_strategy(insp, hint=hint, prefer=prefer)
    except StrategyError as e:
        return {"error": "strategy_selection_failed", "detail": str(e)}

    result = strategy.extract(path, mode=mode, options=options or {})
    if "error" in result:
        return result
    result["strategy_used"] = strategy.name
    result["decision_reason"] = reason
    if warnings:
        existing = list(result.get("warnings") or [])
        result["warnings"] = warnings + existing
    return result
