"""CLI subcommands for `stevens approval` and `stevens dep`.

The handlers take an ``ApprovalStore`` (and for `dep`, a ``SystemRuntime``-
shaped reader) so tests don't need a Postgres instance.

In production, ``main()`` in ``cli.py`` constructs the Postgres-backed
implementations and passes them in. In tests we pass in-memory ones.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, List, Optional

from .approvals.queue import ApprovalRequest
from .approvals.store import (
    ApprovalStore,
    StandingGrant,
    StoreError,
    parse_duration,
)
from .system_runtime import Inventory, InventoryRow


# --- formatters ---


def fmt_pending(requests: List[ApprovalRequest]) -> str:
    if not requests:
        return "(no pending approvals)"
    lines = [f"{len(requests)} pending approval(s):"]
    for r in requests:
        lines.append(
            f"  [{r.id}] {r.capability:<28} caller={r.caller}  "
            f"summary={r.params_summary}"
        )
        if r.rationale:
            lines.append(f"      rationale: {r.rationale}")
    return "\n".join(lines)


def fmt_request_detail(r: ApprovalRequest) -> str:
    lines = [
        f"id:           {r.id}",
        f"capability:   {r.capability}",
        f"caller:       {r.caller}",
        f"status:       {r.status}",
        f"created_at:   {r.created_at}",
        f"summary:      {r.params_summary}",
    ]
    if r.rationale:
        lines.append(f"rationale:    {r.rationale}")
    if r.decided_at:
        lines.append(f"decided_at:   {r.decided_at} by {r.decided_by}")
    return "\n".join(lines)


def fmt_standing(approvals: list) -> str:
    if not approvals:
        return "(no standing approvals)"
    lines = [f"{len(approvals)} standing approval(s):"]
    for sa in approvals:
        status = "revoked" if sa.revoked_at else (
            "expired" if (sa.expires_at and sa.expires_at < datetime.now(timezone.utc)) else "active"
        )
        lines.append(
            f"  [{sa.id}] {sa.capability:<28} caller={sa.caller}  [{status}]"
        )
        if sa.predicates:
            lines.append(f"      predicates: {sa.predicates}")
        if sa.rationale:
            lines.append(f"      rationale:  {sa.rationale}")
        if sa.expires_at:
            lines.append(f"      expires_at: {sa.expires_at.isoformat()}")
        elif sa.expires_session:
            lines.append(f"      session:    {sa.expires_session}")
    return "\n".join(lines)


def fmt_inventory(rows: List[InventoryRow]) -> str:
    if not rows:
        return "(no installed packages tracked)"
    lines = [f"{len(rows)} package(s):"]
    for r in rows:
        lines.append(
            f"  {r.name:<30} mechanism={r.mechanism}  caller={r.caller}  "
            f"health={r.health_status}  installed_at={r.installed_at}"
        )
    return "\n".join(lines)


# --- handlers ---


async def cmd_approval_list(args, store: ApprovalStore) -> int:
    pending = await store.list_pending()
    print(fmt_pending(pending))
    return 0


async def cmd_approval_show(args, store: ApprovalStore) -> int:
    r = await store.get_request(args.id)
    if r is None:
        print(f"no request with id {args.id}", file=sys.stderr)
        return 1
    print(fmt_request_detail(r))
    return 0


async def cmd_approval_approve(
    args, store: ApprovalStore, *, decided_by: str = "operator",
) -> int:
    """Approve a pending request. Optionally promote to standing."""
    r = await store.get_request(args.id)
    if r is None:
        print(f"no request with id {args.id}", file=sys.stderr)
        return 1
    if r.status != "pending":
        print(f"request {args.id} is {r.status!r}, not pending", file=sys.stderr)
        return 1
    promoted_id: Optional[str] = None
    if args.standing_for:
        # Build a standing-grant from the request. Default predicates: replicate
        # whatever the call's params declared (mechanism, source, packages).
        params = r.full_envelope.get("params") if isinstance(r.full_envelope, dict) else {}
        predicates = {}
        if isinstance(params, dict):
            for k in ("mechanism", "source", "packages"):
                if k in params:
                    predicates[k] = params[k]
        # Custom tighten via --tighten k=v ...
        if args.tighten:
            for kv in args.tighten:
                k, _, v = kv.partition("=")
                if not _:
                    continue
                predicates[k] = v
        expires_at: Optional[datetime] = None
        expires_session: Optional[str] = None
        delta = parse_duration(args.standing_for)
        if delta is not None:
            expires_at = datetime.now(timezone.utc) + delta
        elif args.standing_for == "session":
            expires_session = "current"
        sa = await store.grant_standing(
            granted_by=decided_by,
            grant=StandingGrant(
                capability=r.capability, caller=r.caller,
                predicates=predicates,
                expires_at=expires_at,
                expires_session=expires_session,
                rationale=args.rationale or r.rationale,
            ),
        )
        promoted_id = sa.id
        print(f"granted standing approval {sa.id}")
    await store.decide_request(
        request_id=args.id, status="approved",
        decided_by=decided_by, notes=args.notes,
        promoted_standing_id=promoted_id,
    )
    print(f"approved {args.id}")
    return 0


async def cmd_approval_reject(
    args, store: ApprovalStore, *, decided_by: str = "operator",
) -> int:
    r = await store.get_request(args.id)
    if r is None:
        print(f"no request with id {args.id}", file=sys.stderr)
        return 1
    await store.decide_request(
        request_id=args.id, status="rejected",
        decided_by=decided_by, notes=args.reason,
    )
    print(f"rejected {args.id}")
    return 0


async def cmd_approval_standing_list(args, store: ApprovalStore) -> int:
    items = await store.list_standing(include_revoked=args.include_revoked)
    print(fmt_standing(items))
    return 0


async def cmd_approval_standing_grant(
    args, store: ApprovalStore, *, granted_by: str = "operator",
) -> int:
    predicates = {}
    if args.mechanism:
        predicates["mechanism"] = args.mechanism
    if args.source_regex:
        predicates["source"] = {"regex": args.source_regex}
    if args.packages:
        predicates["packages"] = {"in": [p.strip() for p in args.packages.split(",") if p.strip()]}
    if args.param:
        for kv in args.param:
            k, _, v = kv.partition("=")
            if not _:
                continue
            predicates.setdefault("param_matchers", {})[k] = v
    expires_at: Optional[datetime] = None
    expires_session: Optional[str] = None
    if args.duration:
        delta = parse_duration(args.duration)
        if delta is not None:
            expires_at = datetime.now(timezone.utc) + delta
        elif args.duration == "session":
            expires_session = "current"
    sa = await store.grant_standing(
        granted_by=granted_by,
        grant=StandingGrant(
            capability=args.capability,
            caller=args.caller,
            predicates=predicates,
            expires_at=expires_at,
            expires_session=expires_session,
            rationale=args.rationale,
        ),
    )
    print(f"granted standing approval {sa.id} for {sa.capability} caller={sa.caller}")
    if predicates:
        print(f"  predicates: {predicates}")
    return 0


async def cmd_approval_standing_revoke(
    args, store: ApprovalStore, *, revoked_by: str = "operator",
) -> int:
    try:
        sa = await store.revoke_standing(standing_id=args.id, revoked_by=revoked_by)
    except StoreError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"revoked standing approval {sa.id}")
    return 0


# --- dep subcommands ---


async def cmd_dep_list(args, inventory: Inventory) -> int:
    rows = await inventory.list_global(name=args.name)
    print(fmt_inventory(rows))
    return 0


# Bus publishers are wired in production via shared.bus; tests pass a
# fake. The handler takes the publisher as a callable so it stays decoupled.
DepRequester = Callable[[str], Awaitable[None]]


async def cmd_dep_ensure(args, *, request: DepRequester) -> int:
    await request(args.package)
    print(f"requested install of {args.package}")
    return 0


# --- argparse wiring ---


def add_approval_parser(subparsers) -> argparse.ArgumentParser:
    """Register the approval / dep subparsers under the given top-level subparsers.

    Returns the approval subparser for testing convenience.
    """
    ap = subparsers.add_parser("approval", help="manage per-call and standing approvals")
    ap_sub = ap.add_subparsers(dest="subcmd", required=True)

    sp = ap_sub.add_parser("list", help="show pending per-call approvals")
    sp.set_defaults(_handler=cmd_approval_list)

    sp = ap_sub.add_parser("show", help="show full detail of a pending approval")
    sp.add_argument("id")
    sp.set_defaults(_handler=cmd_approval_show)

    sp = ap_sub.add_parser("approve", help="approve a pending per-call approval")
    sp.add_argument("id")
    sp.add_argument("--standing-for", default=None,
                    help="promote to standing for {30d, 4h, session, forever}")
    sp.add_argument("--tighten", action="append", default=None,
                    help="add a predicate to the promoted standing approval (k=v)")
    sp.add_argument("--rationale", default=None)
    sp.add_argument("--notes", default=None)
    sp.set_defaults(_handler=cmd_approval_approve)

    sp = ap_sub.add_parser("reject", help="reject a pending per-call approval")
    sp.add_argument("id")
    sp.add_argument("--reason", required=True)
    sp.set_defaults(_handler=cmd_approval_reject)

    standing = ap_sub.add_parser("standing", help="manage standing approvals")
    st_sub = standing.add_subparsers(dest="standing_sub", required=True)

    sp = st_sub.add_parser("list", help="list standing approvals")
    sp.add_argument("--include-revoked", action="store_true")
    sp.set_defaults(_handler=cmd_approval_standing_list)

    sp = st_sub.add_parser("grant", help="directly grant a standing approval")
    sp.add_argument("--capability", required=True)
    sp.add_argument("--caller", required=True)
    sp.add_argument("--mechanism", default=None)
    sp.add_argument("--source-regex", default=None)
    sp.add_argument("--packages", default=None,
                    help="comma-separated package names")
    sp.add_argument("--param", action="append", default=None,
                    help="custom param matcher k=v (repeatable)")
    sp.add_argument("--duration", default=None,
                    help="{30d, 4h, session, forever}")
    sp.add_argument("--rationale", default=None)
    sp.set_defaults(_handler=cmd_approval_standing_grant)

    sp = st_sub.add_parser("revoke", help="revoke a standing approval")
    sp.add_argument("id")
    sp.set_defaults(_handler=cmd_approval_standing_revoke)

    return ap


def add_dep_parser(subparsers) -> argparse.ArgumentParser:
    dep = subparsers.add_parser("dep", help="manage system dependencies via the installer agent")
    dep_sub = dep.add_subparsers(dest="subcmd", required=True)

    sp = dep_sub.add_parser("ensure", help="request the installer to ensure a package is installed")
    sp.add_argument("package")
    sp.set_defaults(_handler=cmd_dep_ensure)

    sp = dep_sub.add_parser("list", help="list installed packages (full inventory)")
    sp.add_argument("--name", default=None, help="filter by package name")
    sp.set_defaults(_handler=cmd_dep_list)

    return dep
