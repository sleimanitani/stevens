"""ocr_fallback strategy — pdfplumber + pytesseract.

For scanned PDFs with no text layer. Available iff the ``tesseract``
binary is on PATH.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict, Optional

from . import register


class OcrFallbackStrategy:
    name = "ocr_fallback"

    def available(self) -> bool:
        return shutil.which("tesseract") is not None

    def extract(
        self,
        path: Path,
        *,
        mode: str = "both",
        options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        from ..read_pdf import _read_pdf

        # ocr_fallback=True forces OCR when text-extraction yields too little.
        return _read_pdf(str(path), mode=mode, ocr_fallback=True)


register(OcrFallbackStrategy())
