"""Universal tools every Creature gets — no blessing required.

v0.11 step 3a. Two universal tools, intentionally:

- ``think(text)`` — speak aloud for the audit angel. The Mortal's
  reasoning trail. Lands in the observation feed as ``kind: think``.
  No return value, no side effect outside the feed.
- ``mortal.return(result)`` — finish the current task and report back.
  Lands in the feed as ``kind: lifecycle.return``. The supervisor
  observes this and tears the Mortal's monologue loop down.

That's it. No journal API (the audit angel handles observation).
No subordinate-summon tool (Mortals don't summon — Zeus does, post-v0.12).
No memory write (a Mortal's recall surface is the blessed
``tools.memory.recall(query)``, mediated by Mnemosyne).

Universal tools are wired into every Mortal's ``ToolRegistry`` at forge
time by Hephaestus, *in addition to* the Mortal's blessed tools. They
appear in ``ctx.tools.names()`` like any other tool — that's intentional:
the LLM sees ``think`` and ``mortal.return`` listed alongside its
blessed actions and learns to reach for them naturally.
"""

from __future__ import annotations

from typing import Any

from .context import CreatureContext, MortalContext
from .feed import KIND_MORTAL_RETURN, KIND_THINK
from .tools import RegisteredTool


# ----------------------------- universal tool impls ----------------------


async def think(ctx: CreatureContext, text: str) -> None:
    """Append a thought to the observation feed.

    The text goes into the feed envelope's ``data.text`` field. The audit
    angel projects it into the audit log; future Mnemosyne projects it
    into the narrative store.

    No return value — ``think`` is fire-and-forget from the Creature's
    perspective. The LLM uses it to articulate reasoning ("first I'll
    fetch the thread, then check the user's preferences, then draft").
    """
    if not isinstance(text, str) or not text:
        # Defensive: an empty think() is a bug in the LLM's tool call.
        # We still append (so the bug shows up in audit) but we mark it.
        ctx.audit.append(
            kind=KIND_THINK,
            data={"text": text or "", "warning": "empty_or_non_string"},
        )
        return
    ctx.audit.append(kind=KIND_THINK, data={"text": text})


class MortalReturn(Exception):
    """Signal raised by ``mortal.return`` to tear down the monologue loop.

    The supervisor's monologue runner catches this, records the result,
    and stops the Mortal cleanly. Using an exception (rather than a
    return-value sentinel) lets the Mortal call ``mortal.return`` from
    arbitrarily deep in its tool-call chain without restructuring its
    code to bubble a value back.

    ``result`` is whatever the Mortal wants to hand back to its caller
    (typically Sol via Iris, or a peer Mortal that subscribed to the
    completion event on the bus).
    """

    def __init__(self, result: Any):
        super().__init__("mortal.return")
        self.result = result


async def mortal_return(ctx: CreatureContext, result: Any = None) -> None:
    """Finish the current task. Records to the feed, then raises MortalReturn.

    The MortalReturn exception is caught by the supervisor (step 3e),
    not by the Mortal. From the Mortal's perspective, ``mortal.return``
    looks like a tool that simply doesn't come back — the next
    instruction in its monologue loop never executes.

    Beasts and Automatons don't call this — their lifecycle is "run to
    completion of the single transform/tick." Only Mortals have a
    multi-step loop that needs an explicit terminator.
    """
    # We allow this from any Creature kind so a Beast that wants to
    # signal early termination has a canonical way; the supervisor
    # treats ``lifecycle.return`` uniformly across kinds.
    ctx.audit.append(
        kind=KIND_MORTAL_RETURN,
        data={"result": _safe_jsonify(result)},
    )
    raise MortalReturn(result)


def _safe_jsonify(value: Any) -> Any:
    """Coerce arbitrary values into JSON-serializable shape for the feed.

    Worst case the result becomes its ``repr`` — we never want a
    ``mortal.return`` call to fail with a TypeError mid-teardown. The
    feed envelope's ``data`` is free-form so a string fallback is fine.
    """
    import json

    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return {"_unserializable_repr": repr(value)}


# ----------------------------- registry helper ---------------------------


def universal_tool_registry_entries() -> dict[str, RegisteredTool]:
    """Return the fixed dict of universal RegisteredTool entries.

    Hephaestus calls this at forge time, merges the result with the
    blessed tools collected from gods, and hands the combined registry
    to the new Creature.

    Tool names are namespaced under ``mortal.*`` for the lifecycle ones
    so they don't collide with future blessed tools. ``think`` is
    deliberately top-level — it's universal across creature kinds and
    usage frequency makes a short name worth it.
    """
    return {
        "think": RegisteredTool(
            name="think",
            impl=think,
            blessing=None,
            description=(
                "Speak aloud for the audit record. Use to articulate "
                "reasoning or note observations. No return value."
            ),
        ),
        "mortal.return": RegisteredTool(
            name="mortal.return",
            impl=mortal_return,
            blessing=None,
            description=(
                "Finish the current task and report a result. "
                "Terminates the Mortal's monologue loop."
            ),
        ),
    }
