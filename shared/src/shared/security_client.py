"""Client library for the Stevens Security Agent.

Every component that needs a credential or wants to perform a sensitive
action goes through this. The client signs a request with its own
Ed25519 private key, opens a Unix domain socket to the Security Agent,
exchanges a framed msgpack request/response, and raises a typed
exception on error.

The protocol is fully specified in ``docs/protocols/security-agent.md``.
This module is the Python reference implementation.
"""

from __future__ import annotations

import asyncio
import base64
import os
import secrets
import struct
import time
from typing import Any, Dict, Optional

import msgpack
import nacl.signing

from .canonical import canonical_encode

_LENGTH_PREFIX = struct.Struct(">I")
_MAX_PAYLOAD_BYTES = 1 << 20
_DEFAULT_TIMEOUT_SECONDS = 10.0
_PROTOCOL_VERSION = 1


class SecurityClientError(Exception):
    """Base class for security-client errors."""


class TransportError(SecurityClientError):
    """UDS connection, framing, or timeout failure."""


class ResponseError(SecurityClientError):
    """Server returned an error response."""

    def __init__(self, code: str, message: str, trace_id: Optional[str]) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.trace_id = trace_id


class AuthError(ResponseError):
    """Server returned error_code=AUTH."""


class DenyError(ResponseError):
    """Server returned error_code=DENY."""


class NotFoundError(ResponseError):
    """Server returned error_code=NOTFOUND."""


class RateError(ResponseError):
    """Server returned error_code=RATE."""


class InternalError(ResponseError):
    """Server returned error_code=INTERNAL."""


_ERR_CLASSES = {
    "AUTH": AuthError,
    "DENY": DenyError,
    "NOTFOUND": NotFoundError,
    "RATE": RateError,
    "INTERNAL": InternalError,
}


def _fresh_nonce() -> str:
    return base64.b64encode(secrets.token_bytes(32)).decode("ascii")


class SecurityClient:
    """Async client for the Stevens Security Agent.

    Typical use::

        client = SecurityClient.from_key_file(
            socket_path="/run/stevens/security.sock",
            caller_name="email_pm",
            private_key_path="/run/stevens/keys/email_pm.key",
        )
        result = await client.call("gmail.send_draft", {
            "account_id": "gmail.personal",
            "draft_id": "r-abc123",
        })
    """

    def __init__(
        self,
        *,
        socket_path: str,
        caller_name: str,
        private_key_b64: str,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._socket_path = socket_path
        self._caller = caller_name
        self._sk = nacl.signing.SigningKey(base64.b64decode(private_key_b64))
        self._timeout = timeout_seconds

    @classmethod
    def from_key_file(
        cls,
        *,
        socket_path: str,
        caller_name: str,
        private_key_path: str,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> "SecurityClient":
        """Load a base64 Ed25519 private key from a file (produced by gen_test_keypair)."""
        with open(private_key_path, "r", encoding="utf-8") as f:
            private_key_b64 = f.read().strip()
        mode = os.stat(private_key_path).st_mode & 0o777
        if mode & 0o077:
            # Key file is group/world-accessible — refuse to use it.
            raise TransportError(
                f"private key at {private_key_path} has permissive mode {mode:o}; "
                "must be 0o600 or tighter"
            )
        return cls(
            socket_path=socket_path,
            caller_name=caller_name,
            private_key_b64=private_key_b64,
            timeout_seconds=timeout_seconds,
        )

    async def call(
        self,
        capability: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Invoke ``capability`` on the broker. Returns the ``result`` dict on success.

        Raises the appropriate :class:`ResponseError` subclass on server error,
        or :class:`TransportError` if the socket / framing / timeout fails.
        """
        params_dict: Dict[str, Any] = params or {}
        envelope = self._build_signed(capability, params_dict)
        payload = msgpack.packb(envelope, use_bin_type=True)
        if len(payload) > _MAX_PAYLOAD_BYTES:
            raise TransportError(
                f"outbound payload {len(payload)} bytes exceeds max {_MAX_PAYLOAD_BYTES}"
            )

        try:
            resp = await asyncio.wait_for(
                self._exchange(payload), timeout=self._timeout
            )
        except asyncio.TimeoutError as e:
            raise TransportError(f"timed out after {self._timeout}s") from e
        except (OSError, ConnectionError) as e:
            raise TransportError(f"unix socket error: {e}") from e

        if not isinstance(resp, dict):
            raise TransportError("malformed response: not a map")

        if resp.get("ok") is True:
            result = resp.get("result")
            return result if isinstance(result, dict) else {}

        code = str(resp.get("error_code") or "INTERNAL")
        message = str(resp.get("message") or "")
        trace_id = resp.get("trace_id")
        err_cls = _ERR_CLASSES.get(code, ResponseError)
        raise err_cls(code, message, trace_id if isinstance(trace_id, str) else None)

    def _build_signed(self, capability: str, params: Dict[str, Any]) -> Dict[str, Any]:
        envelope = {
            "v": _PROTOCOL_VERSION,
            "caller": self._caller,
            "nonce": _fresh_nonce(),
            "ts": int(time.time()),
            "capability": capability,
            "params": params,
        }
        sig = self._sk.sign(canonical_encode(envelope)).signature
        envelope["sig"] = base64.b64encode(sig).decode("ascii")
        return envelope

    async def _exchange(self, payload: bytes) -> Any:
        reader, writer = await asyncio.open_unix_connection(self._socket_path)
        try:
            writer.write(_LENGTH_PREFIX.pack(len(payload)) + payload)
            await writer.drain()
            header = await reader.readexactly(_LENGTH_PREFIX.size)
            (length,) = _LENGTH_PREFIX.unpack(header)
            if length > _MAX_PAYLOAD_BYTES:
                raise TransportError(
                    f"response length {length} exceeds max {_MAX_PAYLOAD_BYTES}"
                )
            body = await reader.readexactly(length)
            return msgpack.unpackb(body, raw=False)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass
