"""Robust PDF reader — the canonical first shared tool.

Handles:
- text-based PDFs (pdfplumber)
- scanned PDFs (OCR via pytesseract — falls back automatically when
  text extraction yields fewer than ``ocr_threshold_chars`` characters)
- tables that span multiple pages (merged via ``_merge_cross_page_tables``)

Does NOT handle:
- encrypted PDFs (returns a structured error rather than crashing)
- PDFs over ``MAX_PAGES`` (returns a partial-with-warning rather than
  blowing the process — agents that need more should ask for it)

Returns: ``{"text": str, "tables": List[List[List[str]]], "pages": int,
             "used_ocr": bool, "warnings": List[str]}``.

Cross-page table merge rule (deliberately conservative):
- Page N's last table and page N+1's first table merge IF they have the
  same column count > 1 AND page N+1's first row's cells don't look like
  headers (no all-caps cells; no obvious header markers).
- Better to leave two tables separate than to mistakenly merge unrelated
  ones — operators can reconcile by hand.

OCR fallback requires the ``tesseract`` binary at the OS level. If
absent, OCR is skipped with a warning rather than failing — agents
that genuinely need OCR will see ``used_ocr=False`` and zero text on a
scanned PDF and can decide what to do.
"""

from __future__ import annotations

import io
import logging
import shutil
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)


TOOL_METADATA = {
    "id": "pdf.read_pdf",
    "version": "1.0.0",
    "created_by": "v0.2-skills",
    "approved_by": "sol",
    "approved_at": "2026-04-30",
    "scope": "shared",
    "allowed_agents": None,
    "external_deps": ["pdfplumber", "pytesseract", "pillow"],
    "external_binaries": ["tesseract"],  # optional — OCR fallback only
    "safety_class": "read-only",
}


MAX_PAGES = 500
DEFAULT_OCR_THRESHOLD = 100


class ReadPDFInput(BaseModel):
    path: str = Field(description="Absolute path to the PDF file")
    mode: Literal["text", "tables", "both"] = Field(
        default="both",
        description="What to extract: text only, tables only, or both",
    )
    ocr_fallback: bool = Field(
        default=True,
        description="Fall back to OCR if text extraction yields very few characters",
    )
    ocr_threshold_chars: int = Field(
        default=DEFAULT_OCR_THRESHOLD,
        description="Char count below which OCR fallback triggers",
    )
    prefer_strategy: Optional[str] = Field(
        default=None,
        description=(
            "Force a specific strategy: native_text / ocr_fallback / docling. "
            "If omitted, the dispatcher inspects the PDF and picks one."
        ),
    )
    request_hint: Optional[str] = Field(
        default=None,
        description=(
            "Free-text description of what you're trying to get. Words like "
            "'tables', 'formulas', 'layout', 'structure', 'complex' bias the "
            "router toward Docling when available."
        ),
    )


