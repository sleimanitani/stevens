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

from .audit import AuditWriter
from .capabilities import ping  # noqa: F401 — registers the capability
from .capabilities.registry import default_registry
from .dispatch import build_dispatcher
from .identity import NonceCache, load_agents_registry
from .policy import load_policy
from .server import start_server


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

    dispatcher = build_dispatcher(
        identity_registry=identity_registry,
        policy=policy,
        audit_writer=audit_writer,
        capability_registry=default_registry,
        nonce_cache=NonceCache(),
    )

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


def main() -> int:
    try:
        return asyncio.run(_amain())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
