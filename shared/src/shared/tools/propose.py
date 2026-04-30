"""``propose_skill`` — every agent's path for nominating a new skill.

Writes a row to ``skill_proposals`` (migration 004) and drops the body in
``skills/proposed/<kind>/`` for human review. The agent does NOT get to
use its own proposal — it sits at status=pending until Sol approves via
``scripts/review_skills.py``.

This module is sync at the Python boundary (LangChain tools want sync
callables) but the DB write happens through the async ``shared.db``
connection. Bridge via ``asyncio.run`` — proposals are rare and one-shot,
so the loop-setup cost is fine.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

log = logging.getLogger(__name__)


_VALID_KINDS = ("tool", "playbook")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


class ProposeSkillError(Exception):
    """Raised on validation errors before DB/filesystem writes."""


@dataclass(frozen=True)
class ProposalResult:
    proposal_id: uuid.UUID
    body_path: str  # path under skills/proposed/, relative to repo root


def _slugify(s: str) -> str:
    s = s.lower().strip()
    s = _SLUG_RE.sub("-", s).strip("-")
    return s[:60] or "untitled"


def _proposed_dir() -> Path:
    """Repo-root-relative ``skills/proposed/``. Override via env for tests."""
    env = os.environ.get("STEVENS_SKILLS_PROPOSED")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "skills" / "proposed"


def propose_skill(
    *,
    kind: Literal["tool", "playbook"],
    title: str,
    body: str,
    proposing_agent: str,
    rationale: Optional[str] = None,
    originating_event_id: Optional[uuid.UUID] = None,
) -> ProposalResult:
    """Record a proposal. Returns only the proposal_id — agent can't use
    its own proposal directly.

    Validations are deliberate up front so a bad call can't half-write
    (write the file but fail the DB row, etc.):
    - ``kind`` must be tool or playbook
    - ``title`` and ``body`` must be non-empty
    - ``proposing_agent`` must be non-empty (audit trail integrity)
    """
    if kind not in _VALID_KINDS:
        raise ProposeSkillError(f"kind must be one of {_VALID_KINDS}, got {kind!r}")
    if not isinstance(title, str) or not title.strip():
        raise ProposeSkillError("title is required")
    if not isinstance(body, str) or not body.strip():
        raise ProposeSkillError("body is required")
    if not isinstance(proposing_agent, str) or not proposing_agent.strip():
        raise ProposeSkillError("proposing_agent is required")

    ext = ".py" if kind == "tool" else ".md"
    slug = _slugify(title)
    short = uuid.uuid4().hex[:8]
    rel = Path("skills") / "proposed" / f"{kind}s" / f"{slug}-{short}{ext}"
    abs_path = _proposed_dir().parent / rel  # _proposed_dir is .../skills/proposed
    # Above resolves to .../skills/proposed/<kind>s/<slug>-<short>.<ext> which
    # is the same as Path(__file__).resolve().parents[3] / rel; reconstructing
    # to keep behavior identical even when STEVENS_SKILLS_PROPOSED override
    # points elsewhere.
    abs_path = _proposed_dir() / f"{kind}s" / f"{slug}-{short}{ext}"
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(body)

    proposal_id = asyncio.run(
        _insert_row(
            kind=kind,
            proposed_id=slug,
            proposing_agent=proposing_agent,
            body_path=str(rel),
            rationale=rationale,
            originating_event_id=originating_event_id,
        )
    )
    log.info(
        "skill proposal recorded id=%s kind=%s by=%s",
        proposal_id, kind, proposing_agent,
    )
    return ProposalResult(proposal_id=proposal_id, body_path=str(rel))


async def _insert_row(
    *,
    kind: str,
    proposed_id: str,
    proposing_agent: str,
    body_path: str,
    rationale: Optional[str],
    originating_event_id: Optional[uuid.UUID],
) -> uuid.UUID:
    from shared.db import connection

    async with connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO skill_proposals
                    (kind, proposed_id, proposing_agent, body_path,
                     rationale, originating_event)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING proposal_id
                """,
                (
                    kind,
                    proposed_id,
                    proposing_agent,
                    body_path,
                    rationale,
                    str(originating_event_id) if originating_event_id else None,
                ),
            )
            row = await cur.fetchone()
        await conn.commit()
    return row[0]
