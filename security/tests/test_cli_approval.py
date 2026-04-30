"""Tests for the `stevens approval` and `stevens dep` CLI handlers.

Argparse + handler functions are exercised against the in-memory store. No
DB, no Postgres. The Postgres backend is wired up in production via the
``ApprovalStore`` Protocol; that wiring is out of scope here.
"""

from __future__ import annotations

import asyncio
import io
import sys
from contextlib import redirect_stdout
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import List

import pytest

from stevens_security.approvals.queue import ApprovalRequest
from stevens_security.approvals.store import (
    InMemoryApprovalStore,
    StandingGrant,
    StoreError,
    parse_duration,
)
from stevens_security.cli_approvals import (
    cmd_approval_approve,
    cmd_approval_list,
    cmd_approval_reject,
    cmd_approval_show,
    cmd_approval_standing_grant,
    cmd_approval_standing_list,
    cmd_approval_standing_revoke,
    cmd_dep_ensure,
    cmd_dep_list,
)
from stevens_security.system_runtime import InMemoryInventory, InventoryRow


def _capture(coro_fn, *args, **kwargs):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = asyncio.run(coro_fn(*args, **kwargs))
    return rc, buf.getvalue()


# --- approval list / show ---


def test_list_pending_empty() -> None:
    store = InMemoryApprovalStore()
    rc, out = _capture(cmd_approval_list, SimpleNamespace(), store)
    assert rc == 0
    assert "no pending approvals" in out


def test_list_pending_shows_rows() -> None:
    store = InMemoryApprovalStore()
    asyncio.run(store.enqueue_request(request=ApprovalRequest(
        id="r-1", capability="system.execute_privileged", caller="installer",
        params_summary="apt install tesseract-ocr", full_envelope={},
        rationale="OCR needed",
    )))
    rc, out = _capture(cmd_approval_list, SimpleNamespace(), store)
    assert "r-1" in out
    assert "system.execute_privileged" in out
    assert "OCR needed" in out


def test_show_request() -> None:
    store = InMemoryApprovalStore()
    asyncio.run(store.enqueue_request(request=ApprovalRequest(
        id="r-1", capability="x.y", caller="c",
        params_summary="summary", full_envelope={}, rationale="why",
    )))
    rc, out = _capture(cmd_approval_show, SimpleNamespace(id="r-1"), store)
    assert "id:           r-1" in out
    assert "capability:   x.y" in out


def test_show_unknown() -> None:
    store = InMemoryApprovalStore()
    rc = asyncio.run(cmd_approval_show(SimpleNamespace(id="missing"), store))
    assert rc == 1


# --- approve + reject ---


def test_approve_flips_status() -> None:
    store = InMemoryApprovalStore()
    asyncio.run(store.enqueue_request(request=ApprovalRequest(
        id="r-1", capability="x.y", caller="c",
        params_summary="x", full_envelope={"params": {"mechanism": "apt"}},
    )))
    rc = asyncio.run(cmd_approval_approve(
        SimpleNamespace(id="r-1", standing_for=None, tighten=None,
                        rationale=None, notes=None),
        store,
    ))
    assert rc == 0
    r = asyncio.run(store.get_request("r-1"))
    assert r.status == "approved"


def test_approve_promotes_to_standing() -> None:
    store = InMemoryApprovalStore()
    asyncio.run(store.enqueue_request(request=ApprovalRequest(
        id="r-1", capability="system.execute_privileged", caller="installer",
        params_summary="x",
        full_envelope={"params": {"mechanism": "apt", "source": "deb.debian.org"}},
    )))
    rc, out = _capture(
        cmd_approval_approve,
        SimpleNamespace(id="r-1", standing_for="30d", tighten=None,
                        rationale="trusted", notes=None),
        store,
    )
    assert rc == 0
    standing = asyncio.run(store.list_standing())
    assert len(standing) == 1
    sa = standing[0]
    assert sa.capability == "system.execute_privileged"
    # Predicates should include mechanism + source from the call's params.
    assert sa.predicates.get("mechanism") == "apt"
    assert sa.expires_at is not None  # 30d duration → wall-clock expiry


def test_reject_flips_status() -> None:
    store = InMemoryApprovalStore()
    asyncio.run(store.enqueue_request(request=ApprovalRequest(
        id="r-1", capability="x.y", caller="c",
        params_summary="x", full_envelope={},
    )))
    asyncio.run(cmd_approval_reject(
        SimpleNamespace(id="r-1", reason="not safe"), store,
    ))
    r = asyncio.run(store.get_request("r-1"))
    assert r.status == "rejected"
    assert r.decision_notes == "not safe"


