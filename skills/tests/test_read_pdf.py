"""Tests for the PDF reader — the v0.2-skills milestone acceptance gate.

Four cases:
  1. Text-only PDF → text extracted, no tables
  2. PDF with table spanning pages → ONE merged table
  3. Encrypted PDF → structured error
  4. Scanned PDF → OCR fallback (skipped if tesseract not installed)
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from skills.tools.pdf.read_pdf import (
    TOOL_METADATA,
    _looks_like_header_row,
    _merge_cross_page_tables,
    _read_pdf,
    build_tool,
)


# --- acceptance gate cases ---


def test_text_only_pdf(fixtures_dir: Path) -> None:
    result = _read_pdf(str(fixtures_dir / "text_only.pdf"))
    assert "error" not in result
    assert "Hello world" in result["text"]
    assert result["pages"] == 1
    assert result["used_ocr"] is False


def test_cross_page_table_merges_into_one(fixtures_dir: Path) -> None:
    result = _read_pdf(str(fixtures_dir / "two_page_table.pdf"), mode="tables")
    assert "error" not in result
    assert result["pages"] >= 2  # confirm fixture really did span pages
    tables = result["tables"]
    # The acceptance criterion: ONE table out, not two.
    assert len(tables) == 1, f"expected 1 merged table, got {len(tables)}"
    # Body row count should be (rows_total - 1 header) — 39 from the fixture.
    # Allow a small amount of slack for pdfplumber row-detection variance.
    assert len(tables[0]) >= 30


def test_encrypted_pdf_returns_structured_error(fixtures_dir: Path) -> None:
    result = _read_pdf(str(fixtures_dir / "encrypted.pdf"))
    assert result.get("error") == "encrypted"


@pytest.mark.skipif(
    shutil.which("tesseract") is None,
    reason="tesseract binary not installed; OCR fallback can't be exercised",
)
def test_scanned_pdf_uses_ocr(fixtures_dir: Path) -> None:
    result = _read_pdf(str(fixtures_dir / "scanned.pdf"))
    assert "error" not in result
    assert result["used_ocr"] is True
    # OCR is fuzzy — accept any of the words from the fixture.
    text_lower = result["text"].lower()
    assert any(w in text_lower for w in ["scanned", "text", "here"])


# --- non-gate / boundary cases ---


def test_missing_pdf_returns_structured_error(tmp_path: Path) -> None:
    result = _read_pdf(str(tmp_path / "does_not_exist.pdf"))
    assert result["error"] == "not_found"


def test_scanned_pdf_without_ocr_falls_back_gracefully(
    fixtures_dir: Path, monkeypatch
) -> None:
    """When tesseract is absent, OCR is skipped with a warning, not crash."""
    import skills.tools.pdf.read_pdf as mod

    monkeypatch.setattr(mod, "_ocr_available", lambda: False)
    result = _read_pdf(str(fixtures_dir / "scanned.pdf"))
    assert "error" not in result
    assert result["used_ocr"] is False
    assert any("OCR fallback skipped" in w for w in result["warnings"])


def test_build_tool_returns_structured_tool() -> None:
    tool = build_tool()
    assert tool.name == "read_pdf"
    assert "PDF" in tool.description


def test_metadata_declares_shared_read_only() -> None:
    assert TOOL_METADATA["scope"] == "shared"
    assert TOOL_METADATA["safety_class"] == "read-only"


# --- merge logic unit tests (no PDF needed) ---


def test_merge_combines_continuation_table() -> None:
    pages = [
        [[["A", "B", "C"], ["1", "2", "3"]]],  # page 1: header + 1 row
        [[["4", "5", "6"], ["7", "8", "9"]]],  # page 2: 2 more body rows
    ]
    merged = _merge_cross_page_tables(pages)
    assert len(merged) == 1
    assert len(merged[0]) == 4  # header + 3 body rows


def test_merge_keeps_separate_when_columns_differ() -> None:
    pages = [
        [[["A", "B"], ["1", "2"]]],
        [[["X", "Y", "Z"], ["a", "b", "c"]]],
    ]
    merged = _merge_cross_page_tables(pages)
    assert len(merged) == 2


def test_merge_keeps_separate_when_continuation_looks_like_header() -> None:
    pages = [
        [[["A", "B", "C"], ["1", "2", "3"]]],
        [[["NAME", "AMOUNT", "DATE"], ["x", "y", "z"]]],  # all-caps = header
    ]
    merged = _merge_cross_page_tables(pages)
    assert len(merged) == 2


def test_looks_like_header_row_detects_caps() -> None:
    assert _looks_like_header_row(["NAME", "AMOUNT", "DATE"])
    assert not _looks_like_header_row(["1", "2", "3"])
    assert _looks_like_header_row(["name", "amount"])  # marker words
