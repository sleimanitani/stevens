"""Tests for CapabilityRegistry."""

import pytest

from stevens_security.capabilities.registry import (
    CapabilityRegistry,
    RegistryError,
)


@pytest.mark.asyncio
async def test_register_and_get():
    reg = CapabilityRegistry()

    async def handler(agent, params):
        return {"hi": "there"}

    reg.register("test.op", handler)
    spec = reg.get("test.op")
    assert spec is not None
    assert spec.name == "test.op"
    assert spec.handler is handler
    # account_id is always considered clear
    assert "account_id" in spec.clear_params


def test_register_with_clear_params():
    reg = CapabilityRegistry()

    async def handler(agent, params):
        return {}

    reg.register("test.op", handler, clear_params=["model", "length"])
    spec = reg.get("test.op")
    assert spec is not None
    assert "model" in spec.clear_params
    assert "length" in spec.clear_params
    assert "account_id" in spec.clear_params  # always included


def test_duplicate_registration_rejected():
    reg = CapabilityRegistry()

    async def h(agent, params):
        return {}

    reg.register("dup", h)
    with pytest.raises(RegistryError, match="already registered"):
        reg.register("dup", h)


def test_get_returns_none_for_unknown():
    reg = CapabilityRegistry()
    assert reg.get("nope") is None


def test_names_reflects_registrations():
    reg = CapabilityRegistry()

    async def h(agent, params):
        return {}

    reg.register("a", h)
    reg.register("b", h)
    assert reg.names() == frozenset({"a", "b"})


def test_decorator_form():
    reg = CapabilityRegistry()

    @reg.capability("test.decorated", clear_params=["safe"])
    async def h(agent, params):
        return {"decorated": True}

    spec = reg.get("test.decorated")
    assert spec is not None and spec.handler is h
    assert "safe" in spec.clear_params


def test_isolated_registries_do_not_leak():
    a = CapabilityRegistry()
    b = CapabilityRegistry()

    async def h(agent, params):
        return {}

    a.register("x", h)
    assert a.get("x") is not None
    assert b.get("x") is None


def test_module_level_capability_uses_default_registry():
    # Import is fine — the module registers `ping` on the default_registry.
    from stevens_security.capabilities.ping import ping  # noqa: F401
    from stevens_security.capabilities.registry import default_registry

    assert default_registry.get("ping") is not None


def test_unregister_for_tests():
    reg = CapabilityRegistry()

    async def h(agent, params):
        return {}

    reg.register("temp", h)
    assert reg.get("temp") is not None
    reg.unregister("temp")
    assert reg.get("temp") is None
    # Unregistering a missing one is a no-op, not an error.
    reg.unregister("never-was")
