"""Tests for demiurge.doctor."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from demiurge import doctor
from demiurge.provision import provision_agent
from demiurge.sealed_store import initialize_store


@pytest.fixture
def workspace(tmp_path: Path):
    return {
        "secrets_root": tmp_path / "vault",
        "agents_yaml": tmp_path / "agents.yaml",
        "capabilities_yaml": tmp_path / "capabilities.yaml",
        "agents_dir": tmp_path / "agents",
        "socket_path": str(tmp_path / "missing.sock"),
    }


def _run(workspace, **overrides) -> doctor.DoctorReport:
    args = {
        "secrets_root": workspace["secrets_root"],
        "socket_path": workspace["socket_path"],
        "agents_yaml": workspace["agents_yaml"],
        "capabilities_yaml": workspace["capabilities_yaml"],
        "agents_dir": workspace["agents_dir"],
        **overrides,
    }
    return doctor.run_doctor(**args)


def test_doctor_clean_install_reports_missing_store(workspace) -> None:
    report = _run(workspace)
    names = {c.name: c for c in report.checks}
    assert names["sealed-store-exists"].ok is False
    assert "demiurge secrets init" in (names["sealed-store-exists"].remediation or "")


def test_doctor_passes_with_initialized_store_and_provisioned_agent(
    workspace, monkeypatch
) -> None:
    initialize_store(workspace["secrets_root"], b"hunter2")
    monkeypatch.setenv("DEMIURGE_PASSPHRASE", "hunter2")
    provision_agent(
        name="email_pm",
        preset_name="email_pm",
        agents_yaml=workspace["agents_yaml"],
        capabilities_yaml=workspace["capabilities_yaml"],
        agents_dir=workspace["agents_dir"],
    )
    # Touch the socket file so the running check passes.
    Path(workspace["socket_path"]).touch()

    report = _run(workspace)
    failures = report.failed
    assert failures == [], f"unexpected failures: {[c.message for c in failures]}"


def test_doctor_detects_loose_key_perms(workspace, monkeypatch) -> None:
    initialize_store(workspace["secrets_root"], b"x")
    monkeypatch.setenv("DEMIURGE_PASSPHRASE", "x")
    provision_agent(
        name="email_pm",
        preset_name=None,
        agents_yaml=workspace["agents_yaml"],
        capabilities_yaml=workspace["capabilities_yaml"],
        agents_dir=workspace["agents_dir"],
    )
    key = workspace["agents_dir"] / "email_pm.key"
    key.chmod(0o644)  # too loose

    report = _run(workspace)
    failures = [c for c in report.failed if c.name.startswith("agent-key:")]
    assert failures
    assert "loose perms" in failures[0].message


def test_doctor_detects_orphan_policy_entry(workspace, monkeypatch) -> None:
    initialize_store(workspace["secrets_root"], b"x")
    monkeypatch.setenv("DEMIURGE_PASSPHRASE", "x")
    # Write a capabilities.yaml referencing an agent that's not in agents.yaml.
    workspace["capabilities_yaml"].write_text(
        yaml.safe_dump(
            {"agents": [{"name": "ghost", "allow": [{"capability": "ping"}]}]}
        )
    )
    workspace["agents_yaml"].write_text(yaml.safe_dump({"agents": []}))

    report = _run(workspace)
    orphan_check = next(c for c in report.checks if c.name == "policy-refs-agents")
    assert orphan_check.ok is False
    assert "ghost" in orphan_check.message


def test_doctor_warns_on_docker_group_membership(workspace, monkeypatch) -> None:
    """v0.10 step 5: doctor flags docker-group membership as a warning."""
    from demiurge.bootstrap import preflight as bp

    monkeypatch.setattr(bp, "in_docker_group", lambda user=None: True)
    report = _run(workspace)
    docker_check = next(c for c in report.checks if c.name == "docker-group")
    assert docker_check.ok is False
    assert docker_check.info is True  # warning, not blocker
    assert "passwordless root" in docker_check.message
    assert "gpasswd" in (docker_check.remediation or "")
    # And the report must still pass overall (info=True means non-blocker):
    assert docker_check not in report.failed


def test_doctor_passes_when_not_in_docker_group(workspace, monkeypatch) -> None:
    from demiurge.bootstrap import preflight as bp

    monkeypatch.setattr(bp, "in_docker_group", lambda user=None: False)
    report = _run(workspace)
    docker_check = next(c for c in report.checks if c.name == "docker-group")
    assert docker_check.ok is True


def test_format_report_includes_remediation_lines(workspace) -> None:
    """The formatted output should surface the remediation for failed checks."""
    report = _run(workspace)
    out = doctor.format_report(report)
    # No store + no passphrase → at least one remediation arrow shown.
    assert "→" in out
    assert "demiurge secrets init" in out
