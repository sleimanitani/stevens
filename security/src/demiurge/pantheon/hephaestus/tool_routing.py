"""Tool prefix routing + blessed-tool wrapper builder.

v0.11 step 3c. Two responsibilities:

1. **Routing** — DEFAULT_ROUTES is the canonical map from capability
   prefix to owning god. ``demiurge.powers install gmail`` uses
   ``gmail.*`` capabilities → routes to Enkidu. ``web.fetch`` → Arachne.
2. **Wrapping** — `BlessedToolWrapper` is the callable a Mortal sees
   when it invokes a blessed tool. Validates the blessing on every
   call (defense-in-depth — the policy-engine check is the primary
   inside the god) and writes start/end audit events to the
   observation feed.

The composer `forge_blessed_registry()` is what Hephaestus calls at
forge time once `collect_blessings` has succeeded: take the granted
Blessings + the per-god dispatchers + the universal-tool entries, and
produce the final `ToolRegistry` the Creature gets in its context.
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, Mapping, Optional

from shared.creatures.context import CreatureContext
from shared.creatures.feed import (
    KIND_TOOL_CALL_END,
    KIND_TOOL_CALL_START,
)
from shared.creatures.tools import (
    Blessing,
    RegisteredTool,
    ToolDispatchError,
    ToolRegistry,
)
from shared.creatures.universal import universal_tool_registry_entries


# ----------------------------- canonical routes --------------------------


DEFAULT_ROUTES: dict[str, str] = {
    # Secret-touching channel capabilities → Enkidu
    "gmail": "enkidu",
    "calendar": "enkidu",
    "whatsapp": "enkidu",  # both whatsapp.cloud.* and whatsapp.* (legacy)
    "whatsapp_cloud": "enkidu",
    "signal": "enkidu",
    # System / installer capabilities → Enkidu (already Enkidu-enforced)
    "system": "enkidu",
    "_admin": "enkidu",
    # Web → Arachne
    "web": "arachne",
    "network": "arachne",  # legacy capability prefix
    # Documents → Sphinx
    "pdf": "sphinx",
    # Browser-based wizards → Janus
    "browser": "janus",
    # Stub gods (v0.12+)
    "memory": "mnemosyne",
    "say": "iris",
    "zeus": "zeus",
}


# ----------------------------- dispatcher type --------------------------


# A god-side dispatcher is an async callable: given a Creature's
# context, the capability name, the blessing, and the call kwargs,
# perform the actual work and return a result. This is the surface
# Hephaestus injects per-god — for Enkidu it eventually goes through
# the UDS to the running Security Agent. For the stub gods it can
# return canned values. For tests it's whatever the test sets up.
GodDispatcher = Callable[..., Awaitable[Any]]


# ----------------------------- BlessedToolWrapper ------------------------


class BlessedToolWrapper:
    """Callable that dispatches a blessed capability through its god.

    Bound at forge time with the `Blessing` and the per-god dispatcher;
    the dispatcher is closed over so the Mortal can never see (or
    forge) it. On every call:

    1. Validate the blessing — not expired, creature_id matches the
       calling context.
    2. Append a `tool.call.start` event to the observation feed,
       returning its event_id as the correlation_id.
    3. Dispatch via the god.
    4. Append a `tool.call.end` event with result-or-error, correlated
       to the start event.

    The two-event shape (start/end) gives angels a clean interval to
    measure (e.g. dispatch latency, error-rate by capability) without
    needing a join across separate tables. Same envelope schema as
    everything else in the feed.
    """

    def __init__(
        self,
        *,
        blessing: Blessing,
        dispatcher: GodDispatcher,
        description: str = "",
    ):
        self._blessing = blessing
        self._dispatcher = dispatcher
        self._description = description or (
            f"Capability {blessing.capability!r} blessed by {blessing.god!r}."
        )

    @property
    def blessing(self) -> Blessing:
        return self._blessing

    @property
    def description(self) -> str:
        return self._description

    async def __call__(self, ctx: CreatureContext, **kwargs: Any) -> Any:
        cap = self._blessing.capability

        # 1. Validate blessing.
        if self._blessing.is_expired():
            raise ToolDispatchError(
                f"blessing for {cap!r} is expired — Creature must be re-forged"
            )
        if self._blessing.creature_id != ctx.creature_id:
            raise ToolDispatchError(
                f"blessing/context creature_id mismatch: "
                f"{self._blessing.creature_id!r} vs {ctx.creature_id!r}"
            )

        # 2. tool.call.start.
        start_id = ctx.audit.append(
            kind=KIND_TOOL_CALL_START,
            data={
                "capability": cap,
                "god": self._blessing.god,
                "args": _safe_jsonify(kwargs),
            },
        )

        # 3. Dispatch.
        try:
            result = await self._dispatcher(
                ctx, capability=cap, blessing=self._blessing, **kwargs
            )
        except Exception as e:  # noqa: BLE001 — surface cleanly, then re-raise
            ctx.audit.append(
                kind=KIND_TOOL_CALL_END,
                data={
                    "capability": cap,
                    "god": self._blessing.god,
                    "error": f"{type(e).__name__}: {e}",
                },
                correlation_id=start_id,
            )
            raise

        # 4. tool.call.end.
        ctx.audit.append(
            kind=KIND_TOOL_CALL_END,
            data={
                "capability": cap,
                "god": self._blessing.god,
                "result": _safe_jsonify(result),
            },
            correlation_id=start_id,
        )
        return result


def _safe_jsonify(value: Any) -> Any:
    """Coerce arbitrary values into JSON-serializable shape for the feed.

    Tool args/results are operator-supplied shapes; anything is possible.
    Worst case the value becomes its repr — we never want an audit
    write to fail mid-dispatch. Same trick `mortal.return` uses.
    """
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return {"_unserializable_repr": repr(value)}


# ----------------------------- composer ----------------------------------


def forge_blessed_registry(
    *,
    blessings: Mapping[str, Blessing],
    dispatchers: Mapping[str, GodDispatcher],
    descriptions: Optional[Mapping[str, str]] = None,
    include_universal: bool = True,
) -> ToolRegistry:
    """Build the final ``ToolRegistry`` a Mortal sees in its context.

    ``blessings`` — the granted blessings from `collect_blessings`,
    capability-keyed.
    ``dispatchers`` — per-god async dispatchers, god-name-keyed
    ("enkidu" → enkidu_dispatcher, etc.). The dispatcher for a
    capability's god must exist; otherwise the capability is silently
    dropped (with a `ValueError` raised — fail loud, don't silently
    omit a tool the manifest claimed).
    ``descriptions`` — optional per-capability description override
    for the LLM prompt; falls back to a generic string.
    ``include_universal`` — set False to omit the universal tools
    (think, mortal.return). Defaults True; only Beast/Automaton
    contexts that don't need them would set False.

    Idempotent + pure: same inputs → identical registry.
    """
    desc_map = dict(descriptions or {})
    entries: dict[str, RegisteredTool] = {}

    if include_universal:
        entries.update(universal_tool_registry_entries())

    for capability, blessing in blessings.items():
        god = blessing.god
        dispatcher = dispatchers.get(god)
        if dispatcher is None:
            raise ValueError(
                f"capability {capability!r} blessed by {god!r}, but no "
                f"dispatcher registered for that god. Hephaestus must "
                f"register a dispatcher for every god whose blessings "
                f"appear in the manifest."
            )
        wrapper = BlessedToolWrapper(
            blessing=blessing,
            dispatcher=dispatcher,
            description=desc_map.get(capability, ""),
        )
        entries[capability] = RegisteredTool(
            name=capability,
            impl=wrapper,
            blessing=blessing,
            description=wrapper.description,
        )

    return ToolRegistry(entries)
