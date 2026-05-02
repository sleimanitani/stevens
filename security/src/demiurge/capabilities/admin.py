"""``_admin.*`` capabilities — operator-only signals into a running Enkidu.

These exist so the operator CLI (`demiurge approval grant/revoke/approve`)
can tell a running Enkidu "I just changed the DB; please refresh your
in-memory matcher" or "this per-call approval was just decided; please
expect a replay envelope."

The ``_admin`` namespace is allowed only for the ``operator`` caller in
the policy YAML — no agent should ever invoke these. Sol provisions an
``operator`` agent identity once via ``demiurge agent provision operator``
and the CLI signs admin calls with the operator's keypair.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from ..context import CapabilityContext
from .registry import default_registry


log = logging.getLogger(__name__)


@default_registry.capability(
    "_admin.refresh_approvals",
    clear_params=[],
)
async def refresh_approvals(agent, params, context: CapabilityContext):
    """Reload all active standing approvals from the store into the matcher.

    Called by the CLI after grant / revoke. Idempotent.
    """
    store = context.extra.get("_admin_approval_store") if isinstance(context.extra, dict) else None
    matcher = context.extra.get("_admin_matcher") if isinstance(context.extra, dict) else None
    if store is None or matcher is None:
        return {"ok": False, "error": "admin context not configured"}
    try:
        approvals = await store.list_standing()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"store read failed: {e}"}
    matcher.replace_all(approvals)
    log.info("admin: refreshed matcher with %d active standing approvals", len(approvals))
    return {"ok": True, "active_count": len(approvals)}


@default_registry.capability(
    "_admin.mark_request_approved",
    clear_params=["request_id"],
)
async def mark_request_approved(agent, params, context: CapabilityContext):
    """Tell Enkidu that ``request_id`` was just approved in the DB.

    The replayed envelope will arrive shortly carrying ``replay_request_id``;
    Enkidu's gate-bypass predicate consults the approved set this call
    populates.
    """
    request_id = params.get("request_id")
    if not isinstance(request_id, str):
        return {"ok": False, "error": "request_id required"}
    approved = context.extra.get("_admin_approved_replay_ids") if isinstance(context.extra, dict) else None
    if approved is None:
        return {"ok": False, "error": "admin context not configured"}
    approved.add(request_id)
    return {"ok": True, "request_id": request_id}
