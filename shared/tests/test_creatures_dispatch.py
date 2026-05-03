"""Tests for shared.creatures.dispatch — blessing + angel-commission fan-out."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from shared.creatures.dispatch import (
    AngelCommissionResult,
    BlessingResult,
    MockGod,
    collect_angel_commissions,
    collect_blessings,
    route_capability,
)
from shared.creatures.tools import (
    AngelSpec,
    Blessing,
    Denial,
    ToolRequest,
)


# ----------------------------- routing -----------------------------------


ROUTES = {
    "gmail": "enkidu",
    "calendar": "enkidu",
    "web": "arachne",
    "pdf": "sphinx",
    "memory": "mnemosyne",
}


def test_route_capability_by_prefix():
    assert route_capability("gmail.send", routes=ROUTES) == "enkidu"
    assert route_capability("web.fetch", routes=ROUTES) == "arachne"
    assert route_capability("pdf.read", routes=ROUTES) == "sphinx"


def test_route_capability_unknown_prefix():
    assert route_capability("psychic.divine", routes=ROUTES) is None


def test_route_capability_no_dot():
    """Bare capability name routes only if it's literally in the map."""
    assert route_capability("memory", routes=ROUTES) == "mnemosyne"
    assert route_capability("nonsense", routes=ROUTES) is None


# ----------------------------- helpers ----------------------------------


def _ok_blessing(cap: str, creature_id: str = "x", god: str = "test") -> Blessing:
    return Blessing(
        capability=cap,
        creature_id=creature_id,
        god=god,
        issued_at=datetime.now(tz=timezone.utc),
    )


def _denial(cap: str, reason: str, *, god: str = "test", approval: bool = False) -> Denial:
    return Denial(
        capability=cap,
        creature_id="x",
        god=god,
        reason=reason,
        requires_approval=approval,
    )


# ----------------------------- collect_blessings: happy path -------------


def test_collect_blessings_all_granted():
    enkidu = MockGod(
        name="enkidu",
        bless_outcomes={
            "gmail.send": _ok_blessing("gmail.send", god="enkidu"),
            "gmail.read": _ok_blessing("gmail.read", god="enkidu"),
        },
    )
    arachne = MockGod(
        name="arachne",
        bless_outcomes={"web.fetch": _ok_blessing("web.fetch", god="arachne")},
    )
    result = asyncio.run(
        collect_blessings(
            creature_id="email_pm",
            capabilities=["gmail.send", "gmail.read", "web.fetch"],
            gods={"enkidu": enkidu, "arachne": arachne},
            routes=ROUTES,
        )
    )
    assert result.ok
    assert set(result.blessings.keys()) == {"gmail.send", "gmail.read", "web.fetch"}
    assert result.denials == {}
    assert result.unrouted == []


def test_collect_blessings_creature_id_passed_through():
    enkidu = MockGod(
        name="enkidu",
        bless_outcomes={"gmail.send": _ok_blessing("gmail.send", god="enkidu")},
    )
    asyncio.run(
        collect_blessings(
            creature_id="trip_planner.tokyo",
            capabilities=["gmail.send"],
            gods={"enkidu": enkidu},
            routes=ROUTES,
        )
    )
    assert len(enkidu.bless_calls) == 1
    assert enkidu.bless_calls[0].creature_id == "trip_planner.tokyo"


def test_collect_blessings_requested_scope_passed_through():
    enkidu = MockGod(
        name="enkidu",
        bless_outcomes={"gmail.send": _ok_blessing("gmail.send", god="enkidu")},
    )
    asyncio.run(
        collect_blessings(
            creature_id="x",
            capabilities=["gmail.send"],
            gods={"enkidu": enkidu},
            routes=ROUTES,
            requested_scope={"gmail.send": {"account": "gmail.work"}},
        )
    )
    assert enkidu.bless_calls[0].requested_scope == {"account": "gmail.work"}


