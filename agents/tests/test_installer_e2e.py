"""End-to-end installer flow with mocked subprocess.

Wires together (in-memory):
- the dispatcher (with matcher + queue + audit + capabilities)
- the installer agent (with a stub SecurityClient that calls the dispatcher
  directly instead of going over UDS)
- the system runtime (with a fake subprocess that returns scripted results)

Two scenarios:
  1. No standing approval → BLOCKED → operator approves → replay → ok.
  2. With a standing approval pre-loaded → silent execute, no BLOCKED.
"""

from __future__ import annotations

import asyncio
import base64
import json
from datetime import datetime, timezone
from typing import Any, Dict, List

import nacl.signing
import pytest

from agents.installer import agent as installer_agent
from shared.events import (
    SystemDepAwaitingApprovalEvent,
    SystemDepInstalledEvent,
    SystemDepRequestedEvent,
)
from stevens_security.approvals.matcher import MatcherIndex, StandingApproval
from stevens_security.approvals.queue import InMemoryApprovalQueue
from stevens_security.audit import AuditWriter
from stevens_security.canonical import canonical_encode
from stevens_security.capabilities import system as system_caps  # noqa: F401 — registers
from stevens_security.capabilities.registry import default_registry
from stevens_security.context import CapabilityContext
from stevens_security.dispatch import build_dispatcher
from stevens_security.identity import NonceCache, RegisteredAgent
from stevens_security.mechanisms.base import ExecResult, Executor
from stevens_security.policy import AgentPolicy, CapabilityRule, Policy
from stevens_security.system_runtime import (
    InMemoryInventory,
    InMemoryPlanStore,
    SystemRuntime,
)


class _FakeSubprocess:
    def __init__(self) -> None:
        self.calls: List[Executor] = []
        self.responses: List[ExecResult] = []
        self.default = ExecResult(0, b"", b"")

    async def __call__(self, executor: Executor) -> ExecResult:
        self.calls.append(executor)
        if self.responses:
            return self.responses.pop(0)
        return self.default

    def script(self, *results: ExecResult) -> None:
        self.responses.extend(results)


class FakeBus:
    def __init__(self) -> None:
        self.published = []

    async def publish(self, event_obj):
        self.published.append(event_obj)
        return None


class _DispatcherClient:
    """Stand-in for SecurityClient that signs envelopes and calls the
    dispatcher directly (bypassing UDS framing)."""

    def __init__(self, *, dispatcher, sk, caller_name, replay_id_for_next_call=None):
        self._dispatcher = dispatcher
        self._sk = sk
        self._caller = caller_name
        self.replay_id_for_next_call = replay_id_for_next_call
        self._nonce = 0

    async def call(self, capability, params=None):
        self._nonce += 1
        params = params or {}
        envelope = {
            "v": 1, "caller": self._caller, "nonce": f"n-{self._nonce}",
            "ts": int(datetime.now(timezone.utc).timestamp()),
            "capability": capability, "params": params,
        }
        scope = {k: envelope[k] for k in ("v", "caller", "nonce", "ts", "capability", "params")}
        sig = self._sk.sign(canonical_encode(scope)).signature
        envelope["sig"] = base64.b64encode(sig).decode("ascii")
        if self.replay_id_for_next_call is not None:
            envelope["replay_request_id"] = self.replay_id_for_next_call
            self.replay_id_for_next_call = None
        resp = await self._dispatcher(envelope)
        if resp.get("ok") is True:
            return resp["result"]
        # Match SecurityClient's error mapping.
        from shared.security_client import (
            AuthError, BlockedError, DenyError, InternalError,
            NotFoundError, RateError, ResponseError,
        )
        code = resp.get("error_code") or "INTERNAL"
        message = resp.get("message") or ""
        trace_id = resp.get("trace_id")
        cls = {
            "AUTH": AuthError, "DENY": DenyError, "NOTFOUND": NotFoundError,
            "RATE": RateError, "INTERNAL": InternalError,
            "BLOCKED": BlockedError,
        }.get(code, ResponseError)
        if cls is BlockedError:
            raise BlockedError(
                code, message, trace_id,
                approval_request_id=resp.get("approval_request_id"),
            )
        raise cls(code, message, trace_id)


@pytest.fixture
def fake_bus(monkeypatch):
    fb = FakeBus()
    monkeypatch.setattr(installer_agent.bus, "publish", fb.publish)
    yield fb


def _build_stack(tmp_path, *, standing=None):
    sk = nacl.signing.SigningKey.generate()
    vk = sk.verify_key
    identity = {"installer": RegisteredAgent(name="installer", verify_key=vk)}
    policy = Policy(
        agents={
            "installer": AgentPolicy(
                agent="installer",
                allow={
                    "system.read_environment": CapabilityRule(name="system.read_environment"),
                    "system.plan_install": CapabilityRule(name="system.plan_install"),
                    "system.execute_privileged": CapabilityRule(
                        name="system.execute_privileged",
                        requires_approval=True,
                    ),
                },
            ),
        },
    )
    audit = AuditWriter(tmp_path / "audit")
    sub = _FakeSubprocess()
    runtime = SystemRuntime(
        plan_store=InMemoryPlanStore(),
        inventory=InMemoryInventory(),
        run_subprocess=sub,
    )
    matcher = MatcherIndex(standing or [])
    queue = InMemoryApprovalQueue()

    approved_ids: set = set()

    dispatcher = build_dispatcher(
        identity_registry=identity, policy=policy,
        audit_writer=audit,
        capability_registry=default_registry,
        nonce_cache=NonceCache(),
        context=CapabilityContext(extra={"system": runtime}),
        matcher=matcher,
        approval_queue=queue,
        bypass_approval_for_request_id=lambda rid: rid in approved_ids,
    )
    return {
        "sk": sk, "dispatcher": dispatcher, "sub": sub, "runtime": runtime,
        "queue": queue, "matcher": matcher, "audit_dir": tmp_path / "audit",
        "approved_ids": approved_ids,
    }


