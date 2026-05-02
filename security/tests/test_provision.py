"""Tests for demiurge.provision — agent provisioning."""

from __future__ import annotations

import base64
import os
import stat
from pathlib import Path

import pytest
import yaml

from demiurge.provision import (
    ProvisionError,
    default_agents_dir,
    provision_agent,
)


@pytest.fixture
def workspace(tmp_path: Path):
    """A clean workspace with empty agents.yaml + capabilities.yaml + agents dir."""
    return {
        "agents_yaml": tmp_path / "agents.yaml",
        "capabilities_yaml": tmp_path / "capabilities.yaml",
        "agents_dir": tmp_path / "agents",
    }


def test_provision_happy_path(workspace, tmp_path: Path) -> None:
    result = provision_agent(
        name="email_pm",
        preset_name="email_pm",
        agents_yaml=workspace["agents_yaml"],
        capabilities_yaml=workspace["capabilities_yaml"],
        agents_dir=workspace["agents_dir"],
    )
    # All four artifacts produced.
    assert result.key_path.exists()
    assert result.env_path.exists()
    assert workspace["agents_yaml"].exists()
    assert workspace["capabilities_yaml"].exists()
    # agents.yaml has the new pubkey.
    agents_data = yaml.safe_load(workspace["agents_yaml"].read_text())
    assert agents_data["agents"][0]["name"] == "email_pm"
    assert agents_data["agents"][0]["pubkey_b64"] == result.pubkey_b64
    # capabilities.yaml has the preset rules under email_pm.
    caps_data = yaml.safe_load(workspace["capabilities_yaml"].read_text())
    assert caps_data["agents"][0]["name"] == "email_pm"
    cap_names = {r["capability"] for r in caps_data["agents"][0]["allow"]}
    assert "gmail.search" in cap_names
    assert result.preset_changed is True


def test_provision_writes_env_with_correct_keys(workspace) -> None:
    result = provision_agent(
        name="email_pm",
        preset_name="email_pm",
        agents_yaml=workspace["agents_yaml"],
        capabilities_yaml=workspace["capabilities_yaml"],
        agents_dir=workspace["agents_dir"],
        socket_path="/tmp/test.sock",
    )
    env_text = result.env_path.read_text()
    assert "DEMIURGE_CALLER_NAME=email_pm" in env_text
    assert f"DEMIURGE_PRIVATE_KEY_PATH={result.key_path}" in env_text
    assert "DEMIURGE_SECURITY_SOCKET=/tmp/test.sock" in env_text


def test_provision_key_file_is_0600(workspace) -> None:
    result = provision_agent(
        name="email_pm",
        preset_name=None,
        agents_yaml=workspace["agents_yaml"],
        capabilities_yaml=workspace["capabilities_yaml"],
        agents_dir=workspace["agents_dir"],
    )
    mode = stat.S_IMODE(result.key_path.stat().st_mode)
    assert mode == 0o600


def test_provision_existing_agent_without_force_errors(workspace) -> None:
    provision_agent(
        name="email_pm",
        preset_name=None,
        agents_yaml=workspace["agents_yaml"],
        capabilities_yaml=workspace["capabilities_yaml"],
        agents_dir=workspace["agents_dir"],
    )
    with pytest.raises(ProvisionError, match="already registered"):
        provision_agent(
            name="email_pm",
            preset_name=None,
            agents_yaml=workspace["agents_yaml"],
            capabilities_yaml=workspace["capabilities_yaml"],
            agents_dir=workspace["agents_dir"],
        )


def test_provision_force_rotates_key(workspace) -> None:
    r1 = provision_agent(
        name="email_pm",
        preset_name=None,
        agents_yaml=workspace["agents_yaml"],
        capabilities_yaml=workspace["capabilities_yaml"],
        agents_dir=workspace["agents_dir"],
    )
    r2 = provision_agent(
        name="email_pm",
        preset_name=None,
        agents_yaml=workspace["agents_yaml"],
        capabilities_yaml=workspace["capabilities_yaml"],
        agents_dir=workspace["agents_dir"],
        force=True,
    )
    assert r1.pubkey_b64 != r2.pubkey_b64
    # agents.yaml should still have only one entry for email_pm — the new one.
    data = yaml.safe_load(workspace["agents_yaml"].read_text())
    matching = [e for e in data["agents"] if e["name"] == "email_pm"]
    assert len(matching) == 1
    assert matching[0]["pubkey_b64"] == r2.pubkey_b64


def test_provision_unknown_preset(workspace) -> None:
    with pytest.raises(Exception, match="unknown preset"):
        provision_agent(
            name="email_pm",
            preset_name="totally_made_up",
            agents_yaml=workspace["agents_yaml"],
            capabilities_yaml=workspace["capabilities_yaml"],
            agents_dir=workspace["agents_dir"],
        )
    # Verify no half-provisioned state left behind: key file should not exist.
    assert not (workspace["agents_dir"] / "email_pm.key").exists()


def test_provision_invalid_name(workspace) -> None:
    with pytest.raises(ProvisionError, match="snake_case alnum"):
        provision_agent(
            name="email-pm-with-dashes",
            preset_name=None,
            agents_yaml=workspace["agents_yaml"],
            capabilities_yaml=workspace["capabilities_yaml"],
            agents_dir=workspace["agents_dir"],
        )


def test_provision_no_preset_still_works(workspace) -> None:
    """Provisioning with no preset should still write key + env + agents.yaml entry."""
    result = provision_agent(
        name="newbie",
        preset_name=None,
        agents_yaml=workspace["agents_yaml"],
        capabilities_yaml=workspace["capabilities_yaml"],
        agents_dir=workspace["agents_dir"],
    )
    assert result.preset_applied is None
    assert result.preset_changed is False
    # capabilities.yaml not touched.
    assert not workspace["capabilities_yaml"].exists()


def test_provision_pubkey_decodes_to_32_bytes(workspace) -> None:
    result = provision_agent(
        name="email_pm",
        preset_name=None,
        agents_yaml=workspace["agents_yaml"],
        capabilities_yaml=workspace["capabilities_yaml"],
        agents_dir=workspace["agents_dir"],
    )
    raw = base64.b64decode(result.pubkey_b64)
    assert len(raw) == 32


def test_default_agents_dir_uses_xdg(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    d = default_agents_dir()
    assert d == tmp_path / "xdg" / "demiurge" / "agents"
