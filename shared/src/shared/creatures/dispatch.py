"""Blessing + angel-commission dispatcher — Hephaestus's substrate.

v0.11 step 3b. Hephaestus uses this to negotiate with the gods at forge
time. Two parallel fans:

1. **Blessing collection** — for each capability the manifest declares,
   route to the owning god, ask for a ``Blessing | Denial``. All in
   parallel; aggregate; if any required capability denied, the forge
   fails with a structured report that names every denial.
2. **Angel commission collection** — for each god in the registered set,
   ask "do you want an angel attached to this Creature?" Most return
   None (no angel needed); Enkidu always returns an audit-angel spec
   (mandatory); Mnemosyne (v0.13+) always returns a memory-angel spec.

This module is consumed by ``demiurge.pantheon.hephaestus`` (step 3d/3e)
but lives in shared so test infrastructure can build mock gods that
implement the same Protocol without depending on demiurge core.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Mapping, Optional

from .tools import (
    AngelSpec,
    Blessing,
    Denial,
    GodlyBlessing,
    ToolRequest,
)


# ----------------------------- routing -----------------------------------


def route_capability(
    capability: str, *, routes: Mapping[str, str]
) -> Optional[str]:
    """Map a capability name to its owning god by prefix.

    ``routes`` is a dict like ``{"gmail": "enkidu", "web": "arachne",
    "memory": "mnemosyne", ...}``. The capability is split at the first
    dot; the prefix's owning god is returned.

    Returns ``None`` if the prefix has no owner — caller decides whether
    to treat that as a hard fail or a soft warning. (For v0.11 it's
    always a hard fail.)
    """
    if "." not in capability:
        return routes.get(capability)
    prefix, _ = capability.split(".", 1)
    return routes.get(prefix)


# ----------------------------- result types ------------------------------


@dataclass(frozen=True)
class BlessingResult:
    """Aggregated outcome of a parallel blessing fan-out.

    ``blessings`` maps capability → granted Blessing; ``denials`` maps
    capability → Denial; ``unrouted`` is the list of capabilities that
    have no owning god. The forge fails if either ``denials`` or
    ``unrouted`` is non-empty (assuming all capabilities were required —
    optional capabilities aren't a v0.11 concept yet).
    """

    blessings: dict[str, Blessing] = field(default_factory=dict)
    denials: dict[str, Denial] = field(default_factory=dict)
    unrouted: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.denials and not self.unrouted

    def format_report(self) -> str:
        """Operator-readable rundown for surfacing in CLI failures."""
        lines: list[str] = []
        if self.blessings:
            lines.append(f"  blessings granted: {', '.join(sorted(self.blessings))}")
        for cap, denial in sorted(self.denials.items()):
            extra = " (operator approval would unblock)" if denial.requires_approval else ""
            lines.append(
                f"  ✗ {cap}: denied by {denial.god} — {denial.reason}{extra}"
            )
        for cap in self.unrouted:
            lines.append(f"  ✗ {cap}: no god owns this capability prefix")
        return "\n".join(lines)


@dataclass(frozen=True)
class AngelCommissionResult:
    """Aggregated outcome of asking each god whether to commission an angel.

    The list of returned ``AngelSpec`` is what Hephaestus then forges as
    attached angels for this Creature. Order is god-name-stable so two
    forges of the same Creature produce the same angel set.
    """

    specs: list[AngelSpec] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    # gods that raised when asked (rare; treated as no-angel-this-time
    # rather than fail-the-forge — angel commissioning is non-critical
    # except for Enkidu's audit, which is hardcoded mandatory upstream).


# ----------------------------- fan-outs ----------------------------------


async def collect_blessings(
    *,
    creature_id: str,
    capabilities: list[str],
    gods: Mapping[str, GodlyBlessing],
    routes: Mapping[str, str],
    requested_scope: Optional[Mapping[str, dict]] = None,
) -> BlessingResult:
    """Ask each relevant god for blessings, in parallel.

    For every capability:
    1. Route to the owning god (by prefix).
    2. If the prefix has no owner → unrouted.
    3. If the owning god isn't in the ``gods`` mapping → unrouted (the
       god isn't installed/available in this Demiurge instance).
    4. Otherwise call ``god.bless()`` with a ``ToolRequest``.

    Returns a ``BlessingResult`` with successes and failures partitioned.

    The fan-out is parallelized via ``asyncio.gather`` so a slow god
    doesn't serialize the others — typical forge has 3–5 gods to ask.
    """
    scope_map = dict(requested_scope or {})
    requests: list[tuple[str, str, ToolRequest]] = []
    unrouted: list[str] = []

    for cap in capabilities:
        owner = route_capability(cap, routes=routes)
        if owner is None or owner not in gods:
            unrouted.append(cap)
            continue
        requests.append(
            (
                cap,
                owner,
                ToolRequest(
                    capability=cap,
                    creature_id=creature_id,
                    requested_scope=scope_map.get(cap, {}),
                ),
            )
        )

    async def ask(god_name: str, req: ToolRequest):
        return await gods[god_name].bless(creature_id=creature_id, request=req)

    if requests:
        responses = await asyncio.gather(
            *(ask(g, r) for _, g, r in requests),
            return_exceptions=True,
        )
    else:
        responses = []

    blessings: dict[str, Blessing] = {}
    denials: dict[str, Denial] = {}
    for (cap, god_name, req), resp in zip(requests, responses):
        if isinstance(resp, BaseException):
            denials[cap] = Denial(
                capability=cap,
                creature_id=creature_id,
                god=god_name,
                reason=f"god raised: {type(resp).__name__}: {resp}",
            )
        elif isinstance(resp, Blessing):
            blessings[cap] = resp
        elif isinstance(resp, Denial):
            denials[cap] = resp
        else:
            denials[cap] = Denial(
                capability=cap,
                creature_id=creature_id,
                god=god_name,
                reason=(
                    f"god returned {type(resp).__name__}, "
                    f"expected Blessing or Denial"
                ),
            )

    return BlessingResult(
        blessings=blessings,
        denials=denials,
        unrouted=unrouted,
    )


async def collect_angel_commissions(
    *,
    creature_id: str,
    gods: Mapping[str, GodlyBlessing],
) -> AngelCommissionResult:
    """Ask every registered god whether to attach an angel.

    Each god returns ``AngelSpec | None``. Stable ordering by god name
    so two forges of the same Creature produce the same angel set.

    Errors are non-fatal here: angel commissioning is an optimization
    path (except Enkidu's audit angel, which Hephaestus hardcodes
    upstream of this fan-out). A god that raises just gets recorded in
    ``errors`` and contributes no angel.
    """

    god_names = sorted(gods.keys())

    async def ask(god_name: str):
        return await gods[god_name].commission_angel(creature_id=creature_id)

    if not god_names:
        return AngelCommissionResult()

    responses = await asyncio.gather(
        *(ask(g) for g in god_names),
        return_exceptions=True,
    )

    specs: list[AngelSpec] = []
    errors: dict[str, str] = {}
    for god_name, resp in zip(god_names, responses):
        if isinstance(resp, BaseException):
            errors[god_name] = f"{type(resp).__name__}: {resp}"
        elif resp is None:
            continue
        elif isinstance(resp, AngelSpec):
            specs.append(resp)
        else:
            errors[god_name] = (
                f"commission_angel returned {type(resp).__name__}, "
                f"expected AngelSpec or None"
            )

    return AngelCommissionResult(specs=specs, errors=errors)


# ----------------------------- mock god (for tests) ----------------------


@dataclass
class MockGod:
    """In-memory god for unit tests of the blessing dispatcher.

    Configurable per-capability outcomes; configurable angel commissioning.
    Records every call so tests can assert on what was asked.

    Real Pantheon gods (Enkidu, Arachne, etc.) implement ``GodlyBlessing``
    against their actual policy stores; this mock just looks up its
    canned answers.
    """

    name: str
    bless_outcomes: dict[str, "Blessing | Denial"] = field(default_factory=dict)
    angel_to_commission: Optional[AngelSpec] = None
    raise_on_bless: Optional[BaseException] = None
    raise_on_commission: Optional[BaseException] = None
    bless_calls: list[ToolRequest] = field(default_factory=list)
    commission_calls: list[str] = field(default_factory=list)

    async def bless(
        self, *, creature_id: str, request: ToolRequest
    ) -> "Blessing | Denial":
        self.bless_calls.append(request)
        if self.raise_on_bless is not None:
            raise self.raise_on_bless
        if request.capability in self.bless_outcomes:
            return self.bless_outcomes[request.capability]
        return Denial(
            capability=request.capability,
            creature_id=creature_id,
            god=self.name,
            reason=f"{self.name} has no policy for {request.capability!r}",
        )

    async def commission_angel(
        self, *, creature_id: str
    ) -> Optional[AngelSpec]:
        self.commission_calls.append(creature_id)
        if self.raise_on_commission is not None:
            raise self.raise_on_commission
        if self.angel_to_commission is None:
            return None
        # Return a copy with the right creature_id stamped in.
        spec = self.angel_to_commission
        return AngelSpec(
            god=spec.god,
            name=spec.name,
            creature_id=creature_id,
            config=dict(spec.config),
        )
