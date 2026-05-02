"""Caller identity and Ed25519 signature verification.

The Security Agent authenticates every RPC request by verifying an Ed25519
signature over the canonical encoding of the envelope. Callers are
registered by name; each name maps to a single public key.

This module implements, in order, the checks that must pass before any
request body is dispatched:

1. Envelope shape — has the required fields with the right types.
2. Timestamp freshness — within ``CLOCK_SKEW_SECONDS`` of server time.
3. Nonce freshness — not in the recent-nonce LRU.
4. Caller known — listed in the agents registry.
5. Signature valid — Ed25519 verify over the canonical encoding.

All failures raise :class:`AuthError`. The caller is added to the LRU
*after* signature verification succeeds, so an attacker can't poison the
cache with a bogus request.
"""

from __future__ import annotations

import base64
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import nacl.exceptions
import nacl.signing
import yaml

from .canonical import CanonicalEncodingError, canonical_encode

CLOCK_SKEW_SECONDS = 60
NONCE_LRU_SIZE = 100_000
NONCE_TTL_SECONDS = 300  # 5 minutes — outlives CLOCK_SKEW by design
SUPPORTED_PROTOCOL_VERSION = 1

_REQUIRED_FIELDS = {
    "v": int,
    "caller": str,
    "nonce": str,
    "ts": int,
    "capability": str,
    "params": dict,
    "sig": str,
}


class AuthError(Exception):
    """Authentication or replay-protection failure."""


@dataclass(frozen=True)
class RegisteredAgent:
    name: str
    verify_key: nacl.signing.VerifyKey


def load_agents_registry(path: Path) -> Dict[str, RegisteredAgent]:
    """Load ``agents.yaml`` into a {name: RegisteredAgent} map.

    Schema (one entry per agent)::

        agents:
          - name: email_pm
            pubkey_b64: <base64 Ed25519 public key, 32 bytes>

    Missing file → empty registry (server starts but rejects all callers).
    """
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text()) or {}
    entries = data.get("agents") or []
    registry: Dict[str, RegisteredAgent] = {}
    for raw in entries:
        name = raw["name"]
        if name in registry:
            raise ValueError(f"duplicate agent registration: {name}")
        key_bytes = base64.b64decode(raw["pubkey_b64"])
        registry[name] = RegisteredAgent(
            name=name,
            verify_key=nacl.signing.VerifyKey(key_bytes),
        )
    return registry


class NonceCache:
    """Bounded time-windowed nonce cache to prevent replay."""

    def __init__(
        self,
        max_size: int = NONCE_LRU_SIZE,
        ttl_seconds: int = NONCE_TTL_SECONDS,
        now: Optional[Any] = None,
    ) -> None:
        self._seen: "OrderedDict[str, float]" = OrderedDict()
        self._max = max_size
        self._ttl = ttl_seconds
        self._now = now or time.time

    def check_and_add(self, nonce: str) -> bool:
        """Return True and record the nonce if unseen; False if it's a replay.

        Expired entries are evicted on check; the cache also drops the oldest
        entries when it exceeds ``max_size``.
        """
        now = self._now()
        # Evict expired entries from the front.
        while self._seen:
            k, ts = next(iter(self._seen.items()))
            if now - ts > self._ttl:
                self._seen.popitem(last=False)
            else:
                break
        if nonce in self._seen:
            return False
        self._seen[nonce] = now
        while len(self._seen) > self._max:
            self._seen.popitem(last=False)
        return True


def _check_envelope(req: Dict[str, Any]) -> None:
    for field, typ in _REQUIRED_FIELDS.items():
        if field not in req:
            raise AuthError(f"missing required field: {field}")
        value = req[field]
        if typ is int and isinstance(value, bool):
            raise AuthError(f"field {field} has wrong type: bool is not int")
        if not isinstance(value, typ):
            raise AuthError(
                f"field {field} has wrong type: expected {typ.__name__}, got {type(value).__name__}"
            )
    if req["v"] != SUPPORTED_PROTOCOL_VERSION:
        raise AuthError(f"unsupported protocol version: {req['v']}")


def _signed_payload(req: Dict[str, Any]) -> bytes:
    scope = {k: req[k] for k in ("v", "caller", "nonce", "ts", "capability", "params")}
    try:
        return canonical_encode(scope)
    except CanonicalEncodingError as e:
        raise AuthError(f"request not canonically encodable: {e}") from e


def verify_request(
    req: Dict[str, Any],
    registry: Dict[str, RegisteredAgent],
    nonce_cache: NonceCache,
    *,
    now: Optional[Any] = None,
) -> RegisteredAgent:
    """Run every auth check. Return the registered caller on success.

    Order matters — cheap checks first, crypto last.
    """
    _check_envelope(req)

    current_ts = (now or time.time)()
    skew = abs(current_ts - req["ts"])
    if skew > CLOCK_SKEW_SECONDS:
        raise AuthError(f"timestamp skew {skew:.0f}s exceeds {CLOCK_SKEW_SECONDS}s")

    caller_name = req["caller"]
    agent = registry.get(caller_name)
    if agent is None:
        raise AuthError(f"unknown caller: {caller_name!r}")

    try:
        sig_bytes = base64.b64decode(req["sig"], validate=True)
    except Exception as e:  # noqa: BLE001
        raise AuthError(f"signature is not valid base64: {e}") from e

    signed = _signed_payload(req)
    try:
        agent.verify_key.verify(signed, sig_bytes)
    except nacl.exceptions.BadSignatureError:
        raise AuthError("signature verification failed") from None

    # Nonce check is last so signature-failing replay attempts don't thrash it.
    if not nonce_cache.check_and_add(req["nonce"]):
        raise AuthError("nonce replay")

    return agent
