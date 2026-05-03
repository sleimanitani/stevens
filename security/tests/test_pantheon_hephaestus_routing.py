"""Tests for demiurge.pantheon.hephaestus — v0.11 step 3c."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from demiurge.pantheon.hephaestus import (
    DEFAULT_ROUTES,
    ArachneGod,
    BlessedToolWrapper,
    EnkiduGod,
    IrisStubGod,
    JanusGod,
    MnemosyneStubGod,
    SphinxGod,
    ZeusStubGod,
    forge_blessed_registry,
)
from demiurge.policy import Policy, load_policy
from shared.creatures.context import MortalContext
from shared.creatures.dispatch import collect_angel_commissions, collect_blessings
from shared.creatures.feed import (
    KIND_TOOL_CALL_END,
    KIND_TOOL_CALL_START,
    ObservationFeed,
)
from shared.creatures.tools import (
    Blessing,
    Denial,
    ToolDispatchError,
    ToolNotBlessed,
    ToolRegistry,
    with_context,
)


# ----------------------------- fixtures ----------------------------------


@pytest.fixture
def feed(tmp_path: Path) -> ObservationFeed:
    return ObservationFeed("email_pm.personal", base=tmp_path)


def _ctx(feed: ObservationFeed, tools: ToolRegistry) -> MortalContext:
    return MortalContext(
        creature_id=feed.creature_id,
        display_name="Test Mortal",
        audit=feed,
        logger=logging.getLogger("test"),
        llm=object(),  # type: ignore[arg-type]
        tools=tools,
        memory=object(),  # type: ignore[arg-type]
        bus=object(),  # type: ignore[arg-type]
    )


def _blessing(cap: str, *, creature_id: str = "email_pm.personal", god: str = "enkidu") -> Blessing:
    return Blessing(
        capability=cap,
        creature_id=creature_id,
        god=god,
        issued_at=datetime.now(tz=timezone.utc),
    )


# ----------------------------- DEFAULT_ROUTES ----------------------------


def test_default_routes_covers_known_capabilities():
    """Sanity: every capability prefix the existing codebase uses today
    must have an owning god in DEFAULT_ROUTES, otherwise blessings can't
    route at all."""
    expected = {
        "gmail", "calendar", "whatsapp", "whatsapp_cloud", "signal",
        "system", "_admin",
        "web", "network",
        "pdf",
        "browser",
        "memory", "say", "zeus",
    }
    assert expected <= set(DEFAULT_ROUTES.keys())


def test_default_routes_each_god_has_capabilities():
    """No god is unreachable — every god in DEFAULT_ROUTES is the target
    of at least one prefix."""
    targets = set(DEFAULT_ROUTES.values())
    assert {"enkidu", "arachne", "sphinx", "janus", "mnemosyne", "iris", "zeus"} <= targets


# ----------------------------- EnkiduGod (real policy) -------------------


def _write_policy(tmp_path: Path, policy_dict: dict) -> Path:
    p = tmp_path / "capabilities.yaml"
    p.write_text(yaml.safe_dump(policy_dict))
    return p


def test_enkidu_god_blesses_when_policy_allows(tmp_path: Path):
    p_path = _write_policy(
        tmp_path,
        {
            "agents": [
                {
                    "name": "email_pm.personal",
                    "allow": [{"capability": "gmail.send"}],
                }
            ]
        },
    )
    enkidu = EnkiduGod(policy=load_policy(p_path))
    from shared.creatures.tools import ToolRequest

    result = asyncio.run(
        enkidu.bless(
            creature_id="email_pm.personal",
            request=ToolRequest(
                capability="gmail.send", creature_id="email_pm.personal"
            ),
        )
    )
    assert isinstance(result, Blessing)
    assert result.capability == "gmail.send"
    assert result.god == "enkidu"


def test_enkidu_god_denies_when_no_policy(tmp_path: Path):
    p_path = _write_policy(tmp_path, {"agents": []})
    enkidu = EnkiduGod(policy=load_policy(p_path))
    from shared.creatures.tools import ToolRequest

    result = asyncio.run(
        enkidu.bless(
            creature_id="missing.creature",
            request=ToolRequest(
                capability="gmail.send", creature_id="missing.creature"
            ),
        )
    )
    assert isinstance(result, Denial)
    assert "no policy for caller" in result.reason


def test_enkidu_god_denies_when_capability_not_allowed(tmp_path: Path):
    p_path = _write_policy(
        tmp_path,
        {
            "agents": [
                {
                    "name": "email_pm.personal",
                    "allow": [{"capability": "gmail.read"}],  # only read
                }
            ]
        },
    )
    enkidu = EnkiduGod(policy=load_policy(p_path))
    from shared.creatures.tools import ToolRequest

    result = asyncio.run(
        enkidu.bless(
            creature_id="email_pm.personal",
            request=ToolRequest(
                capability="gmail.send", creature_id="email_pm.personal"
            ),
        )
    )
    assert isinstance(result, Denial)
    assert "no rule matches" in result.reason


def test_enkidu_god_account_scope_passes_through(tmp_path: Path):
    p_path = _write_policy(
        tmp_path,
        {
            "agents": [
                {
                    "name": "email_pm.personal",
                    "allow": [
                        {
                            "capability": "gmail.send",
                            "accounts": ["gmail.personal"],
                        }
                    ],
                }
            ]
        },
    )
    enkidu = EnkiduGod(policy=load_policy(p_path))
    from shared.creatures.tools import ToolRequest

    # In-scope account → ok
    ok = asyncio.run(
        enkidu.bless(
            creature_id="email_pm.personal",
            request=ToolRequest(
                capability="gmail.send",
                creature_id="email_pm.personal",
                requested_scope={"account_id": "gmail.personal"},
            ),
        )
    )
    assert isinstance(ok, Blessing)

    # Out-of-scope account → denial
    bad = asyncio.run(
        enkidu.bless(
            creature_id="email_pm.personal",
            request=ToolRequest(
                capability="gmail.send",
                creature_id="email_pm.personal",
                requested_scope={"account_id": "gmail.work"},
            ),
        )
    )
    assert isinstance(bad, Denial)


def test_enkidu_god_always_commissions_audit_angel():
    """Mandatory: every Creature gets an audit angel from Enkidu, full stop."""
    enkidu = EnkiduGod(policy=Policy())
    spec = asyncio.run(enkidu.commission_angel(creature_id="any_creature"))
    assert spec is not None
    assert spec.god == "enkidu"
    assert spec.name == "audit"
    assert spec.creature_id == "any_creature"


# ----------------------------- blanket-allow gods (sanity) ---------------


def test_arachne_god_blesses_known_capabilities():
    arachne = ArachneGod()
    from shared.creatures.tools import ToolRequest

    result = asyncio.run(
        arachne.bless(
            creature_id="x",
            request=ToolRequest(capability="web.fetch", creature_id="x"),
        )
    )
    assert isinstance(result, Blessing)


def test_arachne_god_denies_unknown_capability():
    arachne = ArachneGod()
    from shared.creatures.tools import ToolRequest

    result = asyncio.run(
        arachne.bless(
            creature_id="x",
            request=ToolRequest(capability="web.deep_dive", creature_id="x"),
        )
    )
    assert isinstance(result, Denial)
    assert "does not bless" in result.reason


def test_sphinx_god_pdf_only():
    sphinx = SphinxGod()
    from shared.creatures.tools import ToolRequest

    ok = asyncio.run(
        sphinx.bless(
            creature_id="x",
            request=ToolRequest(capability="pdf.read", creature_id="x"),
        )
    )
    bad = asyncio.run(
        sphinx.bless(
            creature_id="x",
            request=ToolRequest(capability="gmail.send", creature_id="x"),
        )
    )
    assert isinstance(ok, Blessing)
    assert isinstance(bad, Denial)


def test_janus_god_blesses_browser_recipe():
    janus = JanusGod()
    from shared.creatures.tools import ToolRequest

    result = asyncio.run(
        janus.bless(
            creature_id="x",
            request=ToolRequest(capability="browser.run_recipe", creature_id="x"),
        )
    )
    assert isinstance(result, Blessing)


def test_iris_stub_blesses_nothing():
    """Iris is Sol-facing; Mortals shouldn't be calling Iris directly."""
    iris = IrisStubGod()
    from shared.creatures.tools import ToolRequest

    result = asyncio.run(
        iris.bless(
            creature_id="x",
            request=ToolRequest(capability="say.speak", creature_id="x"),
        )
    )
    assert isinstance(result, Denial)


