"""Tests for AdminClient — best-effort operator nudges to a running Enkidu."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from stevens_security.admin_client import AdminClient


def test_try_create_no_key_returns_none(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("STEVENS_OPERATOR_PRIVATE_KEY_PATH", str(tmp_path / "missing.key"))
    assert AdminClient.try_create() is None


@pytest.mark.asyncio
async def test_refresh_approvals_calls_capability():
    class FakeClient:
        def __init__(self):
            self.calls = []

        async def call(self, capability, params):
            self.calls.append((capability, params))
            return {"ok": True, "active_count": 3}

    fake = FakeClient()
    admin = AdminClient(fake)
    out = await admin.refresh_approvals()
    assert fake.calls == [("_admin.refresh_approvals", {})]
    assert out["ok"] is True


@pytest.mark.asyncio
async def test_mark_request_approved_calls_capability():
    class FakeClient:
        def __init__(self):
            self.calls = []

        async def call(self, capability, params):
            self.calls.append((capability, params))
            return {"ok": True, "request_id": "r-1"}

    fake = FakeClient()
    admin = AdminClient(fake)
    await admin.mark_request_approved("r-1")
    assert fake.calls == [("_admin.mark_request_approved", {"request_id": "r-1"})]


@pytest.mark.asyncio
async def test_transport_error_is_swallowed():
    """Enkidu not running — admin nudges should silently no-op (with a log)."""
    from shared.security_client import TransportError

    class FailingClient:
        async def call(self, capability, params):
            raise TransportError("no socket")

    admin = AdminClient(FailingClient())
    assert await admin.refresh_approvals() is None  # no exception
    assert await admin.mark_request_approved("x") is None


@pytest.mark.asyncio
async def test_response_error_is_logged_not_raised():
    from shared.security_client import DenyError

    class DenyingClient:
        async def call(self, capability, params):
            raise DenyError("DENY", "operator not allowed", "trace-1")

    admin = AdminClient(DenyingClient())
    assert await admin.refresh_approvals() is None
