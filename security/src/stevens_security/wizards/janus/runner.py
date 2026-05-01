"""Recipe runner — iterates Steps, dispatches to BrowserSession, fills slots."""

from __future__ import annotations

import logging
from typing import Awaitable, Callable, Dict, List

from .recipe import PlatformRecipe, RecipeError, RecipeResult
from .session import BrowserSession
from .steps import (
    Click,
    Extract,
    Fill,
    Nav,
    OperatorAction,
    Step,
    StoreSecret,
    WaitFor,
)


log = logging.getLogger(__name__)


# Operator-action callback: takes the message, returns True to proceed,
# False to abort. Tests pass a fake; the CLI passes one that prints +
# reads stdin.
OperatorPrompt = Callable[[str], Awaitable[bool]]


# Sealed-store write callback: takes (name, value, metadata) and writes
# to the sealed store. Tests pass a recorder; the CLI passes a real
# wrapper that calls SealedStore.add.
SecretWriter = Callable[[str, str, dict], Awaitable[None]]


async def execute_recipe(
    recipe: PlatformRecipe,
    *,
    session: BrowserSession,
    ask_operator: OperatorPrompt,
    write_secret: SecretWriter,
    recipe_kwargs: dict | None = None,
) -> RecipeResult:
    """Run a recipe end-to-end. Returns RecipeResult with extracted slots
    + names of secrets written.

    Aborts (RecipeError) if the operator declines an OperatorAction.
    """
    recipe_kwargs = recipe_kwargs or {}
    slots: Dict[str, str] = {}
    stored: List[str] = []

    steps = recipe.steps(**recipe_kwargs)
    for i, step in enumerate(steps):
        log.debug("recipe %s step %d: %s", recipe.name, i, type(step).__name__)
        if isinstance(step, Nav):
            await session.nav(step.url)
        elif isinstance(step, WaitFor):
            await session.wait(step.selector, timeout_s=step.timeout_s)
        elif isinstance(step, Fill):
            await session.fill(step.selector, step.value)
        elif isinstance(step, Click):
            await session.click(step.selector)
        elif isinstance(step, OperatorAction):
            ok = await ask_operator(step.message)
            if not ok:
                raise RecipeError(
                    f"recipe {recipe.name!r} aborted by operator at step {i} ({step.message!r})"
                )
        elif isinstance(step, Extract):
            value = await session.extract(step.selector, use_value=step.use_value)
            if not value:
                raise RecipeError(
                    f"recipe {recipe.name!r}: Extract from {step.selector!r} produced empty value"
                )
            slots[step.into_slot] = value
        elif isinstance(step, StoreSecret):
            if step.slot not in slots:
                raise RecipeError(
                    f"recipe {recipe.name!r}: StoreSecret references missing slot {step.slot!r}"
                )
            await write_secret(
                step.secret_name, slots[step.slot],
                {"kind": step.metadata_kind, "recipe": recipe.name},
            )
            stored.append(step.secret_name)
        else:
            raise RecipeError(
                f"recipe {recipe.name!r}: unknown step type {type(step).__name__}"
            )

    return RecipeResult(name=recipe.name, slots=slots, stored_secrets=stored)