def test_zeus_stub_blesses_request_spawn_only():
    zeus = ZeusStubGod()
    from shared.creatures.tools import ToolRequest

    ok = asyncio.run(
        zeus.bless(
            creature_id="x",
            request=ToolRequest(
                capability="zeus.request_spawn", creature_id="x"
            ),
        )
    )
    bad = asyncio.run(
        zeus.bless(
            creature_id="x",
            request=ToolRequest(capability="zeus.veto", creature_id="x"),
        )
    )
    assert isinstance(ok, Blessing)
    assert isinstance(bad, Denial)


def test_mnemosyne_stub_commissions_memory_angel():
    mnemosyne = MnemosyneStubGod()
    spec = asyncio.run(mnemosyne.commission_angel(creature_id="email_pm.personal"))
    assert spec is not None
    assert spec.god == "mnemosyne"
    assert spec.name == "memory"
    assert spec.creature_id == "email_pm.personal"
    assert spec.config.get("v0.11_stub") is True


def test_iris_stub_commissions_no_angel():
    """Iris doesn't observe Mortals — Sol-facing only."""
    iris = IrisStubGod()
    spec = asyncio.run(iris.commission_angel(creature_id="x"))
    assert spec is None


# ----------------------------- BlessedToolWrapper ------------------------


def test_blessed_tool_wrapper_dispatches_and_audits(feed: ObservationFeed):
    """Happy path: wrapper records start/end events around dispatch."""
    blessing = _blessing("gmail.send", creature_id=feed.creature_id, god="enkidu")
    captured = {}

    async def dispatcher(ctx, *, capability, blessing, **kwargs):
        captured["capability"] = capability
        captured["kwargs"] = kwargs
        captured["god"] = blessing.god
        return {"sent": True, "id": "msg_123"}

    wrapper = BlessedToolWrapper(blessing=blessing, dispatcher=dispatcher)
    ctx = _ctx(feed, ToolRegistry({}))

    result = asyncio.run(wrapper(ctx, to="alice@example.com", body="hi"))

    assert result == {"sent": True, "id": "msg_123"}
    assert captured["capability"] == "gmail.send"
    assert captured["god"] == "enkidu"
    assert captured["kwargs"] == {"to": "alice@example.com", "body": "hi"}

    events = list(feed.read_all())
    assert len(events) == 2
    start, end = events
    assert start.kind == KIND_TOOL_CALL_START
    assert start.data["capability"] == "gmail.send"
    assert end.kind == KIND_TOOL_CALL_END
    assert end.correlation_id == start.event_id  # join key works
    assert end.data["result"] == {"sent": True, "id": "msg_123"}