def _looks_like_header_row(row: List[Optional[str]]) -> bool:
    """Heuristic for table header detection in cross-page-merge logic."""
    cells = [c for c in row if c]
    if not cells:
        return False
    all_caps = sum(1 for c in cells if c.isupper() and len(c) > 1)
    if all_caps >= max(1, len(cells) // 2):
        return True
    # Common header markers.
    markers = {"name", "amount", "date", "total", "id", "type", "description"}
    lower_set = {c.strip().lower() for c in cells}
    return bool(lower_set & markers)


def _merge_cross_page_tables(
    tables_per_page: List[List[List[List[Optional[str]]]]],
) -> List[List[List[Optional[str]]]]:
    """Merge tables that visually span page boundaries.

    Input: list per page, each containing zero or more tables; each
    table is a list of rows; each row is a list of cell strings (or
    None).

    Output: one flat list of tables, with cross-page continuations
    merged.
    """
    flat: List[List[List[Optional[str]]]] = []
    for page_tables in tables_per_page:
        for tbl in page_tables:
            if not tbl:
                continue
            # Try to merge with the previous table if conditions hold.
            if (
                flat
                and len(flat[-1][0]) == len(tbl[0])
                and len(tbl[0]) > 1
                and not _looks_like_header_row(tbl[0])
                # Previous table came from a *different* page — only the
                # *first* table of a new page can be a continuation.
                and tbl is page_tables[0]
            ):
                flat[-1].extend(tbl)
            else:
                flat.append(list(tbl))
    return flat


def _ocr_available() -> bool:
    """True if the tesseract binary is on PATH."""
    return shutil.which("tesseract") is not None


def _read_pdf(
    path: str,
    mode: str = "both",
    ocr_fallback: bool = True,
    ocr_threshold_chars: int = DEFAULT_OCR_THRESHOLD,
) -> Dict[str, Any]:
    import pdfplumber

    pdf_path = Path(path)
    if not pdf_path.exists():
        return {"error": "not_found", "detail": f"no PDF at {path}"}

    warnings: List[str] = []
    text_parts: List[str] = []
    tables_per_page: List[List[List[List[Optional[str]]]]] = []
    used_ocr = False

    try:
        pdf = pdfplumber.open(str(pdf_path))
    except Exception as e:  # pdfminer raises a variety of types on bad input
        # pdfplumber wraps the underlying pdfminer exception in
        # PdfminerException(...) where the inner exception comes through
        # as the first arg. Check both str(e) and the wrapped args.
        candidates = [str(e).lower()]
        for arg in getattr(e, "args", ()):
            candidates.append(type(arg).__name__.lower())
            candidates.append(str(arg).lower())
        if any("password" in c or "encrypted" in c for c in candidates):
            return {
                "error": "encrypted",
                "detail": "PDF is encrypted; provide a decrypted copy",
            }
        return {"error": "open_failed", "detail": str(e) or type(e).__name__}

    try:
        page_count = len(pdf.pages)
        if page_count > MAX_PAGES:
            warnings.append(
                f"PDF has {page_count} pages; reading only the first {MAX_PAGES}"
            )
            pages = pdf.pages[:MAX_PAGES]
        else:
            pages = pdf.pages

        for page in pages:
            if mode in ("text", "both"):
                t = page.extract_text() or ""
                text_parts.append(t)
            if mode in ("tables", "both"):
                page_tables = page.extract_tables() or []
                tables_per_page.append(page_tables)
            else:
                tables_per_page.append([])

        full_text = "\n".join(text_parts)
        if (
            mode in ("text", "both")
            and ocr_fallback
            and len(full_text.strip()) < ocr_threshold_chars
        ):
            if _ocr_available():
                ocr_text = _ocr_pdf(pdf)
                if ocr_text.strip():
                    full_text = ocr_text
                    used_ocr = True
            else:
                warnings.append(
                    "text extraction returned <100 chars and tesseract is "
                    "not installed — OCR fallback skipped"
                )

        merged_tables = _merge_cross_page_tables(tables_per_page) if mode in ("tables", "both") else []
    finally:
        pdf.close()

    return {
        "text": full_text,
        "tables": merged_tables,
        "pages": page_count,
        "used_ocr": used_ocr,
        "warnings": warnings,
    }


def _ocr_pdf(pdf) -> str:
    """OCR every page of a pdfplumber-opened PDF. Best-effort."""
    import pytesseract

    out: List[str] = []
    for page in pdf.pages:
        # pdfplumber's Page.to_image() returns a PageImage backed by Pillow.
        try:
            img = page.to_image(resolution=200).original
            text = pytesseract.image_to_string(img)
        except Exception as e:
            log.warning("OCR failed on page: %s", e)
            text = ""
        out.append(text)
    return "\n".join(out)


def _read_pdf_via_dispatcher(
    path: str,
    mode: str = "both",
    ocr_fallback: bool = True,                     # legacy; ignored by dispatcher (strategies decide)
    ocr_threshold_chars: int = DEFAULT_OCR_THRESHOLD,  # legacy; ignored
    prefer_strategy: Optional[str] = None,
    request_hint: Optional[str] = None,
) -> Dict[str, Any]:
    """Tool entry point — routes through the strategy dispatcher.

    Backwards-compatible: the legacy ``ocr_fallback`` / ``ocr_threshold_chars``
    args are accepted (so existing callers don't break) but ignored —
    the strategy router makes the OCR decision now via
    ``prefer_strategy=ocr_fallback`` or by inspecting the PDF.
    """
    from .dispatcher import dispatch
    from pathlib import Path as _Path

    return dispatch(
        _Path(path),
        mode=mode,
        hint=request_hint,
        prefer=prefer_strategy,
    )


def build_tool() -> StructuredTool:
    return StructuredTool.from_function(
        func=_read_pdf_via_dispatcher,
        name="read_pdf",
        description=(
            "Extract text and tables from a PDF. The dispatcher inspects the "
            "PDF and picks the right strategy (native pdfplumber for text-layer "
            "PDFs, OCR fallback for scanned, IBM Docling for complex layouts "
            "when installed). Returns {text, tables, pages, used_ocr, warnings, "
            "strategy_used, decision_reason}. Encrypted PDFs return a structured "
            "error. Use ``prefer_strategy`` to force one; ``request_hint`` to "
            "bias the router."
        ),
        args_schema=ReadPDFInput,
    )
