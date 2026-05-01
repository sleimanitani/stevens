"""native_text strategy — pdfplumber, no OCR.

Fast path for PDFs with embedded text layers. The original ``_read_pdf``
implementation in ``read_pdf.py`` is the basis.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from . import register


_MAX_PAGES = 500


class NativeTextStrategy:
    name = "native_text"

    def available(self) -> bool:
        try:
            import pdfplumber  # noqa: F401
            return True
        except ImportError:
            return False

    def extract(
        self,
        path: Path,
        *,
        mode: str = "both",
        options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        # Re-use the existing _read_pdf — same shape, no OCR fallback (caller
        # routes to ocr_fallback strategy for that). Force ocr_fallback=False
        # so we don't double-OCR if tesseract is also installed.
        from ..read_pdf import _read_pdf

        return _read_pdf(str(path), mode=mode, ocr_fallback=False)


register(NativeTextStrategy())