def test_blessed_tool_wrapper_records_dispatch_error(feed: ObservationFeed):
    """When dispatcher raises, end event records the error (no result)."""
    blessing = _blessing("gmail.send", creature_id=feed.creature_id)

    async def dispatcher(ctx, *, capability, blessing, **kwargs):
        raise RuntimeError("upstream timeout")

    wrapper = BlessedToolWrapper(blessing=blessing, dispatcher=dispatcher)
    ctx = _ctx(feed, ToolRegistry({}))

    with pytest.raises(RuntimeError, match="upstream timeout"):
        asyncio.run(wrapper(ctx))

    events = list(feed.read_all())
    assert len(events) == 2
    assert events[1].kind == KIND_TOOL_CALL_END
    assert "RuntimeError" in events[1].data["error"]
    assert "upstream timeout" in events[1].data["error"]
    assert "result" not in events[1].data
    # Correlation still works on the failure path.
    assert events[1].correlation_id == events[0].event_id


def test_blessed_tool_wrapper_rejects_expired_blessing(feed: ObservationFeed):
    past = datetime.now(tz=timezone.utc) - timedelta(hours=1)
    blessing = Blessing(
        capability="gmail.send",
        creature_id=feed.creature_id,
        god="enkidu",
        issued_at=past - timedelta(hours=1),
        expires_at=past,
    )

    async def dispatcher(ctx, **kwargs):
        return None

    wrapper = BlessedToolWrapper(blessing=blessing, dispatcher=dispatcher)
    ctx = _ctx(feed, ToolRegistry({}))

    with pytest.raises(ToolDispatchError, match="expired"):
        asyncio.run(wrapper(ctx))


