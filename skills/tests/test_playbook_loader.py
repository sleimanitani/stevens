"""Tests for skills.playbooks.loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from skills.playbooks.loader import (
    Playbook,
    PlaybookError,
    load_all,
    load_playbook,
)


_VALID = """---
name: email-appointment-request
description: Triage incoming meeting/call requests
version: 1.0.0
author: email_pm
license: proprietary
metadata:
  applies_to_topics: [email.received.*]
  applies_to_agents: [email_pm]
  triggers:
    - regex: "(?i)(meeting|call|schedule)"
  status: active
  supersedes: null
---

## When to apply
Incoming meeting/call request.

## Procedure
1. Acknowledge.
"""


def _write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(body)
    return p


def test_well_formed_playbook_parses(tmp_path: Path) -> None:
    p = _write(tmp_path, "appointment.md", _VALID)
    pb = load_playbook(p)
    assert pb.name == "email-appointment-request"
    assert pb.description.startswith("Triage")
    assert pb.version == "1.0.0"
    assert pb.author == "email_pm"
    assert pb.license == "proprietary"
    assert pb.status == "active"
    assert pb.is_active
    assert pb.applies_to_topics == ["email.received.*"]
    assert pb.applies_to_agents == ["email_pm"]
    assert len(pb.triggers) == 1
    assert pb.triggers[0].regex.search("Can we schedule a meeting?")
    assert "## Procedure" in pb.body


def test_missing_required_top_level_field_errors(tmp_path: Path) -> None:
    body = _VALID.replace("name: email-appointment-request\n", "")
    p = _write(tmp_path, "broken.md", body)
    with pytest.raises(PlaybookError, match="missing required top-level field 'name'"):
        load_playbook(p)


def test_no_frontmatter_errors(tmp_path: Path) -> None:
    p = _write(tmp_path, "x.md", "just markdown, no frontmatter\n")
    with pytest.raises(PlaybookError, match="no YAML frontmatter"):
        load_playbook(p)


def test_invalid_yaml_in_frontmatter_errors(tmp_path: Path) -> None:
    p = _write(tmp_path, "x.md", "---\nname: x\nversion: [unclosed\n---\nbody\n")
    with pytest.raises(PlaybookError, match="invalid yaml"):
        load_playbook(p)


def test_invalid_regex_errors(tmp_path: Path) -> None:
    body = _VALID.replace('"(?i)(meeting|call|schedule)"', '"(unclosed"')
    p = _write(tmp_path, "x.md", body)
    with pytest.raises(PlaybookError, match="invalid trigger regex"):
        load_playbook(p)


def test_status_validated(tmp_path: Path) -> None:
    body = _VALID.replace("status: active", "status: bogus")
    p = _write(tmp_path, "x.md", body)
    with pytest.raises(PlaybookError, match="status must be one of"):
        load_playbook(p)


def test_status_defaults_to_proposed(tmp_path: Path) -> None:
    body = _VALID.replace("  status: active\n", "")
    p = _write(tmp_path, "x.md", body)
    pb = load_playbook(p)
    assert pb.status == "proposed"
    assert not pb.is_active


def test_load_all_recurses(tmp_path: Path) -> None:
    (tmp_path / "email").mkdir()
    _write(tmp_path / "email", "a.md", _VALID)
    _write(tmp_path / "email", "b.md", _VALID)
    pbs = load_all(tmp_path)
    assert len(pbs) == 2


def test_version_yaml_float_coerced_to_string(tmp_path: Path) -> None:
    """yaml will parse `version: 1.0` as a float — we coerce to str."""
    body = _VALID.replace("version: 1.0.0", "version: 1.0")
    p = _write(tmp_path, "x.md", body)
    pb = load_playbook(p)
    assert pb.version == "1.0"