# ----------------------------- collect_blessings: failure modes ----------


def test_collect_blessings_partial_denial():
    enkidu = MockGod(
        name="enkidu",
        bless_outcomes={
            "gmail.send": _ok_blessing("gmail.send", god="enkidu"),
            "gmail.read": _denial("gmail.read", "no read scope", god="enkidu"),
        },
    )
    result = asyncio.run(
        collect_blessings(
            creature_id="x",
            capabilities=["gmail.send", "gmail.read"],
            gods={"enkidu": enkidu},
            routes=ROUTES,
        )
    )
    assert not result.ok
    assert "gmail.send" in result.blessings
    assert "gmail.read" in result.denials
    assert result.denials["gmail.read"].reason == "no read scope"


def test_collect_blessings_unrouted_capability():
    """A capability whose prefix has no owning god goes in unrouted[]."""
    result = asyncio.run(
        collect_blessings(
            creature_id="x",
            capabilities=["psychic.divine"],
            gods={},
            routes=ROUTES,
        )
    )
    assert not result.ok
    assert result.unrouted == ["psychic.divine"]


def test_collect_blessings_owning_god_not_installed():
    """Routes know the prefix but the god isn't in the gods map → unrouted."""
    result = asyncio.run(
        collect_blessings(
            creature_id="x",
            capabilities=["memory.recall"],
            gods={},  # mnemosyne not installed
            routes=ROUTES,
        )
    )
    assert not result.ok
    assert result.unrouted == ["memory.recall"]


def test_collect_blessings_god_raises_becomes_denial():
    enkidu = MockGod(name="enkidu", raise_on_bless=RuntimeError("policy db down"))
    result = asyncio.run(
        collect_blessings(
            creature_id="x",
            capabilities=["gmail.send"],
            gods={"enkidu": enkidu},
            routes=ROUTES,
        )
    )
    assert not result.ok
    assert "gmail.send" in result.denials
    assert "RuntimeError" in result.denials["gmail.send"].reason
    assert "policy db down" in result.denials["gmail.send"].reason


def test_collect_blessings_god_returns_garbage_becomes_denial():
    """Defensive: a god that returns the wrong type still produces a Denial."""

    class BrokenGod:
        async def bless(self, *, creature_id, request):
            return "this is not a Blessing"

        async def commission_angel(self, *, creature_id):
            return None

    result = asyncio.run(
        collect_blessings(
            creature_id="x",
            capabilities=["gmail.send"],
            gods={"enkidu": BrokenGod()},  # type: ignore[arg-type]
            routes=ROUTES,
        )
    )
    assert not result.ok
    assert "expected Blessing or Denial" in result.denials["gmail.send"].reason


def test_collect_blessings_empty_capabilities():
    result = asyncio.run(
        collect_blessings(
            creature_id="x",
            capabilities=[],
            gods={"enkidu": MockGod(name="enkidu")},
            routes=ROUTES,
        )
    )
    assert result.ok
    assert result.blessings == {}


# ----------------------------- BlessingResult.format_report --------------


def test_blessing_result_format_report_lists_grants_and_denials():
    enkidu = MockGod(
        name="enkidu",
        bless_outcomes={
            "gmail.send": _ok_blessing("gmail.send", god="enkidu"),
            "gmail.read": _denial(
                "gmail.read", "no read scope", god="enkidu", approval=True
            ),
        },
    )
    result = asyncio.run(
        collect_blessings(
            creature_id="x",
            capabilities=["gmail.send", "gmail.read", "psychic.divine"],
            gods={"enkidu": enkidu},
            routes=ROUTES,
        )
    )
    out = result.format_report()
    assert "blessings granted: gmail.send" in out
    assert "gmail.read" in out
    assert "no read scope" in out
    assert "operator approval would unblock" in out
    assert "psychic.divine" in out
    assert "no god owns this capability prefix" in out


# ----------------------------- collect_angel_commissions -----------------


