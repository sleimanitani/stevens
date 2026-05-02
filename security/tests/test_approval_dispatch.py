"""Approval-gated dispatch — BLOCKED queueing, standing-approval bypass, replay."""

from __future__ import annotations

import base64
import json
import time

import nacl.signing
import pytest

from demiurge.approvals.matcher import MatcherIndex, StandingApproval
from demiurge.approvals.queue import InMemoryApprovalQueue
from demiurge.audit import AuditWriter
from demiurge.canonical import canonical_encode
from demiurge.capabilities.registry import CapabilityRegistry
from demiurge.dispatch import build_dispatcher
from demiurge.identity import NonceCache, RegisteredAgent
from demiurge.policy import AgentPolicy, CapabilityRule, Policy


@pytest.fixture
def keypair():
    sk = nacl.signing.SigningKey.generate()
    return sk, sk.verify_key


def _sign(sk, *, caller, capability, params, nonce="n-0", ts=None, replay_request_id=None):
    req = {
        "v": 1,
        "caller": caller,
        "nonce": nonce,
        "ts": int(ts if ts is not None else time.time()),
        "capability": capability,
        "params": params,
    }
    scope = {k: req[k] for k in ("v", "caller", "nonce", "ts", "capability", "params")}
    sig = sk.sign(canonical_encode(scope)).signature
    req["sig"] = base64.b64encode(sig).decode("ascii")
    if replay_request_id is not None:
        req["replay_request_id"] = replay_request_id
    return req


def _read_audit(path):
    files = sorted(path.glob("*.jsonl"))
    if not files:
        return []
    out = []
    for f in files:
        for line in f.read_text().strip().split("\n"):
            if line:
                out.append(json.loads(line))
    return out


def _setup(tmp_path, keypair, *, requires_approval=True, rationale_required=False):
    sk, vk = keypair
    identity_registry = {
        "installer": RegisteredAgent(name="installer", verify_key=vk),
    }
    policy = Policy(
        agents={
            "installer": AgentPolicy(
                agent="installer",
                allow={
                    "system.execute_privileged": CapabilityRule(
                        name="system.execute_privileged",
                        requires_approval=requires_approval,
                        rationale_required=rationale_required,
                    )
                },
            )
        }
    )
    audit = AuditWriter(tmp_path)
    caps = CapabilityRegistry()

    @caps.capability("system.execute_privileged", clear_params=["mechanism", "rationale"])
    async def handler(agent, params):
        return {"executed": True, "mechanism": params.get("mechanism")}

    return sk, identity_registry, policy, audit, caps


@pytest.mark.asyncio
async def test_call_without_standing_returns_blocked_and_enqueues(tmp_path, keypair):
    sk, identity_registry, policy, audit, caps = _setup(tmp_path, keypair)
    queue = InMemoryApprovalQueue()
    dispatcher = build_dispatcher(
        identity_registry=identity_registry, policy=policy,
        audit_writer=audit, capability_registry=caps,
        nonce_cache=NonceCache(),
        matcher=MatcherIndex(),  # empty
        approval_queue=queue,
    )
    req = _sign(
        sk, caller="installer", capability="system.execute_privileged",
        params={"mechanism": "apt", "packages": ["tesseract-ocr"]},
    )
    resp = await dispatcher(req)
    assert resp["ok"] is False
    assert resp["error_code"] == "BLOCKED"
    assert "approval_request_id" in resp

    # Audit line.
    lines = _read_audit(tmp_path)
    blocked_lines = [l for l in lines if l["outcome"] == "blocked"]
    assert len(blocked_lines) == 1
    assert blocked_lines[0]["approval_request_id"] == resp["approval_request_id"]

    # Queue row.
    pending = await queue.list_pending()
    assert len(pending) == 1
    assert pending[0].caller == "installer"
    assert pending[0].capability == "system.execute_privileged"


@pytest.mark.asyncio
async def test_standing_approval_lets_call_through_silently(tmp_path, keypair):
    sk, identity_registry, policy, audit, caps = _setup(tmp_path, keypair)
    matcher = MatcherIndex(
        [
            StandingApproval(
                id="sa-1",
                capability="system.execute_privileged",
                caller="installer",
                predicates={"mechanism": "apt"},
            )
        ]
    )
    queue = InMemoryApprovalQueue()
    dispatcher = build_dispatcher(
        identity_registry=identity_registry, policy=policy,
        audit_writer=audit, capability_registry=caps,
        nonce_cache=NonceCache(), matcher=matcher, approval_queue=queue,
    )
    req = _sign(
        sk, caller="installer", capability="system.execute_privileged",
        params={"mechanism": "apt"},
    )
    resp = await dispatcher(req)
    assert resp["ok"] is True
    assert resp["result"]["executed"] is True

    # Audit line should record the approval_via.
    lines = _read_audit(tmp_path)
    ok = [l for l in lines if l["outcome"] == "ok"]
    assert len(ok) == 1
    assert ok[0]["approval_via"] == "standing/sa-1"

    # Nothing in queue.
    assert await queue.list_pending() == []


