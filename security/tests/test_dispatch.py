"""Tests for the dispatch pipeline (identity → policy → capability → audit).

These tests wire the real components together but call ``dispatch(req)``
directly, bypassing the UDS server. End-to-end-through-the-socket lives
in ``test_end_to_end.py``.
"""

import asyncio
import base64
import json
import time

import nacl.signing
import pytest

from demiurge.audit import AuditWriter
from demiurge.canonical import canonical_encode
from demiurge.capabilities.registry import CapabilityRegistry
from demiurge.dispatch import build_dispatcher
from demiurge.identity import NonceCache, RegisteredAgent
from demiurge.policy import (
    AgentPolicy,
    CapabilityRule,
    Policy,
)


@pytest.fixture
def keypair():
    sk = nacl.signing.SigningKey.generate()
    return sk, sk.verify_key


def sign_request(
    sk: nacl.signing.SigningKey,
    *,
    caller="test_caller",
    capability="test.op",
    params=None,
    nonce="n-0",
    ts=None,
) -> dict:
    if params is None:
        params = {}
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
    return req


def read_audit_lines(path):
    return [json.loads(line) for line in path.read_text().strip().split("\n") if line]


@pytest.mark.asyncio
async def test_ok_path(tmp_path, keypair):
    sk, vk = keypair
    registry = {"caller_a": RegisteredAgent(name="caller_a", verify_key=vk)}
    policy = Policy(
        agents={
            "caller_a": AgentPolicy(
                agent="caller_a",
                allow={"test.op": CapabilityRule(name="test.op")},
            )
        }
    )
    audit = AuditWriter(tmp_path)
    caps = CapabilityRegistry()

    @caps.capability("test.op", clear_params=["safe_field"])
    async def handler(agent, params):
        assert agent.name == "caller_a"
        return {"ok_result": params.get("safe_field")}

    dispatch = build_dispatcher(
        identity_registry=registry,
        policy=policy,
        audit_writer=audit,
        capability_registry=caps,
        nonce_cache=NonceCache(),
    )
    req = sign_request(
        sk,
        caller="caller_a",
        capability="test.op",
        params={"safe_field": "visible", "secret_field": "hidden"},
    )
    resp = await dispatch(req)

    assert resp["ok"] is True
    assert resp["result"]["ok_result"] == "visible"
    assert "trace_id" in resp

    lines = read_audit_lines(next(tmp_path.iterdir()))
    assert len(lines) == 1
    entry = lines[0]
    assert entry["outcome"] == "ok"
    assert entry["caller"] == "caller_a"
    assert entry["capability"] == "test.op"
    assert entry["param_values"] == {"safe_field": "visible"}
    assert "secret_field" in entry["param_hashes"]
    # Defensive: the actual secret string must not appear anywhere in the log.
    raw_log = next(tmp_path.iterdir()).read_text()
    assert "hidden" not in raw_log


@pytest.mark.asyncio
async def test_auth_fail_unknown_caller(tmp_path, keypair):
    sk, _ = keypair
    registry: dict = {}  # nobody registered
    policy = Policy()
    audit = AuditWriter(tmp_path)
    dispatch = build_dispatcher(
        identity_registry=registry,
        policy=policy,
        audit_writer=audit,
        capability_registry=CapabilityRegistry(),
        nonce_cache=NonceCache(),
    )
    req = sign_request(sk, caller="ghost")
    resp = await dispatch(req)

    assert resp["ok"] is False
    assert resp["error_code"] == "AUTH"

    lines = read_audit_lines(next(tmp_path.iterdir()))
    assert len(lines) == 1
    assert lines[0]["outcome"] == "auth_fail"
    assert lines[0]["error_code"] == "AUTH"


@pytest.mark.asyncio
async def test_auth_fail_bad_signature(tmp_path, keypair):
    _, vk = keypair
    attacker = nacl.signing.SigningKey.generate()
    registry = {"caller_a": RegisteredAgent(name="caller_a", verify_key=vk)}
    audit = AuditWriter(tmp_path)
    dispatch = build_dispatcher(
        identity_registry=registry,
        policy=Policy(),
        audit_writer=audit,
        capability_registry=CapabilityRegistry(),
        nonce_cache=NonceCache(),
    )
    req = sign_request(attacker, caller="caller_a")
    resp = await dispatch(req)

    assert resp["error_code"] == "AUTH"
    lines = read_audit_lines(next(tmp_path.iterdir()))
    assert lines[0]["outcome"] == "auth_fail"


