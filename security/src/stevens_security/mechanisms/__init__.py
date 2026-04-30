"""Install mechanisms — strategies for privileged execution.

A **mechanism** owns four pure functions:

- ``validate_plan(plan_dict) -> ValidatedPlan | ValidationError``
- ``build_executor(validated_plan) -> Executor`` (argv + env + timeout)
- ``evaluate_health_check(plan, exec_result) -> bool``
- ``validate_rollback(install_plan, rollback_plan) -> bool``

Subprocess execution itself is centralised in
``security.capabilities.system`` so every mechanism's executor is a pure
data structure (argv list + env dict + timeout). Tests mock the
subprocess, exercise the mechanism's data shape, and never touch the OS.

v0.3 ships only ``apt``. New mechanisms (pip, git, opt_dir, container) add
modules under this package; ``register()`` registers them by name.
"""

from __future__ import annotations

from typing import Dict

from .base import Executor, Mechanism, ValidatedPlan, ValidationError


_REGISTRY: Dict[str, Mechanism] = {}


def register(name: str, mechanism: Mechanism) -> None:
    if name in _REGISTRY:
        raise ValueError(f"mechanism {name!r} already registered")
    _REGISTRY[name] = mechanism


def get(name: str) -> Mechanism:
    if name not in _REGISTRY:
        raise KeyError(f"unknown mechanism: {name!r}; registered: {sorted(_REGISTRY)}")
    return _REGISTRY[name]


def known() -> list[str]:
    return sorted(_REGISTRY)


# Register built-in mechanisms.
from . import apt  # noqa: E402, F401 — side-effect: registers itself
