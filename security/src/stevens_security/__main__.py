"""Security Agent entrypoint.

Run with ``uv run python -m stevens_security`` (dev) or via the ``security``
service in compose (prod).

Configuration comes from environment variables:

- ``STEVENS_SECURITY_SOCKET``    default ``/run/stevens/security.sock``
- ``STEVENS_SECURITY_AGENTS``    default ``security/policy/agents.yaml``
- ``STEVENS_SECURITY_POLICY``    default ``security/policy/capabilities.yaml``
- ``STEVENS_SECURITY_AUDIT_DIR`` default ``/var/lib/stevens/audit``
- ``STEVENS_SECURITY_SECRETS``   default ``/var/lib/stevens/secrets`` (used once the
                                 sealed store lands in steps 9–12; ignored here)

The passphrase prompt for the sealed-store root key (step 11) is not yet
wired — this entrypoint is the transport + auth + policy + audit runtime
that step 6 proved works end-to-end. Real capabilities (ping already +
future gmail.*, anthropic.complete, payments.*) register on import; the
dispatcher picks them up.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

from shared.outbound.web import WebClient

from .approvals.matcher import MatcherIndex
from .approvals.queue import InMemoryApprovalQueue
from .audit import AuditWriter
from .capabilities import admin, network, ping, system  # noqa: F401 — registers capabilities
from .capabilities.network import WebState
from .capabilities.registry import default_registry
from .context import CapabilityContext
from .dispatch import build_dispatcher
from .identity import NonceCache, load_agents_registry
from .outbound.web_state import DomainRateLimiter, TTLCache
from .policy import load_policy
from .server import start_server
from .system_runtime import (
    InMemoryInventory,
    InMemoryPlanStore,
    SystemRuntime,
    real_subprocess_runner,
)


def _env_path(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default))


async def _amain() -> int:
    logging.basicConfig(
        level=os.environ.get("STEVENS_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    log = logging.getLogger("stevens_security")
    log.info("Enkidu — Stevens Security Agent (sole broker for secrets)")

    socket_path = os.environ.get(
        "STEVENS_SECURITY_SOCKET", "/run/stevens/security.sock"
    )
    agents_path = _env_path(
        "STEVENS_SECURITY_AGENTS",
        str(Path(__file__).resolve().parents[3] / "policy" / "agents.yaml"),
    )
    policy_path = _env_path(
        "STEVENS_SECURITY_POLICY",
        str(Path(__file__).resolve().parents[3] / "policy" / "capabilities.yaml"),
    )
    audit_dir = _env_path("STEVENS_SECURITY_AUDIT_DIR", "/var/lib/stevens/audit")

    log.info("loading identity registry from %s", agents_path)
    identity_registry = load_agents_registry(agents_path)
    log.info("loaded %d agent identities", len(identity_registry))

    log.info("loading policy from %s", policy_path)
    policy = load_policy(policy_path)
    log.info("loaded policy for %d agents", len(policy.agents))

    log.info("audit log root: %s", audit_dir)
    audit_writer = AuditWriter(audit_dir)

    # Approvals + system runtime: pick Postgres if DATABASE_URL is set,
    # otherwise stay in-memory (dev / smoke).
    use_postgres = bool(os.environ.get("DATABASE_URL"))
    matcher = MatcherIndex()
    if use_postgres:
        from .approvals.store_postgres import PostgresApprovalStore
        from .system_runtime_postgres import PostgresInventory, PostgresPlanStore

        approval_store = PostgresApprovalStore()
        plan_store = PostgresPlanStore()
        inventory = PostgresInventory()

        # Pre-load active standing approvals into the matcher.
        try:
            existing = await approval_store.list_standing()
            matcher.replace_all(existing)
            log.info("loaded %d standing approval(s) into matcher", len(existing))
        except Exception as e:  # noqa: BLE001
            log.warning("could not pre-load standing approvals: %s", e)

        approval_queue = _ApprovalQueueFromStore(approval_store)
    else:
        from .approvals.store import InMemoryApprovalStore

        approval_store = InMemoryApprovalStore()
        plan_store = InMemoryPlanStore()
        inventory = InMemoryInventory()
        approval_queue = InMemoryApprovalQueue()

    system_rt = SystemRuntime(
        plan_store=plan_store,
        inventory=inventory,
        run_subprocess=real_subprocess_runner,
    )
    web_state = WebState(
        fetch_cache=TTLCache(),
        search_cache=TTLCache(),
        rate_limiter=DomainRateLimiter(),
        web_client=WebClient(),
    )

    # Sealed store unlock — for v0.3.2 we read the passphrase via the same
    # priority order as the CLI (env → keyring → prompt). Done lazily; if
    # no passphrase is available we still start (capabilities that need
    # the sealed store will fail at call time with a clear error).
    sealed_store = _try_unlock_sealed_store(log)

    context = CapabilityContext(
        sealed_store=sealed_store,
        extra={"system": system_rt, "web_state": web_state},
    )

    # Approval-replay tracking: when the operator approves a per-call
    # request via `stevens approval approve`, the CLI invokes the admin
    # capability which adds the request_id here so the dispatcher allows
    # the replayed envelope through the gate.
    approved_replay_ids: set = set()

    dispatcher = build_dispatcher(
        identity_registry=identity_registry,
        policy=policy,
        audit_writer=audit_writer,
        capability_registry=default_registry,
        nonce_cache=NonceCache(),
        context=context,
        matcher=matcher,
        approval_queue=approval_queue,
        bypass_approval_for_request_id=lambda rid: rid in approved_replay_ids,
    )

    # Stash references so the admin capability can refresh / replay.
    context.extra["_admin_approval_store"] = approval_store
    context.extra["_admin_matcher"] = matcher
    context.extra["_admin_approved_replay_ids"] = approved_replay_ids

    Path(socket_path).parent.mkdir(parents=True, exist_ok=True)
    log.info("starting UDS server at %s", socket_path)
    server = await start_server(socket_path, dispatch=dispatcher)

    log.info(
        "capabilities registered: %s",
        ", ".join(sorted(default_registry.names())) or "(none)",
    )

    stop = asyncio.Event()

    def _handle_signal(signum: int, _frame) -> None:
        log.info("received signal %d, shutting down", signum)
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            signal.signal(sig, _handle_signal)

    async with server:
        await stop.wait()

    log.info("server stopped")
    return 0


class _ApprovalQueueFromStore:
    """Adapter: makes a PostgresApprovalStore satisfy the ApprovalQueue Protocol.

    The store and the queue have overlapping but not identical interfaces.
    The dispatcher only uses ``enqueue``, ``get``, ``decide``, ``list_pending``
    on the queue side; this adapter routes those to the store.
    """

    def __init__(self, store):
        self._store = store

    async def enqueue(self, *, request):
        return await self._store.enqueue_request(request=request)

    async def get(self, request_id):
        return await self._store.get_request(request_id)

    async def list_pending(self):
        return await self._store.list_pending()

    async def decide(self, *, request_id, status, decided_by, decision_notes=None, promoted_standing_id=None):
        from .approvals.queue import DecisionResult

        result = await self._store.decide_request(
            request_id=request_id, status=status, decided_by=decided_by,
            notes=decision_notes, promoted_standing_id=promoted_standing_id,
        )
        return DecisionResult(request=result)


def _try_unlock_sealed_store(log):
    """Best-effort sealed-store unlock at boot. Returns the unlocked store
    or None if no passphrase is available.

    Priority: ``$STEVENS_PASSPHRASE`` env → OS keyring → None. We do NOT
    prompt at boot — Enkidu must come up unattended in compose. Operators
    who need an interactive unlock can run ``stevens passphrase remember``
    once and let the keyring serve subsequent boots silently.
    """
    pp_env = os.environ.get("STEVENS_PASSPHRASE")
    pp = pp_env.encode("utf-8") if pp_env else None
    if pp is None:
        try:
            from . import keyring_passphrase

            pp = keyring_passphrase.get()
        except Exception:  # noqa: BLE001
            pp = None
    if pp is None:
        log.info("sealed store: no passphrase configured at boot — capabilities that need secrets will fail until set")
        return None
    try:
        from .sealed_store import SealedStore

        secrets_root = Path(
            os.environ.get("STEVENS_SECURITY_SECRETS", "/var/lib/stevens/secrets")
        )
        store = SealedStore.unlock(secrets_root, pp)
        log.info("sealed store unlocked")
        return store
    except Exception as e:  # noqa: BLE001
        log.warning("sealed store unlock failed: %s", e)
        return None


def main() -> int:
    try:
        return asyncio.run(_amain())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
