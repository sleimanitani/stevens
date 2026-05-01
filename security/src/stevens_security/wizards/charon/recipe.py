"""PlatformRecipe Protocol + registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Protocol

from .steps import Step


class RecipeError(Exception):
    """Raised on recipe registration / lookup errors."""


@dataclass(frozen=True)
class RecipeResult:
    name: str
    slots: Dict[str, str] = field(default_factory=dict)
    stored_secrets: List[str] = field(default_factory=list)


class PlatformRecipe(Protocol):
    name: str
    description: str
    prerequisites: List[str]

    def available(self) -> bool:
        """Cheap prereq check (e.g. is Playwright importable on this host)."""

    def steps(self, **kwargs) -> List[Step]:
        """Return the ordered Step list. May accept recipe-specific kwargs
        (e.g. project_id for the google_oauth_client recipe)."""


_RECIPES: Dict[str, PlatformRecipe] = {}


def register(recipe: PlatformRecipe) -> None:
    if recipe.name in _RECIPES:
        raise RecipeError(f"recipe {recipe.name!r} already registered")
    _RECIPES[recipe.name] = recipe


def get(name: str) -> PlatformRecipe:
    if name not in _RECIPES:
        raise RecipeError(
            f"unknown recipe {name!r}; known: {sorted(_RECIPES)}"
        )
    return _RECIPES[name]


def known() -> List[str]:
    return sorted(_RECIPES)