def test_blessed_tool_wrapper_rejects_creature_id_mismatch(feed: ObservationFeed):
    """A wrapper bound to creature A can't be called from creature B's context.

    This is the v0.11 floor on blessing-replay protection; v0.12+ adds
    Ed25519 signatures over the blessing token itself."""
    blessing = _blessing("gmail.send", creature_id="some_other_creature")

    async def dispatcher(ctx, **kwargs):
        return None

    wrapper = BlessedToolWrapper(blessing=blessing, dispatcher=dispatcher)
    ctx = _ctx(feed, ToolRegistry({}))  # ctx.creature_id = email_pm.personal

    with pytest.raises(ToolDispatchError, match="creature_id mismatch"):
        asyncio.run(wrapper(ctx))


def test_blessed_tool_wrapper_handles_unserializable_args(feed: ObservationFeed):
    """Audit must not fail when args contain non-JSON values."""
    blessing = _blessing("gmail.send", creature_id=feed.creature_id)

    async def dispatcher(ctx, **kwargs):
        return "ok"

    wrapper = BlessedToolWrapper(blessing=blessing, dispatcher=dispatcher)
    ctx = _ctx(feed, ToolRegistry({}))

    class Weird:
        def __repr__(self):
            return "<Weird obj>"

    asyncio.run(wrapper(ctx, payload=Weird()))
    events = list(feed.read_all())
    assert events[0].kind == KIND_TOOL_CALL_START
    assert events[0].data["args"] == {
        "_unserializable_repr": "{'payload': <Weird obj>}"
    }


# ----------------------------- forge_blessed_registry --------------------


def test_forge_blessed_registry_includes_universal_by_default(feed: ObservationFeed):
    """think + mortal.return are in the registry alongside blessed tools."""

    async def dispatcher(ctx, **kwargs):
        return "ok"

    blessings = {"gmail.send": _blessing("gmail.send", god="enkidu")}
    registry = forge_blessed_registry(
        blessings=blessings,
        dispatchers={"enkidu": dispatcher},
    )
    assert set(registry.names()) == {"think", "mortal.return", "gmail.send"}


def test_forge_blessed_registry_omits_universal_when_requested():
    async def dispatcher(ctx, **kwargs):
        return "ok"

    blessings = {"gmail.send": _blessing("gmail.send", god="enkidu")}
    registry = forge_blessed_registry(
        blessings=blessings,
        dispatchers={"enkidu": dispatcher},
        include_universal=False,
    )
    assert set(registry.names()) == {"gmail.send"}


def test_forge_blessed_registry_raises_on_missing_dispatcher():
    """Fail-loud: a blessing with no dispatcher must surface, not silent-drop."""
    blessings = {"web.fetch": _blessing("web.fetch", god="arachne")}
    with pytest.raises(ValueError, match="arachne.*dispatcher"):
        forge_blessed_registry(blessings=blessings, dispatchers={})


def test_forge_blessed_registry_descriptions_used_for_llm_prompt():
    async def dispatcher(ctx, **kwargs):
        return None

    blessings = {"gmail.send": _blessing("gmail.send", god="enkidu")}
    registry = forge_blessed_registry(
        blessings=blessings,
        dispatchers={"enkidu": dispatcher},
        descriptions={"gmail.send": "Send an email via Gmail."},
    )
    assert registry.get("gmail.send").description == "Send an email via Gmail."


