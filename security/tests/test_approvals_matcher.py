"""Tests for the standing-approval matcher and predicate language."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from stevens_security.approvals.matcher import (
    MatcherIndex,
    StandingApproval,
)
from stevens_security.approvals.predicates import (
    PredicateError,
    match_predicate,
)


# --- predicates ---


def test_literal_string_match() -> None:
    assert match_predicate("apt", "apt")
    assert not match_predicate("apt", "pip")


def test_glob_match() -> None:
    assert match_predicate({"glob": "gmail.*"}, "gmail.personal")
    assert not match_predicate({"glob": "gmail.*"}, "calendar.work")


def test_regex_match() -> None:
    assert match_predicate({"regex": r"^deb\.debian\..*$"}, "deb.debian.org/debian")
    assert not match_predicate({"regex": r"^deb\.debian\..*$"}, "evil.example.com")


def test_in_set() -> None:
    assert match_predicate({"in": ["apt", "pip", "git"]}, "apt")
    assert not match_predicate({"in": ["apt"]}, "pip")


def test_list_shorthand_for_in() -> None:
    assert match_predicate(["apt", "pip"], "pip")
    assert not match_predicate(["apt"], "git")


def test_numeric_ge_le_between() -> None:
    assert match_predicate({"ge": 5}, 10)
    assert not match_predicate({"ge": 5}, 4)
    assert match_predicate({"le": 5}, 5)
    assert match_predicate({"between": [1, 10]}, 5)
    assert not match_predicate({"between": [1, 10]}, 100)


def test_predicate_unknown_kind_raises() -> None:
    with pytest.raises(PredicateError):
        match_predicate({"weird_op": "x"}, "y")


def test_predicate_too_many_kinds_raises() -> None:
    with pytest.raises(PredicateError):
        match_predicate({"glob": "a*", "regex": "b"}, "ax")


def test_invalid_regex_raises() -> None:
    with pytest.raises(PredicateError, match="invalid regex"):
        match_predicate({"regex": "[unclosed"}, "anything")


# --- matcher index ---


def _utc(seconds_offset: int = 0) -> datetime:
    return datetime(2026, 4, 30, 12, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=seconds_offset)


def test_match_returns_first_matching_approval() -> None:
    sa = StandingApproval(
        id="A",
        capability="system.execute_privileged",
        caller="installer",
        predicates={"mechanism": "apt"},
    )
    idx = MatcherIndex([sa], clock=_utc)
    out = idx.match(
        capability="system.execute_privileged",
        caller="installer",
        params={"mechanism": "apt", "packages": ["tesseract-ocr"]},
    )
    assert out.matched
    assert out.approval_id == "A"


def test_no_match_when_capability_differs() -> None:
    sa = StandingApproval(
        id="A",
        capability="system.execute_privileged",
        caller="installer",
        predicates={},
    )
    idx = MatcherIndex([sa])
    out = idx.match(capability="other.cap", caller="installer", params={})
    assert not out.matched


def test_no_match_when_caller_differs() -> None:
    sa = StandingApproval(
        id="A", capability="X", caller="installer", predicates={},
    )
    idx = MatcherIndex([sa])
    assert not idx.match(capability="X", caller="email_pm", params={}).matched


def test_missing_predicate_field_is_no_match() -> None:
    """Approval requires `mechanism=apt` but call didn't include mechanism → no match."""
    sa = StandingApproval(
        id="A", capability="X", caller="c", predicates={"mechanism": "apt"},
    )
    idx = MatcherIndex([sa])
    assert not idx.match(capability="X", caller="c", params={}).matched


def test_empty_predicates_matches_anything() -> None:
    sa = StandingApproval(
        id="A", capability="X", caller="c", predicates={},
    )
    idx = MatcherIndex([sa])
    assert idx.match(capability="X", caller="c", params={"anything": "goes"}).matched


def test_multiple_predicates_must_all_match() -> None:
    sa = StandingApproval(
        id="A", capability="X", caller="c",
        predicates={
            "mechanism": "apt",
            "source": {"regex": r"^deb\.debian\..*$"},
        },
    )
    idx = MatcherIndex([sa])
    assert idx.match(
        capability="X", caller="c",
        params={"mechanism": "apt", "source": "deb.debian.org/bookworm"},
    ).matched
    assert not idx.match(
        capability="X", caller="c",
        params={"mechanism": "apt", "source": "evil.example.com/x"},
    ).matched
    assert not idx.match(
        capability="X", caller="c",
        params={"mechanism": "pip", "source": "deb.debian.org/bookworm"},
    ).matched


def test_param_matchers_nested() -> None:
    sa = StandingApproval(
        id="A", capability="X", caller="c",
        predicates={
            "mechanism": "apt",
            "param_matchers": {"sha256": {"in": ["abc", "def"]}},
        },
    )
    idx = MatcherIndex([sa])
    assert idx.match(
        capability="X", caller="c",
        params={"mechanism": "apt", "sha256": "abc"},
    ).matched
    assert not idx.match(
        capability="X", caller="c",
        params={"mechanism": "apt", "sha256": "xyz"},
    ).matched


def test_revoked_skipped() -> None:
    sa = StandingApproval(
        id="A", capability="X", caller="c", predicates={},
        revoked_at=_utc(),
    )
    idx = MatcherIndex([sa])
    assert not idx.match(capability="X", caller="c", params={}).matched


def test_expired_skipped() -> None:
    sa = StandingApproval(
        id="A", capability="X", caller="c", predicates={},
        expires_at=_utc(-3600),  # expired one hour ago
    )
    idx = MatcherIndex([sa], clock=_utc)
    assert not idx.match(capability="X", caller="c", params={}).matched


def test_unexpired_matches() -> None:
    sa = StandingApproval(
        id="A", capability="X", caller="c", predicates={},
        expires_at=_utc(3600),  # one hour from "now"
    )
    idx = MatcherIndex([sa], clock=_utc)
    assert idx.match(capability="X", caller="c", params={}).matched


def test_session_bound_match() -> None:
    sa = StandingApproval(
        id="A", capability="X", caller="c", predicates={},
        expires_session="boot-1",
    )
    idx = MatcherIndex([sa], current_session="boot-1")
    assert idx.match(capability="X", caller="c", params={}).matched
    idx.set_session("boot-2")
    assert not idx.match(capability="X", caller="c", params={}).matched


def test_replace_all_swaps_index() -> None:
    sa1 = StandingApproval(id="A", capability="X", caller="c", predicates={})
    idx = MatcherIndex([sa1])
    assert idx.match(capability="X", caller="c", params={}).matched
    idx.replace_all([])
    assert not idx.match(capability="X", caller="c", params={}).matched


def test_first_match_wins() -> None:
    """When multiple approvals could match, the first one is returned."""
    sa1 = StandingApproval(id="A", capability="X", caller="c", predicates={"mechanism": "apt"})
    sa2 = StandingApproval(id="B", capability="X", caller="c", predicates={})  # broader
    idx = MatcherIndex([sa1, sa2])
    out = idx.match(capability="X", caller="c", params={"mechanism": "apt"})
    assert out.matched and out.approval_id == "A"
