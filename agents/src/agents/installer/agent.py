"""Installer agent.

Triggered by ``system.dep.requested.<package>`` events. Coordinates with
Enkidu to install packages via the privileged-execution protocol; never
runs sudo itself.

The runtime invokes ``handle(event, config)`` per event. The agent uses a
``SecurityClient`` (from env: DEMIURGE_CALLER_NAME / DEMIURGE_PRIVATE_KEY_PATH
/ DEMIURGE_SECURITY_SOCKET) to talk to Enkidu and a bus publisher to emit
outcome events.

Outcome events:
- ``system.dep.installed.<name>`` — install succeeded, inventory written.
- ``system.dep.awaiting_approval.<name>`` — Enkidu returned BLOCKED. The
  ``approval_request_id`` is in the payload; ``demiurge dep ensure --wait``
  subscribes to this topic to know when to re-poll.
- ``system.dep.failed.<name>`` — explicit failure (denied, validation,
  health check). Payload has reason + (optional) error code.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from shared import bus
from shared.events import (
    BaseEvent,
    SystemDepAwaitingApprovalEvent,
    SystemDepFailedEvent,
    SystemDepInstalledEvent,
    SystemDepRequestedEvent,
)
from shared.security_client import (
    BlockedError,
    DenyError,
    SecurityClient,
    SecurityClientError,
)

from .plan_builder import PlanBuildError, build_apt_plan


log = logging.getLogger(__name__)


_CLIENT: Optional[SecurityClient] = None


def _client() -> SecurityClient:
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    socket_path = os.environ.get("DEMIURGE_SECURITY_SOCKET", "/run/demiurge/security.sock")
    caller = os.environ.get("DEMIURGE_CALLER_NAME", "installer")
    key_path = os.environ.get("DEMIURGE_PRIVATE_KEY_PATH")
    if not key_path:
        raise RuntimeError(
            "DEMIURGE_PRIVATE_KEY_PATH must be set for the installer agent"
        )
    _CLIENT = SecurityClient.from_key_file(
        socket_path=socket_path,
        caller_name=caller,
        private_key_path=key_path,
    )
    return _CLIENT


def _set_client_for_tests(client: Optional[SecurityClient]) -> None:
    """Test seam — lets tests inject a fake client without monkeypatching env."""
    global _CLIENT
    _CLIENT = client


async def _publish(event_obj: BaseEvent) -> None:
    """Publish a result event to the bus.

    Best-effort — the installer runs whether or not bus delivery succeeds. We
    log on failure so missing events can be reconstructed from the audit log
    if needed.
    """
    try:
        await bus.publish(event_obj)
    except Exception:  # noqa: BLE001
        log.exception("failed to publish %s", event_obj.topic)


async def handle(event: BaseEvent, config: Dict[str, Any]) -> None:
    if not isinstance(event, SystemDepRequestedEvent):
        log.debug("installer ignoring non-dep event: %s", type(event).__name__)
        return
    package = event.package

    log.info("installer handling system.dep.requested.%s", package)
    try:
        client = _client()
    except Exception as e:  # noqa: BLE001
        log.exception("installer cannot construct SecurityClient")
        await _publish(SystemDepFailedEvent(
            account_id="system", package=package,
            reason="client_init_failed", detail=str(e),
        ))
        return

    # Step 1 — read environment.
    try:
        env_snap = await client.call(
            "system.read_environment",
            {
                "fields": [
                    {"name": "os_release"},
                    {"name": "dpkg_status", "package": package},
                ]
            },
        )
    except SecurityClientError as e:
        await _publish(SystemDepFailedEvent(
            account_id="system", package=package,
            reason="read_environment_failed", detail=str(e),
        ))
        return

    # Already installed? Treat as success without proposing a plan.
    dpkg = (env_snap.get("dpkg_status") or {}).get(package) or {}
    if dpkg.get("installed"):
        await _publish(SystemDepInstalledEvent(
            account_id="system", package=package,
            reason="already_installed",
            version=dpkg.get("version"),
        ))
        return

    # Step 2 — build the plan.
    try:
        plan_body, rollback_body = build_apt_plan(package=package, env_snapshot=env_snap)
    except PlanBuildError as e:
        await _publish(SystemDepFailedEvent(
            account_id="system", package=package,
            reason="plan_build_failed", detail=str(e),
        ))
        return

    # Step 3 — submit the plan for validation.
    rationale = f"installer requested via system.dep.requested.{package}"
    try:
        plan_resp = await client.call(
            "system.plan_install",
            {
                "mechanism": "apt",
                "plan_body": plan_body,
                "rollback_body": rollback_body,
                "rationale": rationale,
            },
        )
    except SecurityClientError as e:
        await _publish(SystemDepFailedEvent(
            account_id="system", package=package,
            reason="plan_install_call_failed", detail=str(e),
        ))
        return

    if not plan_resp.get("validated"):
        await _publish(SystemDepFailedEvent(
            account_id="system", package=package,
            reason="plan_invalid", detail=str(plan_resp.get("errors") or []),
        ))
        return

    plan_id = plan_resp["plan_id"]

    # Step 4 — execute (approval-gated). Echo the plan's mechanism + packages
    # + source.repo so standing-approval predicates can gate on them. The
    # broker re-validates against the stored plan on plan_id, so these
    # values can't widen the call beyond what was already validated.
    try:
        result = await client.call(
            "system.execute_privileged",
            {
                "plan_id": plan_id,
                "rationale": rationale,
                "mechanism": "apt",
                "packages": list(plan_body.get("packages") or []),
                "source": (plan_body.get("source") or {}).get("repo", ""),
            },
        )
    except BlockedError as e:
        await _publish(SystemDepAwaitingApprovalEvent(
            account_id="system", package=package,
            plan_id=plan_id,
            approval_request_id=e.approval_request_id or "",
            rationale=rationale,
        ))
        return
    except DenyError as e:
        await _publish(SystemDepFailedEvent(
            account_id="system", package=package,
            reason="denied", detail=str(e),
        ))
        return
    except SecurityClientError as e:
        await _publish(SystemDepFailedEvent(
            account_id="system", package=package,
            reason="execute_call_failed", detail=str(e),
        ))
        return

    outcome = result.get("outcome")
    if outcome == "ok":
        await _publish(SystemDepInstalledEvent(
            account_id="system", package=package,
            plan_id=plan_id,
            inventory_id=result.get("inventory_id"),
        ))
    else:
        await _publish(SystemDepFailedEvent(
            account_id="system", package=package,
            reason=outcome or "unknown_failure",
            detail=str(result),
        ))
