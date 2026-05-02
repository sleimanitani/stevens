"""Tests for identity verification — envelope shape, timestamp window,
pubkey registry, Ed25519 signature, nonce replay."""

import base64
import time

import nacl.signing
import pytest
import yaml

from demiurge.canonical import canonical_encode
from demiurge.identity import (
    CLOCK_SKEW_SECONDS,
    AuthError,
    NonceCache,
    RegisteredAgent,
    load_agents_registry,
    verify_request,
)


def make_request(
    caller: str = "test_caller",
    capability: str = "ping",
    params: dict | None = None,
    nonce: str = "nonce-0",
    ts: int | None = None,
    v: int = 1,
) -> dict:
    return {
        "v": v,
        "caller": caller,
        "nonce": nonce,
        "ts": int(ts if ts is not None else time.time()),
        "capability": capability,
        "params": params if params is not None else {},
    }


def sign(req: dict, signing_key: nacl.signing.SigningKey) -> dict:
    scope = {k: req[k] for k in ("v", "caller", "nonce", "ts", "capability", "params")}
    sig = signing_key.sign(canonical_encode(scope)).signature
    signed = dict(req)
    signed["sig"] = base64.b64encode(sig).decode("ascii")
    return signed


@pytest.fixture
def keypair():
    sk = nacl.signing.SigningKey.generate()
    return sk, sk.verify_key


@pytest.fixture
def registry(keypair):
    _, vk = keypair
    return {
        "test_caller": RegisteredAgent(name="test_caller", verify_key=vk),
    }


def test_valid_request_accepted(keypair, registry):
    sk, _ = keypair
    req = sign(make_request(), sk)
    agent = verify_request(req, registry, NonceCache())
    assert agent.name == "test_caller"


def test_missing_field_rejected(keypair, registry):
    sk, _ = keypair
    req = sign(make_request(), sk)
    del req["caller"]
    with pytest.raises(AuthError, match="missing required field"):
        verify_request(req, registry, NonceCache())


def test_wrong_type_rejected(keypair, registry):
    sk, _ = keypair
    req = sign(make_request(), sk)
    req["ts"] = "not-an-int"
    with pytest.raises(AuthError, match="wrong type"):
        verify_request(req, registry, NonceCache())


def test_bool_is_not_accepted_as_int(keypair, registry):
    sk, _ = keypair
    req = sign(make_request(), sk)
    req["ts"] = True  # bool subclasses int in Python — must still reject
    with pytest.raises(AuthError, match="wrong type"):
        verify_request(req, registry, NonceCache())


def test_unsupported_version_rejected(keypair, registry):
    sk, _ = keypair
    req = sign(make_request(v=99), sk)
    with pytest.raises(AuthError, match="unsupported protocol version"):
        verify_request(req, registry, NonceCache())


def test_stale_timestamp_rejected(keypair, registry):
    sk, _ = keypair
    req = sign(make_request(ts=int(time.time()) - CLOCK_SKEW_SECONDS - 5), sk)
    with pytest.raises(AuthError, match="timestamp skew"):
        verify_request(req, registry, NonceCache())


def test_future_timestamp_rejected(keypair, registry):
    sk, _ = keypair
    req = sign(make_request(ts=int(time.time()) + CLOCK_SKEW_SECONDS + 5), sk)
    with pytest.raises(AuthError, match="timestamp skew"):
        verify_request(req, registry, NonceCache())


def test_unknown_caller_rejected(keypair, registry):
    sk, _ = keypair
    req = sign(make_request(caller="ghost"), sk)
    with pytest.raises(AuthError, match="unknown caller"):
        verify_request(req, registry, NonceCache())


def test_wrong_key_rejected(registry):
    attacker_sk = nacl.signing.SigningKey.generate()
    req = sign(make_request(), attacker_sk)
    with pytest.raises(AuthError, match="signature verification failed"):
        verify_request(req, registry, NonceCache())


def test_tampered_payload_rejected(keypair, registry):
    sk, _ = keypair
    req = sign(make_request(), sk)
    req["capability"] = "gmail.send"  # swap capability after signing
    with pytest.raises(AuthError, match="signature verification failed"):
        verify_request(req, registry, NonceCache())


