"""Tests for shared.tools.propose — file-write + validation paths.

DB-touching tests are skipped when no DATABASE_URL is set (live Postgres
isn't part of the unit-test suite).
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from shared.tools.propose import (
    ProposalResult,
    ProposeSkillError,
    _slugify,
    propose_skill,
)


def test_slugify_basic() -> None:
    assert _slugify("PDF Reader v2") == "pdf-reader-v2"
    assert _slugify("with !@# punct") == "with-punct"
    assert _slugify("") == "untitled"


def test_kind_validation() -> None:
    with pytest.raises(ProposeSkillError, match="kind must be"):
        propose_skill(
            kind="bogus",  # type: ignore[arg-type]
            title="x",
            body="x",
            proposing_agent="email_pm",
        )


def test_title_required() -> None:
    with pytest.raises(ProposeSkillError, match="title is required"):
        propose_skill(
            kind="tool", title="   ", body="b", proposing_agent="a"
        )


def test_body_required() -> None:
    with pytest.raises(ProposeSkillError, match="body is required"):
        propose_skill(
            kind="playbook", title="t", body="", proposing_agent="a"
        )


def test_proposing_agent_required() -> None:
    with pytest.raises(ProposeSkillError, match="proposing_agent"):
        propose_skill(
            kind="tool", title="t", body="b", proposing_agent=""
        )


def test_writes_file_at_expected_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DEMIURGE_SKILLS_PROPOSED", str(tmp_path))
    fake_uuid = "abcd1234-0000-0000-0000-000000000000"

    async def fake_insert(**kwargs):
        import uuid as _uuid
        return _uuid.UUID(fake_uuid)

    with patch("shared.tools.propose._insert_row", side_effect=fake_insert):
        result = propose_skill(
            kind="playbook",
            title="email blocker triage",
            body="some markdown body",
            proposing_agent="email_pm",
        )

    assert isinstance(result, ProposalResult)
    # File written under <tmp>/playbooks/<slug>-<short>.md.
    files = list((tmp_path / "playbooks").glob("email-blocker-triage-*.md"))
    assert len(files) == 1
    assert files[0].read_text() == "some markdown body"


def test_tool_writes_py_extension(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DEMIURGE_SKILLS_PROPOSED", str(tmp_path))

    async def fake_insert(**kwargs):
        import uuid as _uuid
        return _uuid.uuid4()

    with patch("shared.tools.propose._insert_row", side_effect=fake_insert):
        propose_skill(
            kind="tool",
            title="frobnicate",
            body="def x(): pass",
            proposing_agent="x",
        )

    files = list((tmp_path / "tools").glob("frobnicate-*.py"))
    assert len(files) == 1


def test_returns_proposal_id_only_not_path(tmp_path: Path, monkeypatch) -> None:
    """Agent gets the id back but cannot directly use the body path
    to load its own proposal — by convention, ProposalResult is for
    audit reference, not loading.
    """
    monkeypatch.setenv("DEMIURGE_SKILLS_PROPOSED", str(tmp_path))
    import uuid as _uuid

    async def fake_insert(**kwargs):
        return _uuid.uuid4()

    with patch("shared.tools.propose._insert_row", side_effect=fake_insert):
        result = propose_skill(
            kind="tool", title="t", body="b", proposing_agent="a"
        )
    assert isinstance(result.proposal_id, _uuid.UUID)
    # Path is reported for the operator's review CLI to use, but is in
    # skills/proposed/ — explicitly outside the runtime-loaded directories.
    assert "/proposed/" in result.body_path or "proposed/" in result.body_path
