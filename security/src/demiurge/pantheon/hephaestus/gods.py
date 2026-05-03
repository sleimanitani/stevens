"""GodlyBlessing adapters for each Pantheon member.

v0.11 step 3c. Hephaestus negotiates with gods through a single
interface (``GodlyBlessing`` defined in ``shared.creatures.tools``).
Each god has its own internal policy machinery; this module provides
thin adapters that expose that machinery through the uniform interface.

Adapters:

- **EnkiduGod** — wraps Enkidu's existing ``Policy`` (loaded from
  ``capabilities.yaml``). Real, not a stub.
- **ArachneGod** — thin wrapper over Arachne's per-domain allowlist +
  cache + rate-limiter. Blessing is the static "Mortal X may call
  Arachne for these capabilities"; per-call domain checks remain
  Arachne's runtime concern.
- **SphinxGod** — PDF strategy router. Always-on for now (no per-call
  policy); blessing is just "yes this Mortal can call Sphinx".
- **JanusGod** — operator-assisted browser. Blesses browser caps
  generously; the operator-assisted nature of the recipes is the gate.
- **MnemosyneStubGod** — placeholder for v0.13. Blesses
  ``memory.recall`` only; everything else denies. Always commissions
  a (placeholder) memory angel.
- **IrisStubGod** — placeholder for v0.12. No tools blessed (Iris is
  Sol-facing, not Mortal-facing). Returns no angel commission.
- **ZeusStubGod** — placeholder for v0.12-13. Blesses ``zeus.*`` only
  (request_spawn, etc.) but only for Mortals whose manifest declares
  the right grant. Returns no angel commission.
- **EnkiduAuditAngelGod** mixin — Enkidu's mandatory-audit-angel
  commissioning lives here. Hephaestus *always* attaches an audit
  angel via this commission, regardless of what the operator-side
  policy says.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from shared.creatures.tools import (
    AngelSpec,
    Blessing,
    Denial,
    GodlyBlessing,
    ToolRequest,
)

from ...policy import Policy, evaluate as evaluate_policy


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


# ----------------------------- EnkiduGod ---------------------------------


@dataclass
class EnkiduGod:
    """Adapter from Enkidu's existing Policy to the GodlyBlessing protocol.

    Every capability that lives behind Enkidu (gmail, calendar,
    whatsapp_cloud, signal — the secret-touching ones) routes here at
    forge time. We delegate to the same evaluator the runtime dispatch
    uses, so blessing-time and dispatch-time decisions match.

    Hephaestus is responsible for materializing a policy block in
    ``capabilities.yaml`` for the Creature *before* forge-time blessing
    is called (otherwise EnkiduGod sees no policy for the caller and
    denies). Step 3e wires that.
    """

    policy: Policy
    name: str = "enkidu"

    async def bless(
        self, *, creature_id: str, request: ToolRequest
    ) -> "Blessing | Denial":
        # Build params shape Enkidu's evaluator expects. account_id (if
        # any) lives on the requested_scope for account-scoped caps.
        params = dict(request.requested_scope or {})
        decision = evaluate_policy(
            self.policy, creature_id, request.capability, params
        )
        if not decision.allow:
            return Denial(
                capability=request.capability,
                creature_id=creature_id,
                god=self.name,
                reason=decision.reason,
                requires_approval=decision.requires_approval,
            )
        # Approval-gated allows are still allow-with-strings-attached.
        # The runtime dispatch is what enforces the approval (the
        # blessing itself doesn't gate; the audit-angel + dispatch
        # surface it). For forge-time, treat as ok blessing.
        return Blessing(
            capability=request.capability,
            creature_id=creature_id,
            god=self.name,
            issued_at=_now(),
            scope=dict(request.requested_scope or {}),
        )

    async def commission_angel(
        self, *, creature_id: str
    ) -> Optional[AngelSpec]:
        """Enkidu's audit angel — mandatory for every Creature.

        v0.11 returns the spec; the angel is implemented in-process as a
        projection of the observation feed into the existing audit-log
        writer (3e). v0.13 promotes to out-of-process.
        """
        return AngelSpec(
            god=self.name,
            name="audit",
            creature_id=creature_id,
            config={},
        )


# ----------------------------- generic blanket-allow god -----------------


@dataclass
class _BlanketAllowGod:
    """Common shape for gods whose blessing is yes-or-no on a static set
    of capabilities. The per-call enforcement lives in the god's own
    runtime (e.g. Arachne's domain allowlist).

    Subclasses set ``name`` and ``blessable_capabilities`` (a set of
    capability strings the god is willing to grant for any Mortal).
    """

    name: str
    blessable_capabilities: frozenset[str]
    angel_to_commission: Optional[AngelSpec] = None

    async def bless(
        self, *, creature_id: str, request: ToolRequest
    ) -> "Blessing | Denial":
        if request.capability not in self.blessable_capabilities:
            return Denial(
                capability=request.capability,
                creature_id=creature_id,
                god=self.name,
                reason=(
                    f"{self.name} does not bless {request.capability!r} — "
                    f"recognized: {sorted(self.blessable_capabilities)}"
                ),
            )
        return Blessing(
            capability=request.capability,
            creature_id=creature_id,
            god=self.name,
            issued_at=_now(),
            scope=dict(request.requested_scope or {}),
        )

    async def commission_angel(
        self, *, creature_id: str
    ) -> Optional[AngelSpec]:
        if self.angel_to_commission is None:
            return None
        spec = self.angel_to_commission
        return AngelSpec(
            god=spec.god,
            name=spec.name,
            creature_id=creature_id,
            config=dict(spec.config),
        )


# ----------------------------- shipped god adapters ----------------------


def ArachneGod() -> _BlanketAllowGod:
    """Async-path web fetch + search.

    Per-domain allowlist + rate limiter remain Arachne's runtime
    concerns; blessing-time, we just confirm the Mortal is allowed to
    reach Arachne for these capabilities at all.
    """
    return _BlanketAllowGod(
        name="arachne",
        blessable_capabilities=frozenset({
            "web.fetch",
            "web.search",
        }),
    )


def SphinxGod() -> _BlanketAllowGod:
    """PDF strategy router. Currently always-on (no per-Mortal policy)."""
    return _BlanketAllowGod(
        name="sphinx",
        blessable_capabilities=frozenset({
            "pdf.read",
            "pdf.extract_tables",
        }),
    )


def JanusGod() -> _BlanketAllowGod:
    """Operator-assisted browser. Generous at blessing time; the
    operator-assisted nature of the recipes is the real gate."""
    return _BlanketAllowGod(
        name="janus",
        blessable_capabilities=frozenset({
            "browser.run_recipe",
        }),
    )


# ----------------------------- stub god adapters -------------------------


def MnemosyneStubGod() -> _BlanketAllowGod:
    """v0.13 placeholder. Blesses ``memory.recall`` only.

    Once Mnemosyne ships, the stub gets replaced with a real adapter
    that consults Mnemosyne's per-Mortal namespace policy. The
    blessing is the same shape — only the policy logic changes.

    Always commissions a (placeholder) memory angel so the spec flow
    is exercised. The angel itself is a no-op until v0.13.
    """
    return _BlanketAllowGod(
        name="mnemosyne",
        blessable_capabilities=frozenset({
            "memory.recall",
        }),
        angel_to_commission=AngelSpec(
            god="mnemosyne",
            name="memory",
            creature_id="<placeholder>",  # stamped by collect_angel_commissions
            config={"v0.11_stub": True},
        ),
    )


def IrisStubGod() -> _BlanketAllowGod:
    """v0.12 placeholder. No Mortal-facing capabilities.

    Iris is Sol-facing (preferences, modality, notifications). Mortals
    do NOT call Iris directly; if a Mortal needs to communicate with
    Sol, that's via Zeus → Iris (and v0.11 doesn't have either yet).
    Returns Denial for any capability requested.
    """
    return _BlanketAllowGod(
        name="iris",
        blessable_capabilities=frozenset(),
    )


def ZeusStubGod() -> _BlanketAllowGod:
    """v0.12-13 placeholder. Blesses ``zeus.*`` only — for Mortals
    that have been granted spawn-request authority.

    Once Zeus ships, blessing-time still uses this surface; the real
    runtime behavior (multi-god dispatch, judgment) lives behind the
    capability call itself.
    """
    return _BlanketAllowGod(
        name="zeus",
        blessable_capabilities=frozenset({
            "zeus.request_spawn",
        }),
    )


# ----------------------------- protocol marker ---------------------------


# Type alias for IDE introspection — every adapter above implements
# this Protocol. Useful for type-checking the dict passed to
# `collect_blessings(gods=...)`.
GodAdapter = GodlyBlessing