def test_tampered_params_rejected(keypair, registry):
    sk, _ = keypair
    req = sign(make_request(params={"amount": 10}), sk)
    req["params"] = {"amount": 10000}
    with pytest.raises(AuthError, match="signature verification failed"):
        verify_request(req, registry, NonceCache())


def test_bad_signature_base64_rejected(keypair, registry):
    sk, _ = keypair
    req = sign(make_request(), sk)
    req["sig"] = "!not-base64!"
    with pytest.raises(AuthError, match="not valid base64"):
        verify_request(req, registry, NonceCache())


def test_nonce_replay_rejected(keypair, registry):
    sk, _ = keypair
    cache = NonceCache()
    req1 = sign(make_request(nonce="same"), sk)
    verify_request(req1, registry, cache)
    # Re-sign at a fresh ts but reuse the nonce.
    req2 = sign(make_request(nonce="same"), sk)
    with pytest.raises(AuthError, match="nonce replay"):
        verify_request(req2, registry, cache)


def test_nonce_cache_not_poisoned_by_failed_signature(keypair, registry):
    _, _ = keypair
    attacker_sk = nacl.signing.SigningKey.generate()
    cache = NonceCache()
    bad = sign(make_request(nonce="unique"), attacker_sk)
    with pytest.raises(AuthError, match="signature"):
        verify_request(bad, registry, cache)
    # A later legitimate request with the same nonce must still succeed —
    # the bad request must not have consumed the nonce.
    sk = nacl.signing.SigningKey.generate()
    good_registry = {
        "test_caller": RegisteredAgent(name="test_caller", verify_key=sk.verify_key),
    }
    good = sign(make_request(nonce="unique"), sk)
    agent = verify_request(good, good_registry, cache)
    assert agent.name == "test_caller"


def test_nonce_cache_expires():
    current = [1000.0]

    def now():
        return current[0]

    cache = NonceCache(ttl_seconds=60, now=now)
    assert cache.check_and_add("n1") is True
    assert cache.check_and_add("n1") is False
    current[0] = 1500.0  # well past TTL
    # After TTL, same nonce becomes fresh again (replay protection is
    # time-bounded by design).
    assert cache.check_and_add("n1") is True


def test_nonce_cache_bounded_size():
    cache = NonceCache(max_size=2)
    assert cache.check_and_add("a") is True
    assert cache.check_and_add("b") is True
    assert cache.check_and_add("c") is True  # evicts "a"
    # "a" is now forgotten — a "replay" of a is accepted.
    assert cache.check_and_add("a") is True


def test_load_registry_missing_file(tmp_path):
    path = tmp_path / "nope.yaml"
    assert load_agents_registry(path) == {}


def test_load_registry_empty(tmp_path):
    path = tmp_path / "agents.yaml"
    path.write_text("agents: []\n")
    assert load_agents_registry(path) == {}


def test_load_registry_one_agent(tmp_path):
    sk = nacl.signing.SigningKey.generate()
    pubkey_b64 = base64.b64encode(bytes(sk.verify_key)).decode("ascii")
    path = tmp_path / "agents.yaml"
    path.write_text(
        yaml.safe_dump({"agents": [{"name": "email_pm", "pubkey_b64": pubkey_b64}]})
    )
    reg = load_agents_registry(path)
    assert "email_pm" in reg
    assert bytes(reg["email_pm"].verify_key) == bytes(sk.verify_key)


def test_load_registry_duplicate_name_rejected(tmp_path):
    sk = nacl.signing.SigningKey.generate()
    pubkey_b64 = base64.b64encode(bytes(sk.verify_key)).decode("ascii")
    path = tmp_path / "agents.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "agents": [
                    {"name": "dup", "pubkey_b64": pubkey_b64},
                    {"name": "dup", "pubkey_b64": pubkey_b64},
                ]
            }
        )
    )
    with pytest.raises(ValueError, match="duplicate"):
        load_agents_registry(path)