@pytest.mark.asyncio
async def test_standing_predicate_mismatch_falls_through_to_queue(tmp_path, keypair):
    sk, identity_registry, policy, audit, caps = _setup(tmp_path, keypair)
    matcher = MatcherIndex(
        [
            StandingApproval(
                id="sa-1",
                capability="system.execute_privileged",
                caller="installer",
                predicates={"mechanism": "pip"},  # wrong mechanism for this call
            )
        ]
    )
    queue = InMemoryApprovalQueue()
    dispatcher = build_dispatcher(
        identity_registry=identity_registry, policy=policy,
        audit_writer=audit, capability_registry=caps,
        nonce_cache=NonceCache(), matcher=matcher, approval_queue=queue,
    )
    req = _sign(
        sk, caller="installer", capability="system.execute_privileged",
        params={"mechanism": "apt"},
    )
    resp = await dispatcher(req)
    assert resp["error_code"] == "BLOCKED"
    assert len(await queue.list_pending()) == 1


@pytest.mark.asyncio
async def test_replay_bypasses_approval_gate(tmp_path, keypair):
    """When the operator approves and the dispatcher replays, gate is skipped."""
    sk, identity_registry, policy, audit, caps = _setup(tmp_path, keypair)
    matcher = MatcherIndex()
    queue = InMemoryApprovalQueue()
    approved_request_ids = set()

    dispatcher = build_dispatcher(
        identity_registry=identity_registry, policy=policy,
        audit_writer=audit, capability_registry=caps,
        nonce_cache=NonceCache(),
        matcher=matcher, approval_queue=queue,
        bypass_approval_for_request_id=lambda rid: rid in approved_request_ids,
    )

    # First call → BLOCKED.
    req = _sign(
        sk, caller="installer", capability="system.execute_privileged",
        params={"mechanism": "apt"}, nonce="n-1",
    )
    blocked = await dispatcher(req)
    request_id = blocked["approval_request_id"]

    # Sol approves.
    await queue.decide(
        request_id=request_id, status="approved", decided_by="sol",
    )
    approved_request_ids.add(request_id)

    # Replay — same envelope plus replay_request_id, fresh nonce.
    replay = _sign(
        sk, caller="installer", capability="system.execute_privileged",
        params={"mechanism": "apt"}, nonce="n-2",
        replay_request_id=request_id,
    )
    resp = await dispatcher(replay)
    assert resp["ok"] is True

    # Audit line for replay should record per_call/<id>.
    lines = _read_audit(tmp_path)
    ok = [l for l in lines if l["outcome"] == "ok"]
    assert len(ok) == 1
    assert ok[0]["approval_via"] == f"per_call/{request_id}"


@pytest.mark.asyncio
async def test_rationale_required_but_missing_denies(tmp_path, keypair):
    sk, identity_registry, policy, audit, caps = _setup(
        tmp_path, keypair, rationale_required=True,
    )
    queue = InMemoryApprovalQueue()
    dispatcher = build_dispatcher(
        identity_registry=identity_registry, policy=policy,
        audit_writer=audit, capability_registry=caps,
        nonce_cache=NonceCache(),
        matcher=MatcherIndex(), approval_queue=queue,
    )
    req = _sign(
        sk, caller="installer", capability="system.execute_privileged",
        params={"mechanism": "apt"},  # no rationale
    )
    resp = await dispatcher(req)
    assert resp["error_code"] == "DENY"
    assert "rationale required" in resp["message"]


@pytest.mark.asyncio
async def test_no_queue_configured_returns_internal(tmp_path, keypair):
    """Mis-configured: requires_approval=true but no queue → INTERNAL, not silent fail."""
    sk, identity_registry, policy, audit, caps = _setup(tmp_path, keypair)
    dispatcher = build_dispatcher(
        identity_registry=identity_registry, policy=policy,
        audit_writer=audit, capability_registry=caps,
        nonce_cache=NonceCache(),
        matcher=MatcherIndex(),
        approval_queue=None,
    )
    req = _sign(
        sk, caller="installer", capability="system.execute_privileged",
        params={"mechanism": "apt"},
    )
    resp = await dispatcher(req)
    assert resp["error_code"] == "INTERNAL"


@pytest.mark.asyncio
async def test_non_gated_capability_unaffected(tmp_path, keypair):
    """An allowed capability without requires_approval works as before."""
    sk, identity_registry, policy, audit, caps = _setup(
        tmp_path, keypair, requires_approval=False,
    )
    dispatcher = build_dispatcher(
        identity_registry=identity_registry, policy=policy,
        audit_writer=audit, capability_registry=caps,
        nonce_cache=NonceCache(),
        matcher=MatcherIndex(),
        approval_queue=InMemoryApprovalQueue(),
    )
    req = _sign(
        sk, caller="installer", capability="system.execute_privileged",
        params={"mechanism": "apt"},
    )
    resp = await dispatcher(req)
    assert resp["ok"] is True


@pytest.mark.asyncio
async def test_double_decide_raises(tmp_path):
    """Sanity test on the in-memory queue itself."""
    queue = InMemoryApprovalQueue()
    from demiurge.approvals.queue import ApprovalRequest, QueueError

    rid = "req-1"
    await queue.enqueue(
        request=ApprovalRequest(
            id=rid, capability="x", caller="c",
            params_summary="x", full_envelope={},
        )
    )
    await queue.decide(request_id=rid, status="approved", decided_by="sol")
    with pytest.raises(QueueError, match="already decided"):
        await queue.decide(request_id=rid, status="rejected", decided_by="sol")
