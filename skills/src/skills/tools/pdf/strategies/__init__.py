"""PDF extraction strategies — pluggable backends behind a Protocol.

Pattern lifted from Hermes's terminal_tool.py: each strategy declares
``available()`` (cheap prereq check), ``inspect()`` (cheap shape probe),
and ``extract(path, mode, options)`` (the work).

Strategies in v0.4: native_text (pdfplumber), ocr_fallback (pdfplumber +
pytesseract), docling (IBM Docling for complex layout). New strategies
(pymupdf, unstructured) drop in as new modules implementing the Protocol.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Protocol


class StrategyError(Exception):
    """Raised on strategy registration / lookup errors."""


@dataclass(frozen=True)
class PDFInspection:
    """Cheap, no-extract probe results used for routing."""

    path: Path
    page_count: int = 0
    file_size: int = 0
    has_text_layer: bool = False
    text_layer_chars: int = 0      # chars on the first probed page
    has_images: bool = False
    encrypted: bool = False
    open_failed: Optional[str] = None  # set if even open failed


class PDFStrategy(Protocol):
    name: str

    def available(self) -> bool:
        """Cheap prerequisite check — is this strategy usable on this host?"""

    def extract(
        self,
        path: Path,
        *,
        mode: str = "both",
        options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Run the extraction. Returns the existing read_pdf shape:
        ``{text, tables, pages, used_ocr, warnings}`` (plus strategy-specific extras)."""


# Registry — strategies self-register at import time.
_STRATEGIES: Dict[str, PDFStrategy] = {}


def register(strategy: PDFStrategy) -> None:
    if strategy.name in _STRATEGIES:
        raise StrategyError(f"strategy {strategy.name!r} already registered")
    _STRATEGIES[strategy.name] = strategy


def get(name: str) -> PDFStrategy:
    if name not in _STRATEGIES:
        raise StrategyError(
            f"unknown strategy {name!r}; known: {sorted(_STRATEGIES)}"
        )
    return _STRATEGIES[name]


def known() -> list[str]:
    return sorted(_STRATEGIES)


def available_strategies() -> list[str]:
    return sorted(name for name, s in _STRATEGIES.items() if s.available())


def select_strategy(
    inspection: PDFInspection,
    *,
    hint: Optional[str] = None,
    prefer: Optional[str] = None,
) -> tuple[PDFStrategy, str]:
    """Return (strategy, reason). Hermes-pattern: explicit prefer wins; else
    inspection + hint determine.

    Decision rules (ordered):
    1. ``prefer`` is set + the requested strategy is available → use it.
    2. ``hint`` mentions tables / formulas / layout AND docling is available
       → docling.
    3. ``has_text_layer`` and the probed page yielded > threshold chars → native_text.
    4. ``has_text_layer`` is False or text was tiny AND ocr_fallback is
       available (tesseract installed) → ocr_fallback.
    5. Default fall-through → native_text (it'll return what little text it can).
    """
    if prefer:
        if prefer not in _STRATEGIES:
            raise StrategyError(f"unknown prefer strategy {prefer!r}")
        s = _STRATEGIES[prefer]
        if not s.available():
            raise StrategyError(
                f"prefer strategy {prefer!r} declared unavailable on this host"
            )
        return s, f"explicit prefer={prefer}"

    hint_lc = (hint or "").lower()
    if any(k in hint_lc for k in ("table", "formula", "layout", "structure", "complex")):
        s = _STRATEGIES.get("docling")
        if s is not None and s.available():
            return s, "hint requests structured layout; docling available"

    if inspection.has_text_layer and inspection.text_layer_chars >= 100:
        return _STRATEGIES["native_text"], "PDF has a text layer; native pdfplumber"

    s = _STRATEGIES.get("ocr_fallback")
    if s is not None and s.available():
        return s, "no/low text layer; OCR fallback (tesseract present)"

    # Last resort.
    return _STRATEGIES["native_text"], "default; no specialized strategy applies"