def test_forge_blessed_registry_default_description_when_unset():
    async def dispatcher(ctx, **kwargs):
        return None

    blessings = {"gmail.send": _blessing("gmail.send", god="enkidu")}
    registry = forge_blessed_registry(
        blessings=blessings,
        dispatchers={"enkidu": dispatcher},
    )
    desc = registry.get("gmail.send").description
    assert "gmail.send" in desc
    assert "enkidu" in desc


# ----------------------------- end-to-end (collect + forge + invoke) -----


def test_end_to_end_collect_blessings_forge_registry_invoke(
    tmp_path: Path, feed: ObservationFeed
):
    """End-to-end: real Enkidu policy → collect → forge → invoke."""

    # 1. Set up Enkidu with a real policy.
    p_path = _write_policy(
        tmp_path,
        {
            "agents": [
                {
                    "name": feed.creature_id,
                    "allow": [{"capability": "gmail.send"}],
                }
            ]
        },
    )
    enkidu = EnkiduGod(policy=load_policy(p_path))

    # 2. Collect blessings via the dispatcher.
    result = asyncio.run(
        collect_blessings(
            creature_id=feed.creature_id,
            capabilities=["gmail.send"],
            gods={"enkidu": enkidu},
            routes=DEFAULT_ROUTES,
        )
    )
    assert result.ok

    # 3. Forge the registry with a fake dispatcher.
    captured = {}

    async def fake_dispatcher(ctx, *, capability, blessing, **kwargs):
        captured["capability"] = capability
        captured["kwargs"] = kwargs
        return f"sent: {kwargs.get('to')}"

    registry = forge_blessed_registry(
        blessings=result.blessings,
        dispatchers={"enkidu": fake_dispatcher},
    )

    # 4. Invoke the tool through the registry.
    ctx = _ctx(feed, registry)

    async def run():
        with with_context(ctx):
            return await registry.invoke("gmail.send", to="bob@example.com")

    out = asyncio.run(run())
    assert out == "sent: bob@example.com"
    assert captured == {"capability": "gmail.send", "kwargs": {"to": "bob@example.com"}}

    # 5. Audit feed has start + end + nothing else.
    events = list(feed.read_all())
    assert [e.kind for e in events] == [KIND_TOOL_CALL_START, KIND_TOOL_CALL_END]


def test_end_to_end_unknown_tool_raises_not_blessed(tmp_path: Path, feed: ObservationFeed):
    """A tool not in the manifest's blessings → not in the registry."""
    p_path = _write_policy(tmp_path, {"agents": []})
    enkidu = EnkiduGod(policy=load_policy(p_path))
    result = asyncio.run(
        collect_blessings(
            creature_id=feed.creature_id,
            capabilities=[],
            gods={"enkidu": enkidu},
            routes=DEFAULT_ROUTES,
        )
    )

    async def fake_dispatcher(ctx, **kw):
        return None

    registry = forge_blessed_registry(
        blessings=result.blessings,
        dispatchers={"enkidu": fake_dispatcher},
    )
    ctx = _ctx(feed, registry)

    async def run():
        with with_context(ctx):
            return await registry.invoke("gmail.send", to="bob")

    with pytest.raises(ToolNotBlessed, match="gmail.send"):
        asyncio.run(run())


def test_end_to_end_collect_angel_commissions_includes_enkidu_audit(tmp_path: Path):
    """Enkidu audit angel is mandatory — collect_angel_commissions returns it."""
    p_path = _write_policy(tmp_path, {"agents": []})
    enkidu = EnkiduGod(policy=load_policy(p_path))
    mnemosyne = MnemosyneStubGod()
    iris = IrisStubGod()

    result = asyncio.run(
        collect_angel_commissions(
            creature_id="email_pm.personal",
            gods={"enkidu": enkidu, "mnemosyne": mnemosyne, "iris": iris},
        )
    )

    # Enkidu (audit) + Mnemosyne (memory stub) → two angels. Iris → none.
    god_names = {s.god for s in result.specs}
    assert god_names == {"enkidu", "mnemosyne"}
    assert {s.name for s in result.specs} == {"audit", "memory"}
    for s in result.specs:
        assert s.creature_id == "email_pm.personal"