def _read_audit(audit_dir):
    files = sorted(audit_dir.glob("*.jsonl")) if audit_dir.exists() else []
    out = []
    for f in files:
        for line in f.read_text().strip().split("\n"):
            if line:
                out.append(json.loads(line))
    return out


def _script_install_success(sub: _FakeSubprocess, package="tesseract-ocr") -> None:
    """Two subprocess responses: read_environment + dpkg → installer flow.

    Order in the agent flow:
      1. read_environment → 2 subprocess calls (cat /etc/os-release, dpkg-query)
      2. plan_install → no subprocess (pure validation)
      3. execute_privileged → apt-get install + dpkg-query (probe)
    """
    sub.script(
        # 1. cat /etc/os-release
        ExecResult(0, b'ID=ubuntu\nVERSION_ID="22.04"\n', b""),
        # 2. dpkg-query (initial: not installed)
        ExecResult(1, b"", b"package not installed"),
        # 3. apt-get install
        ExecResult(0, b"", b""),
        # 4. dpkg-query (probe: now installed)
        ExecResult(0, f"{package} install ok installed\n".encode(), b""),
    )


# ============================================================
# Scenario 1: BLOCKED → approve → replay → ok
# ============================================================


@pytest.mark.asyncio
async def test_blocked_then_approve_then_install(tmp_path, fake_bus):
    stack = _build_stack(tmp_path)
    _script_install_success(stack["sub"])

    client = _DispatcherClient(
        dispatcher=stack["dispatcher"], sk=stack["sk"], caller_name="installer",
    )
    installer_agent._set_client_for_tests(client)

    # 1. Drive the request.
    await installer_agent.handle(
        SystemDepRequestedEvent(account_id="system", package="tesseract-ocr"),
        config={},
    )

    # First pass: BLOCKED → published awaiting_approval.
    assert len(fake_bus.published) == 1
    awaiting = fake_bus.published[0]
    assert isinstance(awaiting, SystemDepAwaitingApprovalEvent)
    assert awaiting.package == "tesseract-ocr"
    request_id = awaiting.approval_request_id
    assert request_id

    # Sol approves (the per-call approval).
    await stack["queue"].decide(
        request_id=request_id, status="approved", decided_by="sol",
    )
    stack["approved_ids"].add(request_id)

    # Replay path: re-call execute_privileged with replay_request_id.
    client.replay_id_for_next_call = request_id
    result = await client.call(
        "system.execute_privileged",
        {"plan_id": _last_plan_id(stack), "rationale": "x"},
    )
    assert result["outcome"] == "ok"

    # Audit log shows BLOCKED + ok with approval_via=per_call/<id>.
    lines = _read_audit(stack["audit_dir"])
    blocked = [l for l in lines if l["outcome"] == "blocked"]
    ok_lines = [l for l in lines if l["outcome"] == "ok" and l["capability"] == "system.execute_privileged"]
    assert len(blocked) == 1
    assert len(ok_lines) == 1
    assert ok_lines[0]["approval_via"] == f"per_call/{request_id}"


# ============================================================
# Scenario 2: with standing approval → silent ok
# ============================================================


@pytest.mark.asyncio
async def test_standing_approval_silent_ok(tmp_path, fake_bus):
    standing = [
        StandingApproval(
            id="sa-1",
            capability="system.execute_privileged",
            caller="installer",
            predicates={"mechanism": "apt"},
        ),
    ]
    stack = _build_stack(tmp_path, standing=standing)
    _script_install_success(stack["sub"])

    client = _DispatcherClient(
        dispatcher=stack["dispatcher"], sk=stack["sk"], caller_name="installer",
    )
    installer_agent._set_client_for_tests(client)

    await installer_agent.handle(
        SystemDepRequestedEvent(account_id="system", package="tesseract-ocr"),
        config={},
    )

    # No BLOCKED — install fired through.
    topics = [e.topic for e in fake_bus.published]
    assert topics == ["system.dep.installed.tesseract-ocr"]

    # Audit shows the ok with approval_via=standing/sa-1.
    lines = _read_audit(stack["audit_dir"])
    ok_lines = [
        l for l in lines
        if l["outcome"] == "ok" and l["capability"] == "system.execute_privileged"
    ]
    assert len(ok_lines) == 1
    assert ok_lines[0]["approval_via"] == "standing/sa-1"

    # Inventory has the install row.
    inv = await stack["runtime"].inventory.list_for("installer")
    assert len(inv) == 1
    assert "tesseract-ocr" in inv[0].name


def _last_plan_id(stack) -> str:
    """Helper: pull the most recently inserted plan_id from the runtime."""
    rows = list(stack["runtime"].plan_store._rows.values())
    return rows[-1].id
