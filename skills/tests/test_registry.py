"""Tests for skills.registry."""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import pytest
import yaml

from skills.registry import (
    Registry,
    RegistryError,
    ToolEntry,
    get_tools_for_agent,
    load_registry,
)


# --- helpers ---


_TOOL_TEMPLATE = '''
from langchain_core.tools import StructuredTool

def _impl(x: int) -> int:
    return x + 1

def build_tool():
    return StructuredTool.from_function(
        func=_impl,
        name="{name}",
        description="test tool {name}",
    )
'''


def _write_tool(tools_dir: Path, category: str, name: str) -> Path:
    d = tools_dir / category
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{name}.py"
    f.write_text(_TOOL_TEMPLATE.format(name=name))
    return f


def _build_workspace(tmp_path: Path, tools: list[dict]) -> Path:
    tools_root = tmp_path / "skills_src"
    yaml_entries = []
    for spec in tools:
        path = _write_tool(tools_root, spec["category"], spec["name"])
        rel = path.relative_to(tmp_path)
        entry = {
            "id": spec["id"],
            "path": str(rel),
            "scope": spec.get("scope", "shared"),
            "safety_class": spec.get("safety_class", "read-only"),
            "version": spec.get("version", "1.0.0"),
        }
        if spec.get("allowed_agents"):
            entry["allowed_agents"] = spec["allowed_agents"]
        yaml_entries.append(entry)
    reg_yaml = tmp_path / "registry.yaml"
    reg_yaml.write_text(yaml.safe_dump({"tools": yaml_entries, "playbooks": []}))
    return reg_yaml


# --- tests ---


def test_load_empty_registry(tmp_path: Path) -> None:
    p = tmp_path / "registry.yaml"
    p.write_text("tools: []\nplaybooks: []\n")
    reg = load_registry(p, root=tmp_path)
    assert reg.tools == []
    assert reg.playbooks == []


def test_missing_registry_returns_empty(tmp_path: Path) -> None:
    reg = load_registry(tmp_path / "does_not_exist.yaml", root=tmp_path)
    assert reg.tools == []


def test_shared_tool_visible_to_any_agent(tmp_path: Path) -> None:
    p = _build_workspace(
        tmp_path,
        [{"id": "pdf.read_pdf", "category": "pdf", "name": "read_pdf"}],
    )
    reg = load_registry(p, root=tmp_path)
    tools = get_tools_for_agent("email_pm", registry=reg)
    assert len(tools) == 1
    assert tools[0].name == "read_pdf"


def test_restricted_tool_gated_by_allowed_agents(tmp_path: Path) -> None:
    p = _build_workspace(
        tmp_path,
        [
            {
                "id": "security.scan_url",
                "category": "security",
                "name": "scan_url",
                "scope": "restricted",
                "allowed_agents": ["security_agent"],
            }
        ],
    )
    reg = load_registry(p, root=tmp_path)
    assert get_tools_for_agent("email_pm", registry=reg) == []
    assert len(get_tools_for_agent("security_agent", registry=reg)) == 1


def test_exclude_pattern_works(tmp_path: Path) -> None:
    p = _build_workspace(
        tmp_path,
        [
            {"id": "pdf.read_pdf", "category": "pdf", "name": "read_pdf"},
            {"id": "research.linkedin", "category": "research", "name": "linkedin"},
        ],
    )
    reg = load_registry(p, root=tmp_path)
    tools = get_tools_for_agent(
        "email_pm", excludes=["research.*"], registry=reg
    )
    names = [t.name for t in tools]
    assert "read_pdf" in names
    assert "linkedin" not in names


def test_safety_max_caps_destructive(tmp_path: Path) -> None:
    p = _build_workspace(
        tmp_path,
        [
            {
                "id": "tax.delete_form",
                "category": "tax",
                "name": "delete_form",
                "safety_class": "destructive",
            },
            {"id": "pdf.read_pdf", "category": "pdf", "name": "read_pdf"},
        ],
    )
    reg = load_registry(p, root=tmp_path)
    tools = get_tools_for_agent(
        "email_pm", safety_max="read-write", registry=reg
    )
    names = {t.name for t in tools}
    assert "read_pdf" in names
    assert "delete_form" not in names


def test_restricted_tool_without_allowed_agents_errors(tmp_path: Path) -> None:
    p = tmp_path / "registry.yaml"
    p.write_text(
        yaml.safe_dump(
            {
                "tools": [
                    {
                        "id": "x.y",
                        "path": "tools/x/y.py",
                        "scope": "restricted",
                        "safety_class": "read-only",
                    }
                ],
                "playbooks": [],
            }
        )
    )
    with pytest.raises(RegistryError, match="needs allowed_agents"):
        load_registry(p, root=tmp_path)


def test_unknown_scope_errors(tmp_path: Path) -> None:
    p = tmp_path / "registry.yaml"
    p.write_text(
        yaml.safe_dump(
            {
                "tools": [
                    {
                        "id": "x.y",
                        "path": "tools/x/y.py",
                        "scope": "WORLD-WRITABLE",
                        "safety_class": "read-only",
                    }
                ],
                "playbooks": [],
            }
        )
    )
    with pytest.raises(RegistryError, match="scope must be one of"):
        load_registry(p, root=tmp_path)