@pytest.mark.asyncio
async def test_deny_by_policy(tmp_path, keypair):
    sk, vk = keypair
    registry = {"caller_a": RegisteredAgent(name="caller_a", verify_key=vk)}
    policy = Policy(
        agents={
            "caller_a": AgentPolicy(
                agent="caller_a",
                allow={"allowed.op": CapabilityRule(name="allowed.op")},
            )
        }
    )
    audit = AuditWriter(tmp_path)
    dispatch = build_dispatcher(
        identity_registry=registry,
        policy=policy,
        audit_writer=audit,
        capability_registry=CapabilityRegistry(),
        nonce_cache=NonceCache(),
    )
    req = sign_request(sk, caller="caller_a", capability="forbidden.op")
    resp = await dispatch(req)

    assert resp["ok"] is False
    assert resp["error_code"] == "DENY"
    lines = read_audit_lines(next(tmp_path.iterdir()))
    assert lines[0]["outcome"] == "deny"
    assert lines[0]["capability"] == "forbidden.op"


@pytest.mark.asyncio
async def test_notfound_capability(tmp_path, keypair):
    # Caller authenticated + policy allows, but no capability registered.
    sk, vk = keypair
    registry = {"caller_a": RegisteredAgent(name="caller_a", verify_key=vk)}
    policy = Policy(
        agents={
            "caller_a": AgentPolicy(
                agent="caller_a",
                allow={"ghost.op": CapabilityRule(name="ghost.op")},
            )
        }
    )
    audit = AuditWriter(tmp_path)
    dispatch = build_dispatcher(
        identity_registry=registry,
        policy=policy,
        audit_writer=audit,
        capability_registry=CapabilityRegistry(),  # empty
        nonce_cache=NonceCache(),
    )
    req = sign_request(sk, caller="caller_a", capability="ghost.op")
    resp = await dispatch(req)

    assert resp["error_code"] == "NOTFOUND"
    lines = read_audit_lines(next(tmp_path.iterdir()))
    assert lines[0]["outcome"] == "notfound"


@pytest.mark.asyncio
async def test_internal_handler_exception(tmp_path, keypair):
    sk, vk = keypair
    registry = {"caller_a": RegisteredAgent(name="caller_a", verify_key=vk)}
    policy = Policy(
        agents={
            "caller_a": AgentPolicy(
                agent="caller_a",
                allow={"boom": CapabilityRule(name="boom")},
            )
        }
    )
    audit = AuditWriter(tmp_path)
    caps = CapabilityRegistry()

    @caps.capability("boom")
    async def boom(agent, params):
        raise RuntimeError("kaboom")

    dispatch = build_dispatcher(
        identity_registry=registry,
        policy=policy,
        audit_writer=audit,
        capability_registry=caps,
        nonce_cache=NonceCache(),
    )
    req = sign_request(sk, caller="caller_a", capability="boom")
    resp = await dispatch(req)

    assert resp["error_code"] == "INTERNAL"
    assert "kaboom" in resp["message"]
    lines = read_audit_lines(next(tmp_path.iterdir()))
    assert lines[0]["outcome"] == "internal"
    assert "kaboom" in lines[0]["extra"]["error"]


@pytest.mark.asyncio
async def test_account_id_is_clear_not_hashed(tmp_path, keypair):
    sk, vk = keypair
    registry = {"caller_a": RegisteredAgent(name="caller_a", verify_key=vk)}
    policy = Policy(
        agents={
            "caller_a": AgentPolicy(
                agent="caller_a",
                allow={
                    "op": CapabilityRule(
                        name="op", account_patterns=["gmail.*"]
                    )
                },
            )
        }
    )
    audit = AuditWriter(tmp_path)
    caps = CapabilityRegistry()

    @caps.capability("op")
    async def op(agent, params):
        return {"done": True}

    dispatch = build_dispatcher(
        identity_registry=registry,
        policy=policy,
        audit_writer=audit,
        capability_registry=caps,
        nonce_cache=NonceCache(),
    )
    req = sign_request(
        sk, caller="caller_a", capability="op", params={"account_id": "gmail.personal"}
    )
    resp = await dispatch(req)

    assert resp["ok"] is True
    lines = read_audit_lines(next(tmp_path.iterdir()))
    assert lines[0]["account_id"] == "gmail.personal"
    assert lines[0]["param_values"] == {"account_id": "gmail.personal"}
    assert "account_id" not in lines[0]["param_hashes"]
