"""PDF reader regression — runs read_pdf against a curated corpus.

Declared manual: needs a real running Enkidu, a real Brave API key (for
search-related future tests; not yet used), and outbound network access.
On first run, downloads the corpus; subsequent runs use the local copy.

Usage::

    uv run python scripts/test_pdf_reader_on_corpus.py
    uv run python scripts/test_pdf_reader_on_corpus.py --cache-dir /tmp/pdf-corpus
    uv run python scripts/test_pdf_reader_on_corpus.py --urls https://x.com/y.pdf,...

Each entry in the report:
- name: derived from URL
- pages: total pages parsed
- used_ocr: whether OCR fallback fired
- table_count: number of tables extracted
- text_chars: length of extracted text
- warnings: list from read_pdf

Returns 0 if all PDFs parse without erroring (encrypted is acceptable; OCR
skipped without tesseract is acceptable). Returns 1 on download or parse
failures.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional


# Curated initial corpus. Selected for diverse shapes:
DEFAULT_URLS = [
    "https://arxiv.org/pdf/1706.03762.pdf",                     # Attention Is All You Need (text-heavy, multi-column)
    "https://www.irs.gov/pub/irs-pdf/fw9.pdf",                  # IRS W-9 (forms + tables)
    "https://bitcoin.org/bitcoin.pdf",                           # Bitcoin whitepaper (small, text)
    "https://www.un.org/sites/un2.un.org/files/udhr.pdf",       # UDHR (long-prose)
]


def _slug_for(url: str) -> str:
    h = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    leaf = url.rstrip("/").split("/")[-1] or "pdf"
    if not leaf.endswith(".pdf"):
        leaf = leaf + ".pdf"
    return f"{h}-{leaf}"


async def _fetch_via_skill(url: str) -> bytes:
    """Use the synchronous web_fetch skill (which calls Enkidu directly)."""
    from skills.tools.web.fetch import _client

    result = await _client().call(
        "network.fetch", {"url": url, "follow_redirects": True},
    )
    if "error" in result:
        raise RuntimeError(f"fetch failed: {result['error']}: {result.get('detail', '')}")
    body = result.get("body", b"")
    if not isinstance(body, (bytes, bytearray)):
        raise RuntimeError(f"unexpected body type: {type(body).__name__}")
    return bytes(body)


def _ensure_local(url: str, cache_dir: Path) -> Path:
    """Return a local path with the PDF bytes, downloading if needed."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / _slug_for(url)
    if target.exists() and target.stat().st_size > 0:
        return target
    print(f"  → fetching {url}...")
    data = asyncio.run(_fetch_via_skill(url))
    target.write_bytes(data)
    print(f"  → wrote {len(data)} bytes to {target}")
    return target


def _run_read_pdf(path: Path) -> Dict[str, Any]:
    from skills.tools.pdf.read_pdf import _read_pdf

    return _read_pdf(str(path))


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Run read_pdf against a corpus of PDFs.")
    p.add_argument(
        "--urls", default=None,
        help="comma-separated URLs (defaults to the built-in seed list)",
    )
    p.add_argument(
        "--cache-dir", default=None,
        help="directory to cache downloaded PDFs (default: ./.pdf-corpus/)",
    )
    args = p.parse_args(argv)

    urls = (
        [u.strip() for u in args.urls.split(",") if u.strip()]
        if args.urls else list(DEFAULT_URLS)
    )
    cache_dir = Path(args.cache_dir or ".pdf-corpus").resolve()

    failed = 0
    print(f"corpus: {len(urls)} URL(s); cache: {cache_dir}\n")
    for url in urls:
        print(f"[{url}]")
        try:
            local = _ensure_local(url, cache_dir)
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ download failed: {e}")
            failed += 1
            continue
        try:
            result = _run_read_pdf(local)
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ read_pdf raised: {e}")
            failed += 1
            continue

        if "error" in result:
            err = result["error"]
            if err == "encrypted":
                print(f"  · encrypted (acceptable, structured error)")
            else:
                print(f"  ✗ read_pdf error: {result}")
                failed += 1
                continue
        else:
            text_chars = len(result.get("text", "") or "")
            tables = result.get("tables") or []
            print(
                f"  ✓ pages={result['pages']:<4} used_ocr={result['used_ocr']!s:<5} "
                f"tables={len(tables):<3} text_chars={text_chars}"
            )
            for w in result.get("warnings") or []:
                print(f"      warning: {w}")
        print()

    print(f"summary: {len(urls) - failed} ok, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
