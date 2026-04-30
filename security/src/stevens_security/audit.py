"""Append-only audit log writer.

Every request to the Security Agent — accepted, denied, malformed —
produces exactly one JSONL line in::

    <root>/YYYY-MM-DD.jsonl   (UTC date)

One file per UTC day; the writer rolls over automatically on midnight UTC.
Lines are written under an asyncio lock so concurrent writers don't
interleave.

Sensitive parameter values never appear in the clear. The caller is
responsible for picking which fields are sensitive and hashing them via
:func:`hash_param`; the writer only records what it's handed. Default
stance in the capability layer (step 6) will be: everything is sensitive
unless the capability declares otherwise.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import msgpack


@dataclass(frozen=True)
class AuditEntry:
    """One audit line. Times are ISO 8601 UTC strings (caller-formatted)."""

    ts: str
    trace_id: str
    outcome: str  # "ok" | "deny" | "auth_fail" | "notfound" | "rate" | "internal" | "framing_fail" | "blocked"
    latency_ms: int
    caller: Optional[str] = None
    capability: Optional[str] = None
    account_id: Optional[str] = None
    error_code: Optional[str] = None
    param_hashes: Dict[str, str] = field(default_factory=dict)
    param_values: Dict[str, Any] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)
    # Approval-gating metadata.
    # `approval_via` is "standing/<sa_id>" or "per_call/<req_id>" when the call
    # was authorized through an approval. Absent for calls that didn't require
    # approval. See docs/protocols/approvals.md.
    approval_via: Optional[str] = None
    # On `outcome="blocked"`, the queued per-call request id Sol will decide on.
    approval_request_id: Optional[str] = None


def hash_param(value: Any) -> str:
    """Return the SHA-256 hex digest of ``value``.

    Uses msgpack with ``use_bin_type=True`` for deterministic bytes across
    runs. Strings, ints, bools, None, bytes, lists, and dicts (with string
    keys) all hash consistently. Dict key order is stabilized by sorting
    keys lexicographically before packing.

    This is an audit-log fingerprint, not a canonical encoding for
    signing — floats and non-string keys are accepted (we trade strictness
    for "works on any plausible param value").
    """
    encoded = msgpack.packb(_stabilize(value), use_bin_type=True, default=_fallback)
    return hashlib.sha256(encoded).hexdigest()


def _stabilize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _stabilize(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_stabilize(v) for v in value]
    return value


def _fallback(obj: Any) -> Any:
    # Msgpack default — anything exotic becomes its repr.
    return repr(obj)


class AuditWriter:
    """Append-only JSONL writer with asyncio-locked writes and daily rollover."""

    def __init__(
        self,
        root_dir: Path,
        *,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._root = Path(root_dir)
        self._root.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _path_for_now(self) -> Path:
        now = self._clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        else:
            now = now.astimezone(timezone.utc)
        return self._root / f"{now.strftime('%Y-%m-%d')}.jsonl"

    async def log(self, entry: AuditEntry) -> None:
        """Append one audit entry as JSONL."""
        line = json.dumps(
            asdict(entry),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        async with self._lock:
            path = self._path_for_now()
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            if path.stat().st_mode & 0o777 != 0o600:
                try:
                    path.chmod(0o600)
                except OSError:
                    pass
