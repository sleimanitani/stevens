"""Tests for shared.security_client.

Wires a real Security Agent server (from demiurge) with a minimal
policy + registry, then exercises the client against it over a real UDS.
"""

import asyncio
import base64
import os

import nacl.signing
import pytest

from shared.security_client import (
    AuthError,
    DenyError,
    NotFoundError,
    SecurityClient,
    TransportError,
)
from demiurge.audit import AuditWriter
from demiurge.capabilities.ping import ping
from demiurge.capabilities.registry import CapabilityRegistry
from demiurge.dispatch import build_dispatcher
from demiurge.identity import NonceCache, RegisteredAgent
from demiurge.policy import AgentPolicy, CapabilityRule, Policy
from demiurge.server import start_server


async def _start_ping_server(tmp_path, caller_name: str, allow_ping: bool = True):
    sk = nacl.signing.SigningKey.generate()
    identity_registry = {
        caller_name: RegisteredAgent(name=caller_name, verify_key=sk.verify_key),
    }
    allow = {"ping": CapabilityRule(name="ping")} if allow_ping else {}
    policy = Policy(agents={caller_name: AgentPolicy(agent=caller_name, allow=allow)})
    audit = AuditWriter(tmp_path / "audit")
    caps = CapabilityRegistry()
    caps.register("ping", ping)

    socket_path = str(tmp_path / "sec.sock")
    dispatcher = build_dispatcher(
        identity_registry=identity_registry,
        policy=policy,
        audit_writer=audit,
        capability_registry=caps,
        nonce_cache=NonceCache(),
    )
    server = await start_server(socket_path, dispatch=dispatcher)
    return server, socket_path, sk


def _sk_to_b64(sk: nacl.signing.SigningKey) -> str:
    return base64.b64encode(bytes(sk)).decode("ascii")


@pytest.mark.asyncio
async def test_client_ping_roundtrip(tmp_path):
    server, socket_path, sk = await _start_ping_server(tmp_path, "dev_tester")
    try:
        client = SecurityClient(
            socket_path=socket_path,
            caller_name="dev_tester",
            private_key_b64=_sk_to_b64(sk),
        )
        result = await client.call("ping", {})
    finally:
        server.close()
        await server.wait_closed()

    assert result["pong"] is True
    assert isinstance(result["server_time"], int)


@pytest.mark.asyncio
async def test_client_wrong_key_raises_autherror(tmp_path):
    server, socket_path, _ = await _start_ping_server(tmp_path, "dev_tester")
    try:
        attacker_sk = nacl.signing.SigningKey.generate()
        client = SecurityClient(
            socket_path=socket_path,
            caller_name="dev_tester",
            private_key_b64=_sk_to_b64(attacker_sk),
        )
        with pytest.raises(AuthError) as exc:
            await client.call("ping", {})
        assert exc.value.code == "AUTH"
        assert exc.value.trace_id is not None
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_client_unknown_caller_raises_autherror(tmp_path):
    server, socket_path, sk = await _start_ping_server(tmp_path, "dev_tester")
    try:
        client = SecurityClient(
            socket_path=socket_path,
            caller_name="ghost",
            private_key_b64=_sk_to_b64(sk),
        )
        with pytest.raises(AuthError):
            await client.call("ping", {})
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_client_policy_deny_raises_denyerror(tmp_path):
    server, socket_path, sk = await _start_ping_server(
        tmp_path, "dev_tester", allow_ping=False
    )
    try:
        client = SecurityClient(
            socket_path=socket_path,
            caller_name="dev_tester",
            private_key_b64=_sk_to_b64(sk),
        )
        with pytest.raises(DenyError) as exc:
            await client.call("ping", {})
        assert exc.value.code == "DENY"
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_client_unknown_capability_raises_notfound(tmp_path):
    # Allow the capability name in policy but don't register it in the
    # capability registry — that produces a NOTFOUND, not DENY.
    sk = nacl.signing.SigningKey.generate()
    identity_registry = {
        "dev_tester": RegisteredAgent(name="dev_tester", verify_key=sk.verify_key),
    }
    policy = Policy(
        agents={
            "dev_tester": AgentPolicy(
                agent="dev_tester",
                allow={"nothing.here": CapabilityRule(name="nothing.here")},
            )
        }
    )
    audit = AuditWriter(tmp_path / "audit")
    caps = CapabilityRegistry()  # empty
    socket_path = str(tmp_path / "sec.sock")
    dispatcher = build_dispatcher(
        identity_registry=identity_registry,
        policy=policy,
        audit_writer=audit,
        capability_registry=caps,
        nonce_cache=NonceCache(),
    )
    server = await start_server(socket_path, dispatch=dispatcher)
    try:
        client = SecurityClient(
            socket_path=socket_path,
            caller_name="dev_tester",
            private_key_b64=_sk_to_b64(sk),
        )
        with pytest.raises(NotFoundError):
            await client.call("nothing.here", {})
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_client_no_server_raises_transporterror(tmp_path):
    sk = nacl.signing.SigningKey.generate()
    client = SecurityClient(
        socket_path=str(tmp_path / "no-such-socket.sock"),
        caller_name="dev_tester",
        private_key_b64=_sk_to_b64(sk),
    )
    with pytest.raises(TransportError):
        await client.call("ping", {})


@pytest.mark.asyncio
async def test_client_short_timeout_raises_transporterror(tmp_path):
    # Start a server that deliberately sleeps before responding so the client
    # sees the timeout path (not a "connection refused" path).
    socket_path = str(tmp_path / "slow.sock")

    async def handler(reader, writer):
        await reader.readexactly(4)
        await asyncio.sleep(1.0)
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_unix_server(handler, path=socket_path)
    try:
        sk = nacl.signing.SigningKey.generate()
        client = SecurityClient(
            socket_path=socket_path,
            caller_name="dev_tester",
            private_key_b64=_sk_to_b64(sk),
            timeout_seconds=0.1,
        )
        with pytest.raises(TransportError):
            await client.call("ping", {})
    finally:
        server.close()
        await server.wait_closed()


def test_from_key_file_refuses_permissive_mode(tmp_path):
    sk = nacl.signing.SigningKey.generate()
    p = tmp_path / "bad_perms.key"
    p.write_text(_sk_to_b64(sk) + "\n")
    os.chmod(p, 0o644)  # group/world readable
    with pytest.raises(TransportError, match="permissive mode"):
        SecurityClient.from_key_file(
            socket_path="/tmp/nope",
            caller_name="x",
            private_key_path=str(p),
        )


def test_from_key_file_accepts_0600(tmp_path):
    sk = nacl.signing.SigningKey.generate()
    p = tmp_path / "ok.key"
    p.write_text(_sk_to_b64(sk) + "\n")
    os.chmod(p, 0o600)
    client = SecurityClient.from_key_file(
        socket_path="/tmp/nope",
        caller_name="x",
        private_key_path=str(p),
    )
    assert client is not None
