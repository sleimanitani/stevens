"""End-to-end: signed request → UDS → full dispatch pipeline → response.

The one test the whole step-6 milestone is aimed at: prove that identity,
policy, capability registry, and audit all flow through the UDS server
correctly for a real round-trip.
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
from demiurge.framing import read_frame, write_frame
from demiurge.identity import NonceCache, RegisteredAgent
from demiurge.policy import AgentPolicy, CapabilityRule, Policy
from demiurge.server import start_server


def sign(sk, *, caller, capability, params=None, nonce="n-1", ts=None):
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
    req["sig"] = base64.b64encode(sk.sign(canonical_encode(scope)).signature).decode("ascii")
    return req


async def send(socket_path, request):
    reader, writer = await asyncio.open_unix_connection(socket_path)
    try:
        await write_frame(writer, request)
        return await read_frame(reader)
    finally:
        writer.close()
        await writer.wait_closed()


async def build_ping_server(tmp_path):
    """Shared fixture: server with dev_tester allowed to call ping."""
    from demiurge.capabilities.ping import ping  # ensure ping registered

    sk = nacl.signing.SigningKey.generate()
    identity_registry = {
        "dev_tester": RegisteredAgent(name="dev_tester", verify_key=sk.verify_key),
    }
    policy = Policy(
        agents={
            "dev_tester": AgentPolicy(
                agent="dev_tester",
                allow={"ping": CapabilityRule(name="ping")},
            )
        }
    )
    audit = AuditWriter(tmp_path / "audit")
    caps = CapabilityRegistry()
    caps.register("ping", ping)

    socket_path = str(tmp_path / "sec.sock")
    dispatch = build_dispatcher(
        identity_registry=identity_registry,
        policy=policy,
        audit_writer=audit,
        capability_registry=caps,
        nonce_cache=NonceCache(),
    )
    server = await start_server(socket_path, dispatch=dispatch)
    return {
        "server": server,
        "socket_path": socket_path,
        "sk": sk,
        "audit_dir": tmp_path / "audit",
    }


@pytest.mark.asyncio
async def test_end_to_end_ping_ok(tmp_path):
    ctx = await build_ping_server(tmp_path)
    try:
        req = sign(ctx["sk"], caller="dev_tester", capability="ping")
        resp = await send(ctx["socket_path"], req)
    finally:
        ctx["server"].close()
        await ctx["server"].wait_closed()

    assert resp["ok"] is True
    assert resp["result"]["pong"] is True
    assert isinstance(resp["result"]["server_time"], int)
    assert "trace_id" in resp

    audit_files = list(ctx["audit_dir"].iterdir())
    assert len(audit_files) == 1
    lines = audit_files[0].read_text().strip().split("\n")
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["outcome"] == "ok"
    assert entry["caller"] == "dev_tester"
    assert entry["capability"] == "ping"
    assert entry["trace_id"] == resp["trace_id"]


@pytest.mark.asyncio
async def test_end_to_end_auth_fail_wrong_key(tmp_path):
    ctx = await build_ping_server(tmp_path)
    try:
        attacker = nacl.signing.SigningKey.generate()
        req = sign(attacker, caller="dev_tester", capability="ping")
        resp = await send(ctx["socket_path"], req)
    finally:
        ctx["server"].close()
        await ctx["server"].wait_closed()

    assert resp["ok"] is False
    assert resp["error_code"] == "AUTH"
    lines = (ctx["audit_dir"] / next(iter(p.name for p in ctx["audit_dir"].iterdir()))).read_text()
    assert json.loads(lines.strip())["outcome"] == "auth_fail"


@pytest.mark.asyncio
async def test_end_to_end_deny_unauthorized_capability(tmp_path):
    ctx = await build_ping_server(tmp_path)
    try:
        # dev_tester is only allowed to call ping — asking for anything else denies.
        req = sign(ctx["sk"], caller="dev_tester", capability="gmail.send_draft")
        resp = await send(ctx["socket_path"], req)
    finally:
        ctx["server"].close()
        await ctx["server"].wait_closed()

    assert resp["ok"] is False
    assert resp["error_code"] == "DENY"
    audit_files = list(ctx["audit_dir"].iterdir())
    assert len(audit_files) == 1
    entry = json.loads(audit_files[0].read_text().strip())
    assert entry["outcome"] == "deny"
    assert entry["capability"] == "gmail.send_draft"
