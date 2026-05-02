"""Tests for approval forwarding config + matcher."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from demiurge.approvals.forwarding import (
    ForwardingConfig,
    ForwardingConfigError,
    ForwardingRule,
    ForwardingTarget,
    load_config,
    matching_targets,
)
from demiurge.approvals.queue import ApprovalRequest


def _req(caller="installer", capability="system.execute_privileged") -> ApprovalRequest:
    return ApprovalRequest(
        id="r-1", capability=capability, caller=caller,
        params_summary="x", full_envelope={},
    )


def test_empty_config_no_targets(tmp_path: Path):
    p = tmp_path / "f.yaml"
    p.write_text("rules: []\n")
    config = load_config(p)
    assert matching_targets(config, _req()) == []


def test_missing_file_returns_empty(tmp_path: Path):
    config = load_config(tmp_path / "missing.yaml")
    assert config.rules == []


def test_session_mode_uses_origin():
    config = ForwardingConfig(rules=[ForwardingRule(mode="session")])
    targets = matching_targets(
        config, _req(),
        origin_channel="gmail", origin_account_id="gmail.personal",
        origin_thread_id="t-99",
    )
    assert len(targets) == 1
    assert targets[0].channel == "gmail"
    assert targets[0].thread_id == "t-99"


def test_session_mode_skipped_when_no_origin():
    config = ForwardingConfig(rules=[ForwardingRule(mode="session")])
    assert matching_targets(config, _req()) == []


def test_targets_mode_emits_fixed_targets():
    config = ForwardingConfig(rules=[ForwardingRule(
        mode="targets",
        targets=[
            ForwardingTarget(channel="gmail", account_id="gmail.personal"),
            ForwardingTarget(channel="whatsapp_cloud", account_id="wac.work"),
        ],
    )])
    out = matching_targets(config, _req())
    assert {t.channel for t in out} == {"gmail", "whatsapp_cloud"}


def test_both_mode_combines():
    config = ForwardingConfig(rules=[ForwardingRule(
        mode="both",
        targets=[ForwardingTarget(channel="gmail", account_id="gmail.work")],
    )])
    out = matching_targets(
        config, _req(),
        origin_channel="whatsapp_cloud", origin_account_id="wac.x",
    )
    assert len(out) == 2
    channels = {t.channel for t in out}
    assert channels == {"whatsapp_cloud", "gmail"}


def test_agent_filter_excludes_non_matching():
    config = ForwardingConfig(rules=[ForwardingRule(
        mode="targets", agent_filter=["email_pm"],
        targets=[ForwardingTarget(channel="gmail", account_id="x")],
    )])
    assert matching_targets(config, _req(caller="installer")) == []
    assert matching_targets(config, _req(caller="email_pm"))


def test_capability_filter_glob():
    config = ForwardingConfig(rules=[ForwardingRule(
        mode="targets", capability_filter=["system.*"],
        targets=[ForwardingTarget(channel="gmail", account_id="x")],
    )])
    assert matching_targets(config, _req(capability="system.execute_privileged"))
    assert not matching_targets(config, _req(capability="payment.charge"))


def test_load_invalid_mode_raises(tmp_path: Path):
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.safe_dump({"rules": [{"mode": "magic"}]}))
    with pytest.raises(ForwardingConfigError, match="mode"):
        load_config(p)


def test_load_targets_mode_requires_targets(tmp_path: Path):
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.safe_dump({"rules": [{"mode": "targets"}]}))
    with pytest.raises(ForwardingConfigError, match="non-empty targets"):
        load_config(p)


def test_load_target_missing_account_raises(tmp_path: Path):
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.safe_dump({
        "rules": [{"mode": "targets", "targets": [{"channel": "gmail"}]}]
    }))
    with pytest.raises(ForwardingConfigError, match="account_id"):
        load_config(p)
