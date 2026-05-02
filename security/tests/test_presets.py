"""Tests for demiurge.presets — preset loader + merger."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from demiurge.presets import (
    Preset,
    PresetError,
    PresetRule,
    list_presets,
    load_preset,
    merge_into_capabilities,
)


# --- shipped presets parse correctly ---


def test_shipped_email_pm_preset_loads() -> None:
    p = load_preset("email_pm")
    assert p.name == "email_pm"
    caps = {r.capability for r in p.allow}
    assert "gmail.search" in caps
    assert "gmail.create_draft" in caps
    assert "calendar.list_events" in caps
    # Drafting calendar invites is not in this preset.
    assert "calendar.insert_event" not in caps


def test_shipped_subject_agent_preset_loads() -> None:
    p = load_preset("subject_agent")
    caps = {r.capability for r in p.allow}
    assert "whatsapp.send_text" in caps
    assert "gmail.create_draft" in caps


def test_shipped_interface_preset_loads() -> None:
    p = load_preset("interface")
    assert [r.capability for r in p.allow] == ["ping"]


def test_list_presets_finds_all_three() -> None:
    names = set(list_presets())
    assert {"email_pm", "subject_agent", "interface"}.issubset(names)


# --- error paths ---


def test_unknown_preset_raises(tmp_path: Path) -> None:
    with pytest.raises(PresetError, match="unknown preset"):
        load_preset("does_not_exist", presets_dir=tmp_path)


def test_malformed_preset_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("allow: not_a_list\n")
    with pytest.raises(PresetError, match="must be a list"):
        load_preset("bad", presets_dir=tmp_path)


def test_preset_with_invalid_yaml_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("allow: [\n  unclosed")
    with pytest.raises(PresetError, match="invalid yaml"):
        load_preset("bad", presets_dir=tmp_path)


def test_preset_allow_entry_missing_capability_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("allow:\n  - accounts: ['x']\n")
    with pytest.raises(PresetError, match="missing 'capability'"):
        load_preset("bad", presets_dir=tmp_path)


# --- merge_into_capabilities behavior ---


def _read_yaml(p: Path) -> dict:
    return yaml.safe_load(p.read_text())


def test_merge_into_empty_file_creates_entry(tmp_path: Path) -> None:
    target = tmp_path / "capabilities.yaml"
    preset = Preset(
        name="email_pm",
        allow=[
            PresetRule(capability="gmail.search", accounts=["gmail.*"]),
            PresetRule(capability="ping"),
        ],
    )
    changed = merge_into_capabilities(target, "email_pm", preset)
    assert changed is True

    data = _read_yaml(target)
    assert data == {
        "agents": [
            {
                "name": "email_pm",
                "allow": [
                    {"capability": "gmail.search", "accounts": ["gmail.*"]},
                    {"capability": "ping"},
                ],
            }
        ]
    }


def test_merge_is_idempotent(tmp_path: Path) -> None:
    target = tmp_path / "capabilities.yaml"
    preset = Preset(
        name="email_pm",
        allow=[PresetRule(capability="gmail.search", accounts=["gmail.*"])],
    )
    assert merge_into_capabilities(target, "email_pm", preset) is True
    # Second call: identical → no-op.
    assert merge_into_capabilities(target, "email_pm", preset) is False


def test_merge_appends_new_agent_when_others_exist(tmp_path: Path) -> None:
    target = tmp_path / "capabilities.yaml"
    target.write_text(
        yaml.safe_dump(
            {"agents": [{"name": "other", "allow": [{"capability": "ping"}]}]}
        )
    )
    preset = Preset(
        name="email_pm",
        allow=[PresetRule(capability="gmail.search", accounts=["gmail.*"])],
    )
    changed = merge_into_capabilities(target, "email_pm", preset)
    assert changed is True
    data = _read_yaml(target)
    names = [e["name"] for e in data["agents"]]
    assert names == ["other", "email_pm"]


def test_merge_refuses_to_clobber_diverged_rules(tmp_path: Path) -> None:
    target = tmp_path / "capabilities.yaml"
    target.write_text(
        yaml.safe_dump(
            {
                "agents": [
                    {
                        "name": "email_pm",
                        "allow": [{"capability": "gmail.send_email"}],  # different
                    }
                ]
            }
        )
    )
    preset = Preset(
        name="email_pm",
        allow=[PresetRule(capability="gmail.search", accounts=["gmail.*"])],
    )
    with pytest.raises(PresetError, match="already has different allow rules"):
        merge_into_capabilities(target, "email_pm", preset)


def test_merge_creates_parent_dir(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "deeper" / "capabilities.yaml"
    preset = Preset(
        name="email_pm",
        allow=[PresetRule(capability="ping")],
    )
    merge_into_capabilities(target, "email_pm", preset)
    assert target.exists()
