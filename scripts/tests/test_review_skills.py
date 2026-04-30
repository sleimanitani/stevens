"""Unit tests for the file-promotion logic in scripts/review_skills.py.

DB-touching paths are exercised by the higher-level integration test in
the Email PM rewire step. Here we focus on ``promote_into_repo``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

# Load the script as a module — it lives outside any package.
_SPEC = importlib.util.spec_from_file_location(
    "review_skills",
    Path(__file__).resolve().parents[1] / "review_skills.py",
)
review_skills = importlib.util.module_from_spec(_SPEC)
sys.modules["review_skills"] = review_skills
_SPEC.loader.exec_module(review_skills)


_PB_BODY = """---
name: appointment-request
description: triage incoming meeting requests
version: 1.0.0
author: email_pm
license: proprietary
metadata:
  applies_to_topics: [email.received.*]
  applies_to_agents: [email_pm]
  triggers:
    - regex: "(?i)(meeting|schedule|call)"
  status: active
---

## When to apply
A meeting request.
"""


def _build_repo(tmp_path: Path) -> Path:
    """Stand up a minimal repo skeleton: skills/ with proposed/ + registry.yaml."""
    (tmp_path / "skills" / "proposed" / "playbooks").mkdir(parents=True)
    (tmp_path / "skills" / "proposed" / "tools").mkdir()
    (tmp_path / "skills" / "src" / "skills" / "playbooks").mkdir(parents=True)
    (tmp_path / "skills" / "src" / "skills" / "tools").mkdir()
    (tmp_path / "skills" / "registry.yaml").write_text(
        "tools: []\nplaybooks: []\n"
    )
    return tmp_path


def test_promote_playbook_moves_file_and_updates_registry(tmp_path: Path) -> None:
    repo = _build_repo(tmp_path)
    proposed = repo / "skills" / "proposed" / "playbooks" / "appointment-12345.md"
    proposed.write_text(_PB_BODY)

    new_path = review_skills.promote_into_repo(
        repo_root=repo,
        body_path_rel="skills/proposed/playbooks/appointment-12345.md",
        kind="playbook",
        proposed_id="appointment",
        category="email",
    )
    assert new_path.exists()
    assert not proposed.exists()
    # Registry has a playbooks entry.
    reg = yaml.safe_load((repo / "skills" / "registry.yaml").read_text())
    assert len(reg["playbooks"]) == 1
    entry = reg["playbooks"][0]
    assert entry["id"] == "email/appointment-request"
    assert entry["applies_to_agents"] == ["email_pm"]
    assert entry["applies_to_topics"] == ["email.received.*"]


def test_promote_tool_writes_correct_registry_shape(tmp_path: Path) -> None:
    repo = _build_repo(tmp_path)
    proposed = repo / "skills" / "proposed" / "tools" / "frobnicate-cafebabe.py"
    proposed.write_text("def build_tool(): pass\n")

    review_skills.promote_into_repo(
        repo_root=repo,
        body_path_rel="skills/proposed/tools/frobnicate-cafebabe.py",
        kind="tool",
        proposed_id="frobnicate",
        category="utility",
        scope="shared",
        safety_class="read-only",
        version="1.2.3",
    )
    reg = yaml.safe_load((repo / "skills" / "registry.yaml").read_text())
    assert len(reg["tools"]) == 1
    e = reg["tools"][0]
    assert e["id"] == "utility.frobnicate"
    assert e["scope"] == "shared"
    assert e["safety_class"] == "read-only"
    assert e["version"] == "1.2.3"
    assert e["path"].startswith("skills/src/skills/tools/utility/")


def test_promote_restricted_tool_records_allowed_agents(tmp_path: Path) -> None:
    repo = _build_repo(tmp_path)
    proposed = repo / "skills" / "proposed" / "tools" / "private-deadbeef.py"
    proposed.write_text("def build_tool(): pass\n")

    review_skills.promote_into_repo(
        repo_root=repo,
        body_path_rel="skills/proposed/tools/private-deadbeef.py",
        kind="tool",
        proposed_id="private",
        category="security",
        scope="restricted",
        allowed_agents=["security_agent"],
    )
    reg = yaml.safe_load((repo / "skills" / "registry.yaml").read_text())
    assert reg["tools"][0]["allowed_agents"] == ["security_agent"]


def test_promote_missing_file_errors(tmp_path: Path) -> None:
    repo = _build_repo(tmp_path)
    with pytest.raises(FileNotFoundError):
        review_skills.promote_into_repo(
            repo_root=repo,
            body_path_rel="skills/proposed/tools/missing.py",
            kind="tool",
            proposed_id="x",
            category="x",
        )
