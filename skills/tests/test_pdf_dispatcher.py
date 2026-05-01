"""Tests for the PDF dispatcher — inspect + route."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from skills.tools.pdf import (  # registers strategies via __init__
    dispatcher,
)
from skills.tools.pdf.strategies import (
    PDFInspection,
    StrategyError,
    available_strategies,
    get,
    known,
    select_strategy,
)


# --- registry ---


def test_known_strategies_includes_three():
    assert {"native_text", "ocr_fallback", "docling"}.issubset(set(known()))


def test_native_text_always_available():
    assert get("native_text").available() is True


# --- selector ---


def _insp(**kwargs) -> PDFInspection:
    defaults = dict(path=Path("/tmp/x.pdf"), page_count=1, file_size=1024,
                    has_text_layer=True, text_layer_chars=500, has_images=False)
    defaults.update(kwargs)
    return PDFInspection(**defaults)


def test_text_layer_routes_to_native_text():
    s, reason = select_strategy(_insp(has_text_layer=True, text_layer_chars=500))
    assert s.name == "native_text"
    assert "text layer" in reason.lower()


def test_no_text_layer_routes_to_ocr_when_available():
    if not get("ocr_fallback").available():
        pytest.skip("tesseract not installed; OCR strategy unavailable")
    s, reason = select_strategy(_insp(has_text_layer=False, text_layer_chars=0))
    assert s.name == "ocr_fallback"


def test_no_text_layer_falls_through_when_ocr_unavailable(monkeypatch):
    monkeypatch.setattr(
        "skills.tools.pdf.strategies.ocr_fallback.OcrFallbackStrategy.available",
        lambda self: False,
    )
    s, reason = select_strategy(_insp(has_text_layer=False, text_layer_chars=0))
    assert s.name == "native_text"  # fallback


def test_hint_bias_to_docling_when_available(monkeypatch):
    # Pretend docling is installed.
    monkeypatch.setattr(
        "skills.tools.pdf.strategies.docling.DoclingStrategy.available",
        lambda self: True,
    )
    s, reason = select_strategy(
        _insp(has_text_layer=True, text_layer_chars=500),
        hint="extract the tables on every page",
    )
    assert s.name == "docling"
    assert "structured layout" in reason.lower() or "docling" in reason.lower()


def test_hint_bias_skipped_when_docling_unavailable():
    # docling.available() returns False (default — no package installed).
    s, reason = select_strategy(
        _insp(has_text_layer=True, text_layer_chars=500),
        hint="extract tables",
    )
    # Falls through to native_text.
    assert s.name == "native_text"


def test_explicit_prefer_wins():
    s, reason = select_strategy(
        _insp(has_text_layer=True, text_layer_chars=500),
        prefer="native_text",
    )
    assert s.name == "native_text"
    assert "explicit prefer" in reason


def test_prefer_unknown_raises():
    with pytest.raises(StrategyError, match="unknown prefer"):
        select_strategy(_insp(), prefer="magic")


# --- dispatcher integration ---


def test_dispatch_text_only_pdf(fixtures_dir: Path):
    out = dispatcher.dispatch(fixtures_dir / "text_only.pdf")
    assert "error" not in out
    assert out["strategy_used"] == "native_text"
    assert "Hello world" in out["text"]


def test_dispatch_encrypted_pdf_returns_structured_error(fixtures_dir: Path):
    out = dispatcher.dispatch(fixtures_dir / "encrypted.pdf")
    assert out.get("error") == "encrypted"


def test_dispatch_missing_pdf(tmp_path: Path):
    out = dispatcher.dispatch(tmp_path / "missing.pdf")
    assert out["error"] == "open_failed"


def test_dispatch_explicit_prefer(fixtures_dir: Path):
    out = dispatcher.dispatch(fixtures_dir / "text_only.pdf", prefer="native_text")
    assert out["strategy_used"] == "native_text"
    assert "explicit prefer" in out["decision_reason"]
