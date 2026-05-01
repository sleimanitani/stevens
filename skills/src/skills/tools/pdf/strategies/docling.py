"""docling strategy — IBM Docling, ML-based document understanding.

Lazy-loaded: the ``docling`` package and its underlying models are heavy;
we don't import at module load. ``available()`` does a thin import check;
``extract()`` does the real load on first call.

Output normalization: Docling returns its own document object; we project
to the existing read_pdf shape (text + tables + pages + warnings) so the
dispatcher's contract is uniform.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from . import register


class DoclingStrategy:
    name = "docling"

    def __init__(self) -> None:
        self._converter = None  # lazy

    def available(self) -> bool:
        try:
            import docling  # noqa: F401
            return True
        except ImportError:
            return False

    def _get_converter(self):
        if self._converter is None:
            from docling.document_converter import DocumentConverter

            self._converter = DocumentConverter()
        return self._converter

    def extract(
        self,
        path: Path,
        *,
        mode: str = "both",
        options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        try:
            converter = self._get_converter()
        except Exception as e:  # noqa: BLE001
            return {"error": "docling_init_failed", "detail": str(e)}
        try:
            result = converter.convert(str(path))
        except Exception as e:  # noqa: BLE001
            return {"error": "docling_convert_failed", "detail": str(e)}

        # Project to the read_pdf shape.
        doc = getattr(result, "document", None)
        if doc is None:
            return {"error": "docling_no_document", "detail": "no document on result"}
        text = ""
        try:
            text = doc.export_to_markdown()
        except Exception:  # noqa: BLE001
            try:
                text = doc.export_to_text()
            except Exception:  # noqa: BLE001
                text = ""
        # Tables: Docling tracks them in document.tables; we serialize each
        # as a list-of-rows for shape compatibility with pdfplumber's output.
        tables: List[List[List[Optional[str]]]] = []
        try:
            for tbl in getattr(doc, "tables", []) or []:
                rows = []
                # Docling table API differs by version; try multiple paths.
                if hasattr(tbl, "data") and hasattr(tbl.data, "table_cells"):
                    # Group cells by row.
                    by_row: Dict[int, Dict[int, str]] = {}
                    for cell in tbl.data.table_cells:
                        by_row.setdefault(cell.start_row_offset_idx, {})[cell.start_col_offset_idx] = cell.text
                    for row_idx in sorted(by_row):
                        cells_in_row = by_row[row_idx]
                        max_col = max(cells_in_row) if cells_in_row else -1
                        rows.append([cells_in_row.get(c, "") for c in range(max_col + 1)])
                tables.append(rows)
        except Exception:  # noqa: BLE001
            pass
        page_count = 0
        try:
            page_count = len(getattr(doc, "pages", None) or [])
        except Exception:  # noqa: BLE001
            pass

        return {
            "text": text,
            "tables": tables,
            "pages": page_count,
            "used_ocr": False,   # Docling has its own OCR; reflect "used_ocr" only when our shim does it
            "warnings": [],
        }


register(DoclingStrategy())
