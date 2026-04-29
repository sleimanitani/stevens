"""Lightweight status — `stevens status`.

Always returns 0. Just a glance: sealed-store state, Enkidu running or
not, registered agents, last 5 audit lines. For "is anything wrong?" use
``stevens doctor`` instead.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import yaml

from . import audit_tail


def _store_state(secrets_root: Path) -> str:
    if not (secrets_root / "master.info").exists():
        return "not initialized"
    return "initialized"


def _enkidu_state(socket_path: str) -> str:
    if Path(socket_path).exists():
        return f"socket present at {socket_path}"
    return "not running"


def _agent_names(agents_yaml: Path) -> List[str]:
    if not agents_yaml.exists():
        return []
    raw = yaml.safe_load(agents_yaml.read_text()) or {}
    return [
        e["name"]
        for e in (raw.get("agents") or [])
        if isinstance(e, dict) and "name" in e
    ]


def _format_recent_audit(audit_dir: Path, *, n: int) -> List[str]:
    path = audit_tail.current_log_file(audit_dir)
    lines = audit_tail.tail_lines(path, n)
    return [audit_tail.format_line(raw) for raw in lines]


def render_status(
    *,
    secrets_root: Path,
    socket_path: str,
    agents_yaml: Path,
    audit_dir: Path,
) -> str:
    out = []
    out.append(f"sealed store:    {_store_state(secrets_root)}  ({secrets_root})")
    out.append(f"Enkidu:          {_enkidu_state(socket_path)}")
    agents = _agent_names(agents_yaml)
    if agents:
        out.append(f"agents ({len(agents)}):    {', '.join(agents)}")
    else:
        out.append("agents:          (none registered)")
    out.append("")
    out.append("recent audit (last 5):")
    recent = _format_recent_audit(audit_dir, n=5)
    if recent:
        for line in recent:
            out.append(f"  {line}")
    else:
        out.append("  (no audit lines yet)")
    return "\n".join(out)
