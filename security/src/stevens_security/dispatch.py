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
from typing import Any, Callable, Dict, Optional

from .approvals.matcher import MatcherIndex
from .approvals.queue import ApprovalQueue, ApprovalRequest, make_request_id
from .audit import AuditEntry, AuditWriter, hash_param
from .capabilities.registry import CapabilityRegistry
from .context import CapabilityContext
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
    context: Optional[CapabilityContext] = None,
    matcher: Optional[MatcherIndex] = None,
    approval_queue: Optional[ApprovalQueue] = None,
    bypass_approval_for_request_id: Optional[Callable[[str], bool]] = None,
) -> Dispatcher:
    """Return an async dispatch function with all dependencies bound.

    Approval-gating params:
    - ``matcher``: in-memory standing-approval index. None disables matching
      (every approval-gated call goes straight to the queue).
    - ``approval_queue``: where BLOCKED calls land. If None and a call needs
      approval, the dispatcher returns INTERNAL — guard against silent
      mis-config.
    - ``bypass_approval_for_request_id``: replay hook. The CLI's
      ``stevens approval approve`` calls back into the dispatcher with the
      original envelope; this predicate identifies replays and skips the
      gate. The replayed envelope is signed by the original agent (not
      Enkidu); the gate-skip just bypasses the per-call queue check.
    """

    effective_context = context or CapabilityContext()

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

        # --- 2b. Approval gate (if requires_approval) ---
        approval_via: Optional[str] = None
        is_replay = False
        replay_id = req.get("replay_request_id") if isinstance(req, dict) else None
        if isinstance(replay_id, str) and bypass_approval_for_request_id is not None:
            is_replay = bypass_approval_for_request_id(replay_id)
        if decision.requires_approval and not is_replay:
            # Rationale is mandatory iff rule says so.
            rationale = params.get("rationale") if isinstance(params.get("rationale"), str) else None
            if decision.rationale_required and not rationale:
                await audit_writer.log(
                    AuditEntry(
                        ts=ts, trace_id=trace_id, outcome="deny", error_code="DENY",
                        caller=agent.name, capability=capability_name,
                        account_id=account_id, latency_ms=_elapsed_ms(t0),
                        extra={"reason": "rationale required but absent"},
                    )
                )
                return _error(trace_id, "DENY", "rationale required for this capability")

            # Standing approval check (in-memory, hot path).
            if matcher is not None:
                m = matcher.match(
                    capability=capability_name, caller=agent.name, params=params,
                )
                if m.matched:
                    approval_via = f"standing/{m.approval_id}"
                    # fall through to capability execution
            if approval_via is None:
                # No standing approval → enqueue per-call request, return BLOCKED.
                if approval_queue is None:
                    await audit_writer.log(
                        AuditEntry(
                            ts=ts, trace_id=trace_id, outcome="internal",
                            error_code="INTERNAL", caller=agent.name,
                            capability=capability_name, account_id=account_id,
                            latency_ms=_elapsed_ms(t0),
                            extra={"reason": "approval required but no queue configured"},
                        )
                    )
                    return _error(
                        trace_id, "INTERNAL",
                        "this capability requires approval but no queue is configured",
                    )
                request_id = make_request_id()
                approval_request = ApprovalRequest(
                    id=request_id,
                    capability=capability_name,
                    caller=agent.name,
                    params_summary=_summarize_params(capability_name, params),
                    full_envelope=req if isinstance(req, dict) else {},
                    rationale=rationale,
                    blocked_trace_id=trace_id,
                )
                await approval_queue.enqueue(request=approval_request)
                await audit_writer.log(
                    AuditEntry(
                        ts=ts, trace_id=trace_id, outcome="blocked",
                        error_code="BLOCKED", caller=agent.name,
                        capability=capability_name, account_id=account_id,
                        latency_ms=_elapsed_ms(t0),
                        approval_request_id=request_id,
                    )
                )
                return {
                    "ok": False, "error_code": "BLOCKED",
                    "message": "approval pending",
                    "trace_id": trace_id,
                    "approval_request_id": request_id,
                }
        elif is_replay and replay_id is not None:
            approval_via = f"per_call/{replay_id}"

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
            result = await spec.invoke(agent, params, effective_context)
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
                approval_via=approval_via,
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


def _summarize_params(capability: str, params: Dict[str, Any]) -> str:
    """Build a one-line human summary of a call for the approval queue UI.

    Rationale-required calls already require the agent to send a clear-text
    rationale; we include the capability name and a short hint of the most
    operator-relevant params (mechanism, packages for installs; account_id for
    channel calls). Sensitive-looking params are elided.
    """
    bits = [capability]
    for key in ("mechanism", "packages", "account_id", "thread_id"):
        if key in params:
            v = params[key]
            if isinstance(v, list):
                bits.append(f"{key}=[{','.join(str(x) for x in v[:3])}{'...' if len(v) > 3 else ''}]")
            else:
                bits.append(f"{key}={v}")
    return " ".join(bits)
