"""Tests for Janus — Steps / Recipe / Runner / built-in recipes.

All tests use FakeBrowserSession; no real Playwright. Real recipe runs
against live provider sites are operator-side.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from demiurge.wizards.janus import (
    Click,
    Extract,
    FakeBrowserSession,
    Fill,
    Nav,
    OperatorAction,
    RecipeError,
    StoreSecret,
    WaitFor,
    execute_recipe,
    get,
    known,
)


# --- Step types ---


def test_step_dataclasses_are_equal_by_value():
    assert Nav("https://x.com") == Nav("https://x.com")
    assert Fill("#a", "v") == Fill("#a", "v")
    assert Fill("#a", "v") != Fill("#a", "other")


# --- Registry ---


def test_known_recipes_includes_three_v07():
    assert {"brave", "anthropic", "google_oauth_client"}.issubset(set(known()))


def test_get_unknown_recipe_raises():
    with pytest.raises(RecipeError, match="unknown recipe"):
        get("does_not_exist")


# --- Built-in recipes — step-list shape ---


def test_brave_recipe_steps_extract_and_store():
    r = get("brave")
    steps = r.steps()
    assert any(isinstance(s, Nav) for s in steps)
    assert any(isinstance(s, OperatorAction) for s in steps)
    assert any(isinstance(s, Extract) and s.into_slot == "api_key" for s in steps)
    assert any(
        isinstance(s, StoreSecret) and s.secret_name == "web.brave.api_key"
        for s in steps
    )


def test_anthropic_recipe_stores_compress_key():
    r = get("anthropic")
    steps = r.steps()
    assert any(
        isinstance(s, StoreSecret) and s.secret_name == "compress.anthropic.api_key"
        for s in steps
    )


def test_google_oauth_client_requires_project_id():
    r = get("google_oauth_client")
    # Needs project_id; calling without it should raise (TypeError from
    # the keyword-only argument).
    with pytest.raises(TypeError):
        r.steps()


def test_google_oauth_client_steps_use_project_id():
    r = get("google_oauth_client")
    steps = r.steps(project_id="stevens-test")
    nav_urls = [s.url for s in steps if isinstance(s, Nav)]
    assert all("project=stevens-test" in url for url in nav_urls)


# --- Runner ---


@pytest.mark.asyncio
async def test_runner_happy_path():
    """Drive the brave recipe with a fake browser + fake operator + fake store."""
    r = get("brave")
    session = FakeBrowserSession(
        extracts={
            # The brave recipe's _KEY_DISPLAY selector covers multiple
            # CSS strings; FakeBrowserSession matches by exact selector.
            "[data-testid='api-key-value'], code.api-key, pre.api-key": "BRV-FAKE-KEY-12345",
        },
    )
    written: List = []

    async def write(name, value, metadata):
        written.append((name, value, metadata))

    async def ask(message: str) -> bool:
        return True   # operator always agrees

    result = await execute_recipe(
        r, session=session, ask_operator=ask, write_secret=write,
    )
    assert "web.brave.api_key" in result.stored_secrets
    assert written == [
        ("web.brave.api_key", "BRV-FAKE-KEY-12345",
         {"kind": "web_brave_api_key", "recipe": "brave"}),
    ]


@pytest.mark.asyncio
async def test_runner_aborts_when_operator_declines():
    r = get("brave")
    session = FakeBrowserSession()
    written: List = []

    async def write(name, value, metadata):
        written.append((name, value))

    async def ask(message: str) -> bool:
        return False   # operator says no on first OperatorAction

    with pytest.raises(RecipeError, match="aborted by operator"):
        await execute_recipe(
            r, session=session, ask_operator=ask, write_secret=write,
        )
    assert written == []


@pytest.mark.asyncio
async def test_runner_extract_empty_value_errors():
    r = get("brave")
    # No extracts dict entry → returns empty string → recipe should fail
    # at the Extract step rather than silently storing an empty key.
    session = FakeBrowserSession(extracts={})
    async def ask(m): return True
    async def write(*a, **k): pass

    with pytest.raises(RecipeError, match="empty value"):
        await execute_recipe(
            r, session=session, ask_operator=ask, write_secret=write,
        )


@pytest.mark.asyncio
async def test_runner_store_missing_slot_errors():
    """Hand-rolled recipe that stores a slot it never extracted."""
    from demiurge.wizards.janus.recipe import register
    from demiurge.wizards.janus.steps import StoreSecret as _SS

    class BadRecipe:
        name = "bad_test_recipe"
        description = "test"
        prerequisites: List[str] = []

        def available(self): return True
        def steps(self, **kwargs):
            return [_SS(slot="never_filled", secret_name="x.y", metadata_kind="t")]

    register(BadRecipe())
    session = FakeBrowserSession()
    async def ask(m): return True
    async def write(*a, **k): pass
    with pytest.raises(RecipeError, match="missing slot"):
        await execute_recipe(
            get("bad_test_recipe"),
            session=session, ask_operator=ask, write_secret=write,
        )


# --- FakeBrowserSession sanity ---


@pytest.mark.asyncio
async def test_fake_session_records_calls():
    sess = FakeBrowserSession(extracts={"#k": "value"})
    await sess.nav("https://x.com")
    await sess.click("#btn")
    out = await sess.extract("#k")
    assert sess.calls == [
        ("nav", "https://x.com"),
        ("click", "#btn"),
        ("extract", "#k"),
    ]
    assert out == "value"