# --- standing approvals ---


def test_grant_standing_minimal() -> None:
    store = InMemoryApprovalStore()
    args = SimpleNamespace(
        capability="system.execute_privileged",
        caller="installer",
        mechanism="apt",
        source_regex=None,
        packages=None,
        param=None,
        duration=None,
        rationale="trust apt",
    )
    rc, out = _capture(cmd_approval_standing_grant, args, store)
    assert rc == 0
    items = asyncio.run(store.list_standing())
    assert len(items) == 1
    assert items[0].predicates == {"mechanism": "apt"}


def test_grant_standing_with_packages_and_source() -> None:
    store = InMemoryApprovalStore()
    args = SimpleNamespace(
        capability="system.execute_privileged",
        caller="installer",
        mechanism=None,
        source_regex=r"^deb\.debian\..*$",
        packages="tesseract-ocr,poppler-utils",
        param=None,
        duration="forever",
        rationale=None,
    )
    rc = asyncio.run(cmd_approval_standing_grant(args, store))
    items = asyncio.run(store.list_standing())
    sa = items[0]
    assert sa.predicates["source"] == {"regex": r"^deb\.debian\..*$"}
    assert sa.predicates["packages"] == {"in": ["tesseract-ocr", "poppler-utils"]}
    assert sa.expires_at is None  # forever


def test_revoke_standing() -> None:
    store = InMemoryApprovalStore()
    sa = asyncio.run(store.grant_standing(
        granted_by="op",
        grant=StandingGrant(capability="x", caller="y"),
    ))
    rc = asyncio.run(cmd_approval_standing_revoke(SimpleNamespace(id=sa.id), store))
    assert rc == 0
    revoked = asyncio.run(store.list_standing(include_revoked=True))
    assert revoked[0].revoked_at is not None


def test_revoke_unknown_returns_1() -> None:
    store = InMemoryApprovalStore()
    rc = asyncio.run(cmd_approval_standing_revoke(SimpleNamespace(id="bogus"), store))
    assert rc == 1


def test_standing_list_excludes_revoked_by_default() -> None:
    store = InMemoryApprovalStore()
    sa = asyncio.run(store.grant_standing(
        granted_by="op",
        grant=StandingGrant(capability="x", caller="y"),
    ))
    asyncio.run(store.revoke_standing(standing_id=sa.id, revoked_by="op"))
    rc, out = _capture(
        cmd_approval_standing_list,
        SimpleNamespace(include_revoked=False), store,
    )
    assert "no standing approvals" in out
    rc, out = _capture(
        cmd_approval_standing_list,
        SimpleNamespace(include_revoked=True), store,
    )
    assert sa.id in out


# --- dep ---


def test_dep_list_empty() -> None:
    inv = InMemoryInventory()
    rc, out = _capture(cmd_dep_list, SimpleNamespace(name=None), inv)
    assert "no installed packages" in out


def test_dep_list_with_rows() -> None:
    inv = InMemoryInventory()
    asyncio.run(inv.append(InventoryRow(
        id="i-1", caller="installer", name="tesseract-ocr",
        mechanism="apt", plan_id="p-1", health_status="passed",
    )))
    rc, out = _capture(cmd_dep_list, SimpleNamespace(name=None), inv)
    assert "tesseract-ocr" in out
    assert "passed" in out


def test_dep_ensure_calls_requester() -> None:
    called = []

    async def fake_request(pkg):
        called.append(pkg)

    rc = asyncio.run(cmd_dep_ensure(
        SimpleNamespace(package="tesseract-ocr"),
        request=fake_request,
    ))
    assert rc == 0
    assert called == ["tesseract-ocr"]


# --- duration parser ---


def test_parse_duration_units() -> None:
    from datetime import timedelta

    assert parse_duration("30d") == timedelta(days=30)
    assert parse_duration("4h") == timedelta(hours=4)
    assert parse_duration("15m") == timedelta(minutes=15)


def test_parse_duration_special() -> None:
    assert parse_duration("forever") is None
    assert parse_duration("session") is None


def test_parse_duration_invalid_raises() -> None:
    with pytest.raises(ValueError):
        parse_duration("eternity")
