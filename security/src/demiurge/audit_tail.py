"""Audit log tailer — `demiurge audit tail [-f]`.

Reads the JSONL audit log written by ``audit.AuditWriter`` and prints
either a one-shot last-N lines or a follow stream (``-f``).

The audit dir contains one file per UTC date (``YYYY-MM-DD.jsonl``). In
follow mode we transparently switch to the new date file when the day
rolls over.
"""

from __future__ import annotations

import json
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Optional, TextIO


def today_utc() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")


def current_log_file(audit_dir: Path) -> Path:
    return audit_dir / f"{today_utc()}.jsonl"


def format_line(raw: str, *, raw_mode: bool = False) -> str:
    """Format one JSONL audit line for display.

    raw_mode = pass the JSON through unchanged (for piping to jq).
    Otherwise: ``<ts> <caller>/<capability> account=<acct> <outcome> <latency_ms>ms``.
    """
    if raw_mode:
        return raw.rstrip("\n")
    try:
        rec = json.loads(raw)
    except json.JSONDecodeError:
        return f"(unparseable line) {raw.rstrip()}"
    ts = rec.get("ts", "?")
    caller = rec.get("caller") or "-"
    cap = rec.get("capability") or "-"
    account = rec.get("account_id") or "-"
    outcome = rec.get("outcome", "?")
    latency = rec.get("latency_ms", "?")
    err = rec.get("error_code")
    err_part = f" err={err}" if err else ""
    return f"{ts}  {caller}/{cap}  account={account}  {outcome} {latency}ms{err_part}"


def tail_lines(path: Path, n: int) -> Iterable[str]:
    """Return the last ``n`` lines of ``path`` (or [] if missing)."""
    if not path.exists():
        return []
    # For audit logs, files are typically small; reading whole file is fine.
    # If they grow huge, swap to a backwards-seek implementation.
    with path.open("r") as f:
        buf: deque[str] = deque(f, maxlen=n)
    return list(buf)


def follow(
    audit_dir: Path,
    *,
    out: TextIO,
    raw_mode: bool = False,
    sleep_s: float = 1.0,
    _max_iterations: Optional[int] = None,
) -> None:
    """Follow the current day's log; switch files at UTC date rollover.

    Blocks indefinitely unless ``_max_iterations`` is given (test seam).
    """
    current_path = current_log_file(audit_dir)
    pos = current_path.stat().st_size if current_path.exists() else 0
    iterations = 0
    while True:
        # Detect date rollover: the path we should be tailing changed.
        target = current_log_file(audit_dir)
        if target != current_path:
            current_path = target
            pos = 0

        if current_path.exists():
            with current_path.open("r") as f:
                f.seek(pos)
                chunk = f.read()
                pos = f.tell()
            if chunk:
                for line in chunk.splitlines():
                    out.write(format_line(line, raw_mode=raw_mode) + "\n")
                out.flush()

        iterations += 1
        if _max_iterations is not None and iterations >= _max_iterations:
            return
        time.sleep(sleep_s)


def print_tail(
    audit_dir: Path, *, n: int, out: TextIO, raw_mode: bool = False
) -> None:
    """Print the last ``n`` lines of today's audit log to ``out``."""
    path = current_log_file(audit_dir)
    lines = tail_lines(path, n)
    if not lines:
        out.write(f"(no audit lines yet at {path})\n")
        return
    for raw in lines:
        out.write(format_line(raw, raw_mode=raw_mode) + "\n")
