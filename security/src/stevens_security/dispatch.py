"""Request dispatch pipeline.

One function: :func:`build_dispatcher` returns a coroutine suitable for
:func:`stevens_security.server.start_server`'s ``dispatch`` argument. It
orchestrates the full request path:

    frame decoded → identity verify → policy evaluate → capability lookup
    → capability handler → audit write → response

Every code path — success, auth failure, policy deny, not found, internal
error — produces exactly one audit line. This is the invariant the audit
log depends on for completeness.

Sensitive parameters (anything not listed in a capability's ``clear_params``)
are SHA-256 hashed before being written to audit. The clear value never
touches disk.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .audit import AuditEntry, AuditWriter, hash_param
from .capabilities.registry import CapabilityRegistry
from .identity import AuthError, NonceCache, RegisteredAgent, verify_request
from .policy import Policy, evaluate
from .server import Dispatcher


def build_dispatcher(
    *,
    identity_registry: Dict[str, RegisteredAgent],
    policy: Policy,
    audit_writer: AuditWriter,
    capability_registry: CapabilityRegistry,
    nonce_cache: NonceCache,
) -> Dispatcher:
    """Return an async dispatch function with all dependencies bound."""

    async def dispatch(req: Dict[str, Any]) -> Dict[str, Any]:
        t0 = time.monotonic()
        trace_id = str(uuid.uuid4())
        ts = datetime.now(timezone.utc).isoformat()

        # --- 1. Identity ---
        try:
            agent = verify_request(req, identity_registry, nonce_cache)
        except AuthError as e:
            await audit_writer.log(
                AuditEntry(
                    ts=ts,
                    trace_id=trace_id,
                    outcome="auth_fail",
                    error_code="AUTH",
                    caller=_safe_get(req, "caller"),
                    capability=_safe_get(req, "capability"),
                    latency_ms=_elapsed_ms(t0),
                    extra={"reason": str(e)},
                )
            )
            return _error(trace_id, "AUTH", str(e))

        capability_name = req.get("capability")
        raw_params = req.get("params")
        params: Dict[str, Any] = raw_params if isinstance(raw_params, dict) else {}
        account_id = params.get("account_id") if isinstance(params.get("account_id"), str) else None

        # --- 2. Policy ---
        decision = evaluate(policy, agent.name, capability_name, params)
        if not decision.allow:
            await audit_writer.log(
                AuditEntry(
                    ts=ts,
                    trace_id=trace_id,
                    outcome="deny",
                    error_code="DENY",
                    caller=agent.name,
                    capability=capability_name,
                    account_id=account_id,
                    latency_ms=_elapsed_ms(t0),
                    extra={"reason": decision.reason},
                )
            )
            return _error(trace_id, "DENY", decision.reason)

        # --- 3. Capability lookup ---
        spec = capability_registry.get(capability_name)
        if spec is None:
            await audit_writer.log(
                AuditEntry(
                    ts=ts,
                    trace_id=trace_id,
                    outcome="notfound",
                    error_code="NOTFOUND",
                    caller=agent.name,
                    capability=capability_name,
                    account_id=account_id,
                    latency_ms=_elapsed_ms(t0),
                )
            )
            return _error(trace_id, "NOTFOUND", f"no such capability: {capability_name!r}")

        # --- 4. Handler exec ---
        try:
            result = await spec.handler(agent, params)
        except Exception as e:  # noqa: BLE001
            await audit_writer.log(
                AuditEntry(
                    ts=ts,
                    trace_id=trace_id,
                    outcome="internal",
                    error_code="INTERNAL",
                    caller=agent.name,
                    capability=capability_name,
                    account_id=account_id,
                    latency_ms=_elapsed_ms(t0),
                    extra={"error": f"{type(e).__name__}: {e}"},
                )
            )
            return _error(trace_id, "INTERNAL", f"{type(e).__name__}: {e}")

        # --- 5. Success audit ---
        param_hashes = {
            k: hash_param(v) for k, v in params.items() if k not in spec.clear_params
        }
        param_values = {k: v for k, v in params.items() if k in spec.clear_params}
        await audit_writer.log(
            AuditEntry(
                ts=ts,
                trace_id=trace_id,
                outcome="ok",
                caller=agent.name,
                capability=capability_name,
                account_id=account_id,
                latency_ms=_elapsed_ms(t0),
                param_hashes=param_hashes,
                param_values=param_values,
            )
        )
        return {"ok": True, "result": result, "trace_id": trace_id}

    return dispatch


def _error(trace_id: str, code: str, message: str) -> Dict[str, Any]:
    return {
        "ok": False,
        "error_code": code,
        "message": message,
        "trace_id": trace_id,
    }


def _safe_get(obj: Any, key: str) -> Optional[str]:
    if isinstance(obj, dict):
        v = obj.get(key)
        if isinstance(v, str):
            return v
    return None


def _elapsed_ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)
