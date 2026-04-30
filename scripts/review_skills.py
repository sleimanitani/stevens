"""Operator CLI to triage agent-proposed skills.

Subcommands::

    review_skills.py list
    review_skills.py show <id>
    review_skills.py approve <id> [--scope shared|restricted]
                                  [--allowed-agents A,B]
                                  [--safety read-only|read-write|destructive]
                                  [--category <category>]
    review_skills.py reject <id> --reason "..."

Approve moves the body file from ``skills/proposed/<kind>s/<x>`` to
``skills/src/skills/<kind>s/<category>/<x>`` (or
``playbooks/<category>/<x>`` for playbooks), appends an entry to
``skills/registry.yaml``, and flips the DB row to ``approved``.

Reject just flips the DB row to ``rejected`` with the operator-supplied
reason. The body file stays in ``proposed/`` for the audit trail.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import yaml


@dataclass(frozen=True)
class Proposal:
    proposal_id: str
    kind: str
    proposed_id: str
    proposing_agent: str
    body_path: str
    rationale: Optional[str]
    status: str
    created_at: object


def _repo_root() -> Path:
    """Repo root: the parent of ``scripts/`` is the repo root by convention."""
    return Path(__file__).resolve().parents[1]


# --- DB access ---


async def _list_proposals(*, status: str = "pending") -> List[Proposal]:
    from shared.db import connection

    out: List[Proposal] = []
    async with connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT proposal_id, kind, proposed_id, proposing_agent,
                       body_path, rationale, status, created_at
                FROM skill_proposals
                WHERE status = %s
                ORDER BY created_at ASC
                """,
                (status,),
            )
            rows = await cur.fetchall()
    for r in rows:
        out.append(
            Proposal(
                proposal_id=str(r[0]),
                kind=r[1],
                proposed_id=r[2],
                proposing_agent=r[3],
                body_path=r[4],
                rationale=r[5],
                status=r[6],
                created_at=r[7],
            )
        )
    return out


async def _get_proposal(proposal_id: str) -> Optional[Proposal]:
    from shared.db import connection

    async with connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT proposal_id, kind, proposed_id, proposing_agent,
                       body_path, rationale, status, created_at
                FROM skill_proposals
                WHERE proposal_id = %s
                """,
                (proposal_id,),
            )
            r = await cur.fetchone()
    if not r:
        return None
    return Proposal(
        proposal_id=str(r[0]),
        kind=r[1],
        proposed_id=r[2],
        proposing_agent=r[3],
        body_path=r[4],
        rationale=r[5],
        status=r[6],
        created_at=r[7],
    )


async def _set_status(
    proposal_id: str, status: str, *, reviewer: str, notes: Optional[str] = None
) -> None:
    from shared.db import connection

    async with connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE skill_proposals
                SET status = %s, reviewed_by = %s, reviewed_at = now(),
                    review_notes = %s
                WHERE proposal_id = %s
                """,
                (status, reviewer, notes, proposal_id),
            )
        await conn.commit()


# --- registry update (pure file ops, testable) ---


def promote_into_repo(
    *,
    repo_root: Path,
    body_path_rel: str,           # e.g. skills/proposed/playbooks/x.md
    kind: str,                     # tool | playbook
    proposed_id: str,              # e.g. email-blocker-triage (slug)
    category: str,
    scope: str = "shared",
    allowed_agents: Optional[List[str]] = None,
    safety_class: str = "read-only",
    version: str = "1.0.0",
) -> Path:
    """Move the body file out of ``proposed/`` and update registry.yaml.

    Returns the new absolute path. Pure file/yaml operations — no DB.
    """
    src = repo_root / body_path_rel
    if not src.exists():
        raise FileNotFoundError(f"proposed body file not found: {src}")
    if kind == "tool":
        dest_dir = repo_root / "skills" / "src" / "skills" / "tools" / category
    elif kind == "playbook":
        dest_dir = repo_root / "skills" / "src" / "skills" / "playbooks" / category
    else:
        raise ValueError(f"unknown kind: {kind!r}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    shutil.move(str(src), str(dest))

    reg_path = repo_root / "skills" / "registry.yaml"
    raw = yaml.safe_load(reg_path.read_text()) if reg_path.exists() else {}
    if not isinstance(raw, dict):
        raw = {}
    raw.setdefault("tools", [])
    raw.setdefault("playbooks", [])

    rel = dest.relative_to(repo_root)
    if kind == "tool":
        entry = {
            "id": f"{category}.{src.stem.split('-')[0]}",
            "path": str(rel),
            "scope": scope,
            "safety_class": safety_class,
            "version": version,
        }
        if scope == "restricted":
            entry["allowed_agents"] = allowed_agents or []
        raw["tools"].append(entry)
    else:  # playbook — pull metadata from the file's frontmatter
        from skills.playbooks.loader import load_playbook

        pb = load_playbook(dest)
        raw["playbooks"].append(
            {
                "id": f"{category}/{pb.name}",
                "path": str(rel),
                "applies_to_topics": list(pb.applies_to_topics),
                "applies_to_agents": list(pb.applies_to_agents),
            }
        )
    reg_path.write_text(yaml.safe_dump(raw, sort_keys=False))
    return dest


# --- subcommands ---


def cmd_list(args: argparse.Namespace) -> int:
    proposals = asyncio.run(_list_proposals(status=args.status))
    if not proposals:
        print(f"(no {args.status} proposals)")
        return 0
    print(f"{len(proposals)} {args.status} proposal(s):")
    for p in proposals:
        print(
            f"  [{p.proposal_id}]  {p.kind:<8} {p.proposed_id:<30} "
            f"by {p.proposing_agent}  ({p.created_at})"
        )
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    proposal = asyncio.run(_get_proposal(args.id))
    if not proposal:
        print(f"no proposal with id {args.id}", file=sys.stderr)
        return 1
    body_path = _repo_root() / proposal.body_path
    if not body_path.exists():
        print(f"body file missing: {body_path}", file=sys.stderr)
        return 1
    editor = os.environ.get("EDITOR", "less")
    return subprocess.call([editor, str(body_path)])


def cmd_approve(args: argparse.Namespace) -> int:
    proposal = asyncio.run(_get_proposal(args.id))
    if not proposal:
        print(f"no proposal with id {args.id}", file=sys.stderr)
        return 1
    if proposal.status != "pending":
        print(f"proposal status is {proposal.status!r}, not pending", file=sys.stderr)
        return 1
    allowed = (
        [a.strip() for a in args.allowed_agents.split(",") if a.strip()]
        if args.allowed_agents
        else None
    )
    promote_into_repo(
        repo_root=_repo_root(),
        body_path_rel=proposal.body_path,
        kind=proposal.kind,
        proposed_id=proposal.proposed_id,
        category=args.category or proposal.proposed_id.split("-")[0],
        scope=args.scope,
        allowed_agents=allowed,
        safety_class=args.safety,
    )
    asyncio.run(
        _set_status(
            args.id, "approved",
            reviewer=os.environ.get("USER", "operator"),
        )
    )
    print(f"approved {args.id} → moved into repo + registry updated")
    return 0


def cmd_reject(args: argparse.Namespace) -> int:
    proposal = asyncio.run(_get_proposal(args.id))
    if not proposal:
        print(f"no proposal with id {args.id}", file=sys.stderr)
        return 1
    asyncio.run(
        _set_status(
            args.id, "rejected",
            reviewer=os.environ.get("USER", "operator"),
            notes=args.reason,
        )
    )
    print(f"rejected {args.id}: {args.reason}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="review_skills",
        description="Triage agent-proposed skills (tools + playbooks).",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("list", help="show pending proposals")
    sp.add_argument(
        "--status", default="pending",
        choices=["pending", "approved", "rejected", "superseded"],
    )
    sp.set_defaults(fn=cmd_list)

    sp = sub.add_parser("show", help="open a proposal in $EDITOR")
    sp.add_argument("id")
    sp.set_defaults(fn=cmd_show)

    sp = sub.add_parser("approve", help="approve and promote into the repo")
    sp.add_argument("id")
    sp.add_argument(
        "--scope", choices=["shared", "restricted"], default="shared"
    )
    sp.add_argument(
        "--allowed-agents",
        default=None,
        help="comma-separated agent names (required if --scope=restricted)",
    )
    sp.add_argument(
        "--safety",
        choices=["read-only", "read-write", "destructive"],
        default="read-only",
    )
    sp.add_argument(
        "--category",
        default=None,
        help="subdir under skills/src/skills/<kind>s/ (defaults to first slug segment)",
    )
    sp.set_defaults(fn=cmd_approve)

    sp = sub.add_parser("reject", help="mark a proposal rejected")
    sp.add_argument("id")
    sp.add_argument("--reason", required=True)
    sp.set_defaults(fn=cmd_reject)

    args = p.parse_args(argv)
    return int(args.fn(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
