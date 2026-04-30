"""Shared fixtures for skills tests — synthesizes test PDFs on demand."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable, List

import pytest


_FIXTURES = Path(__file__).parent / "fixtures"


def _gen_text_pdf(out: Path, lines: List[str]) -> None:
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(out))
    y = 800
    for line in lines:
        c.drawString(50, y, line)
        y -= 20
    c.showPage()
    c.save()


def _gen_table_pdf(out: Path, *, rows_total: int = 30, cols: int = 3) -> None:
    """Produce a PDF whose body is a single tall table that spans pages.

    pdfplumber detects tables based on visual cell boundaries — we draw
    explicit rectangles so it sees a real grid.
    """
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(out))
    page_w, page_h = 612, 792
    cell_h = 30
    cell_w = (page_w - 100) / cols
    rows_per_page = (page_h - 100) // cell_h
    rows_drawn = 0
    page_first_row = 0  # which logical row index starts this page

    while rows_drawn < rows_total:
        rows_this_page = min(rows_per_page, rows_total - rows_drawn)
        # Draw header on FIRST page only.
        is_first_page = page_first_row == 0
        if is_first_page:
            for j in range(cols):
                x = 50 + j * cell_w
                y = page_h - 80
                c.rect(x, y, cell_w, cell_h)
                c.drawString(x + 5, y + 10, f"COL{j}")
            y_start = page_h - 80 - cell_h
            rows_left_to_draw = rows_this_page - 1
            row_index_offset = 1
        else:
            y_start = page_h - 80
            rows_left_to_draw = rows_this_page
            row_index_offset = 0

        for i in range(rows_left_to_draw):
            for j in range(cols):
                x = 50 + j * cell_w
                y = y_start - i * cell_h
                c.rect(x, y, cell_w, cell_h)
                c.drawString(
                    x + 5,
                    y + 10,
                    f"r{rows_drawn + row_index_offset + i}c{j}",
                )

        rows_drawn += rows_this_page
        page_first_row = rows_drawn
        if rows_drawn < rows_total:
            c.showPage()
    c.save()


def _gen_encrypted_pdf(out: Path) -> None:
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(out), encrypt="hunter2")
    c.drawString(50, 800, "secret content")
    c.showPage()
    c.save()


def _gen_scanned_pdf(out: Path, text: str = "Scanned text here") -> None:
    """A 'scanned' PDF — image of text, no embedded text layer."""
    from PIL import Image, ImageDraw, ImageFont
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader

    img = Image.new("RGB", (1000, 200), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 32)
    except OSError:
        font = ImageFont.load_default()
    draw.text((20, 80), text, fill="black", font=font)

    import io as _io

    buf = _io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    c = canvas.Canvas(str(out))
    c.drawImage(ImageReader(buf), 50, 500, width=500, height=100)
    c.showPage()
    c.save()


@pytest.fixture(scope="session")
def fixtures_dir(tmp_path_factory) -> Path:
    """Create the four fixture PDFs once per test session."""
    d = tmp_path_factory.mktemp("pdf_fixtures")
    _gen_text_pdf(d / "text_only.pdf", ["Hello world.", "Second line of text.", "Third."])
    _gen_table_pdf(d / "two_page_table.pdf", rows_total=40)  # plenty to span pages
    _gen_encrypted_pdf(d / "encrypted.pdf")
    _gen_scanned_pdf(d / "scanned.pdf")
    return d
