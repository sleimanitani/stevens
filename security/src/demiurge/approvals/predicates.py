"""Predicate matchers for standing approvals.

A predicate is a constraint on a single field value. Each standing approval
has zero or more predicates; missing fields = "any" for that field.

Match semantics: `match_predicate(predicate_dict, value) -> bool`.

The predicate language is deliberately small. Adding a new shape is a
deliberate decision; don't proliferate.
"""

from __future__ import annotations

import fnmatch
import re
from typing import Any, Iterable


class PredicateError(Exception):
    """Raised on malformed predicate definitions."""


_KNOWN_KINDS = {"literal", "glob", "regex", "in", "ge", "le", "between"}


def _expect_known_kind(spec: dict) -> str:
    """Return the single recognized kind key in ``spec``.

    A predicate dict must have exactly one structural key (`glob`, `regex`,
    `in`, etc.). Plain literals are passed as the value directly (not as
    a dict) and treated as `literal`.
    """
    kinds = [k for k in spec.keys() if k in _KNOWN_KINDS]
    if len(kinds) != 1:
        raise PredicateError(
            f"predicate spec must have exactly one of {_KNOWN_KINDS}, got {sorted(spec.keys())}"
        )
    return kinds[0]


def match_predicate(predicate: Any, value: Any) -> bool:
    """Return True if ``value`` matches the ``predicate`` definition.

    ``predicate`` may be:
    - a plain string/int/bool   → equality match (literal)
    - a list                    → membership match (literal "in")
    - a dict with one of {glob, regex, in, ge, le, between}
    """
    if predicate is None:
        # Convention: missing predicate at the call site means "any" — caller
        # should not invoke us. If you do, it's a no-op pass.
        return True

    # Plain scalars: literal equality.
    if isinstance(predicate, (str, int, float, bool)):
        return value == predicate

    # Plain list: membership.
    if isinstance(predicate, list):
        return value in predicate

    if not isinstance(predicate, dict):
        raise PredicateError(f"predicate must be scalar/list/dict, got {type(predicate).__name__}")

    kind = _expect_known_kind(predicate)
    spec = predicate[kind]

    if kind == "literal":
        return value == spec

    if kind == "glob":
        if not isinstance(value, str) or not isinstance(spec, str):
            return False
        return fnmatch.fnmatchcase(value, spec)

    if kind == "regex":
        if not isinstance(value, str) or not isinstance(spec, str):
            return False
        try:
            pattern = re.compile(spec)
        except re.error as e:
            raise PredicateError(f"invalid regex {spec!r}: {e}") from e
        return pattern.search(value) is not None

    if kind == "in":
        if not isinstance(spec, (list, tuple, set)):
            raise PredicateError(f"'in' expects a list, got {type(spec).__name__}")
        return value in spec

    if kind == "ge":
        return _numeric_compare(value, spec, ">=")

    if kind == "le":
        return _numeric_compare(value, spec, "<=")

    if kind == "between":
        if not (isinstance(spec, (list, tuple)) and len(spec) == 2):
            raise PredicateError("'between' expects a 2-element [lo, hi] list")
        return _numeric_compare(value, spec[0], ">=") and _numeric_compare(value, spec[1], "<=")

    raise PredicateError(f"unknown predicate kind {kind!r}")


def _numeric_compare(value: Any, threshold: Any, op: str) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    if not isinstance(threshold, (int, float)):
        return False
    if op == ">=":
        return value >= threshold
    if op == "<=":
        return value <= threshold
    return False  # unreachable