def test_collect_angel_commissions_all_return_none():
    result = asyncio.run(
        collect_angel_commissions(
            creature_id="x",
            gods={"enkidu": MockGod(name="enkidu")},
        )
    )
    assert result.specs == []
    assert result.errors == {}


def test_collect_angel_commissions_one_god_provides():
    spec = AngelSpec(god="enkidu", name="audit", creature_id="placeholder")
    enkidu = MockGod(name="enkidu", angel_to_commission=spec)
    result = asyncio.run(
        collect_angel_commissions(
            creature_id="email_pm",
            gods={"enkidu": enkidu},
        )
    )
    assert len(result.specs) == 1
    s = result.specs[0]
    assert s.god == "enkidu"
    assert s.name == "audit"
    assert s.creature_id == "email_pm"  # creature_id stamped at commission time


def test_collect_angel_commissions_multiple_gods_stable_order():
    """Stable god-name ordering so two forges produce identical angel sets."""
    e_spec = AngelSpec(god="enkidu", name="audit", creature_id="x")
    m_spec = AngelSpec(god="mnemosyne", name="memory", creature_id="x")
    enkidu = MockGod(name="enkidu", angel_to_commission=e_spec)
    mnemosyne = MockGod(name="mnemosyne", angel_to_commission=m_spec)
    arachne = MockGod(name="arachne")  # returns None
    result = asyncio.run(
        collect_angel_commissions(
            creature_id="email_pm",
            gods={"mnemosyne": mnemosyne, "enkidu": enkidu, "arachne": arachne},
        )
    )
    # Order by god name (alphabetical); arachne returns None so isn't here.
    assert [s.god for s in result.specs] == ["enkidu", "mnemosyne"]


def test_collect_angel_commissions_god_raises_records_error():
    enkidu = MockGod(name="enkidu", raise_on_commission=RuntimeError("oops"))
    result = asyncio.run(
        collect_angel_commissions(
            creature_id="x",
            gods={"enkidu": enkidu},
        )
    )
    assert result.specs == []
    assert "enkidu" in result.errors
    assert "RuntimeError" in result.errors["enkidu"]


def test_collect_angel_commissions_god_returns_garbage_records_error():
    class BrokenGod:
        async def bless(self, *, creature_id, request):
            return Denial(capability="x", creature_id="x", god="b", reason="n/a")

        async def commission_angel(self, *, creature_id):
            return "definitely not an AngelSpec"

    result = asyncio.run(
        collect_angel_commissions(
            creature_id="x",
            gods={"broken": BrokenGod()},  # type: ignore[arg-type]
        )
    )
    assert result.specs == []
    assert "broken" in result.errors
    assert "expected AngelSpec" in result.errors["broken"]


def test_collect_angel_commissions_empty_gods():
    result = asyncio.run(
        collect_angel_commissions(creature_id="x", gods={})
    )
    assert result.specs == []
    assert result.errors == {}


# ----------------------------- Blessing expiration -----------------------


def test_blessing_is_expired_with_future_expires_at():
    from datetime import timedelta

    now = datetime.now(tz=timezone.utc)
    b = Blessing(
        capability="x.y",
        creature_id="z",
        god="g",
        issued_at=now,
        expires_at=now + timedelta(hours=1),
    )
    assert not b.is_expired()


def test_blessing_is_expired_with_past_expires_at():
    from datetime import timedelta

    now = datetime.now(tz=timezone.utc)
    b = Blessing(
        capability="x.y",
        creature_id="z",
        god="g",
        issued_at=now - timedelta(hours=2),
        expires_at=now - timedelta(hours=1),
    )
    assert b.is_expired()


def test_blessing_no_expiration_never_expires():
    """A blessing without ``expires_at`` lives for the Creature's lifetime."""
    now = datetime.now(tz=timezone.utc)
    b = Blessing(capability="x.y", creature_id="z", god="g", issued_at=now)
    assert not b.is_expired()
