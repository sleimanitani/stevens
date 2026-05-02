"""Tests for the Google Cloud onboarding wizard.

All gcloud subprocess calls are mocked. Real end-to-end run is operator-side.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from demiurge.wizards.google import (
    GcloudStatus,
    WizardError,
    WizardInputs,
    check_gcloud,
    consent_screen_step,
    create_project,
    create_pubsub_topic,
    create_push_subscription,
    enable_apis,
    grant_gmail_pubsub_publisher,
    list_projects,
    oauth_client_step,
    run_wizard,
    subscription_exists,
    topic_exists,
    wait_for_client_json,
)


def _ok(stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr=stderr)


def _err(stderr: str = "boom", returncode: int = 1) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout="", stderr=stderr)


# --- check_gcloud ---


def test_check_gcloud_installed_authed(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/gcloud" if name == "gcloud" else None)
    calls = []

    def runner(args):
        calls.append(args)
        if args[0:2] == ["gcloud", "version"]:
            return _ok("Google Cloud SDK 471.0.0\n")
        if args[0:3] == ["gcloud", "config", "list"]:
            return _ok(json.dumps({"core": {"account": "sol@y76.io", "project": "stevens-x"}}))
        return _err()

    status = check_gcloud(runner=runner)
    assert status.installed is True
    assert status.account == "sol@y76.io"
    assert status.project == "stevens-x"


def test_check_gcloud_not_installed(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    status = check_gcloud()
    assert status.installed is False
    assert "Install" in (status.install_hint or "")


def test_check_gcloud_installed_no_auth(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/gcloud")

    def runner(args):
        if args[0:2] == ["gcloud", "version"]:
            return _ok("Google Cloud SDK 471.0.0\n")
        if args[0:3] == ["gcloud", "config", "list"]:
            return _ok(json.dumps({"core": {}}))
        return _err()

    status = check_gcloud(runner=runner)
    assert status.installed is True
    assert status.account is None


# --- project / API ---


def test_list_projects_parses_json():
    def runner(args):
        return _ok(json.dumps([{"projectId": "p1", "name": "Project One"}]))

    out = list_projects(runner=runner)
    assert out == [{"project_id": "p1", "name": "Project One"}]


def test_create_project_idempotent_on_already_exists():
    def runner(args):
        return _err(stderr="The project ID you specified already in use.")

    # Should NOT raise.
    create_project("p1", runner=runner)


def test_create_project_raises_on_real_failure():
    def runner(args):
        return _err(stderr="permission denied")

    with pytest.raises(WizardError, match="failed"):
        create_project("p1", runner=runner)


def test_enable_apis_passes_list():
    captured = []

    def runner(args):
        captured.append(args)
        return _ok()

    enable_apis("p1", runner=runner)
    assert captured[0][:3] == ["gcloud", "services", "enable"]
    assert "gmail.googleapis.com" in captured[0]


# --- manual-step text ---


def test_consent_step_url_includes_project():
    step = consent_screen_step("p1")
    assert "project=p1" in step.url
    assert any("External" in line for line in step.instructions)


def test_oauth_client_step_url_includes_project():
    step = oauth_client_step("p1")
    assert "project=p1" in step.url
    assert any("Desktop application" in line for line in step.instructions)


# --- wait_for_client_json ---


def test_wait_for_client_json_finds_new_file(tmp_path: Path):
    # Pre-existing file should be ignored.
    (tmp_path / "client_secret_OLD.json").write_text("{}")
    # Mock a clock that increments each call; on the third call drop the new file.
    new_path = tmp_path / "client_secret_NEW.json"
    state = {"i": 0}

    def fake_clock():
        state["i"] += 1
        if state["i"] == 3:
            new_path.write_text('{"installed":{}}')
        return float(state["i"])

    with patch("demiurge.wizards.google.time.sleep", lambda s: None):
        out = wait_for_client_json(
            downloads_dir=tmp_path, timeout_s=100, poll_interval_s=0.0,
            clock=fake_clock,
        )
    assert out == new_path


def test_wait_for_client_json_timeout(tmp_path: Path):
    state = {"i": 0}

    def fake_clock():
        state["i"] += 1
        return float(state["i"])

    with patch("demiurge.wizards.google.time.sleep", lambda s: None):
        with pytest.raises(WizardError, match="timed out"):
            wait_for_client_json(
                downloads_dir=tmp_path, timeout_s=2, poll_interval_s=0.0,
                clock=fake_clock,
            )


def test_wait_for_client_json_missing_dir(tmp_path: Path):
    with pytest.raises(WizardError, match="not found"):
        wait_for_client_json(downloads_dir=tmp_path / "missing", timeout_s=1)


# --- pubsub ---


def test_topic_exists_true_on_success():
    def runner(args):
        return _ok("projects/p1/topics/gmail-push")

    assert topic_exists("p1", "gmail-push", runner=runner)


def test_topic_exists_false_on_failure():
    def runner(args):
        return _err()

    assert not topic_exists("p1", "gmail-push", runner=runner)


def test_create_pubsub_topic_returns_resource_path():
    state = {"described": False}

    def runner(args):
        if args[0:3] == ["gcloud", "pubsub", "topics"]:
            if "describe" in args:
                if state["described"]:
                    return _ok("projects/p1/topics/gmail-push")
                state["described"] = True
                return _err()  # first describe → not exists → triggers create
            if "create" in args:
                return _ok("created")
        return _err()

    path = create_pubsub_topic("p1", runner=runner)
    assert path == "projects/p1/topics/gmail-push"


def test_grant_gmail_pubsub_publisher_uses_known_sa():
    captured = []

    def runner(args):
        captured.append(args)
        return _ok()

    grant_gmail_pubsub_publisher("p1", runner=runner)
    assert any("gmail-api-push@system.gserviceaccount.com" in str(a) for a in captured[0])
    assert "roles/pubsub.publisher" in captured[0]


def test_create_push_subscription_returns_path():
    def runner(args):
        if "describe" in args:
            return _err()  # not exists
        return _ok()

    path = create_push_subscription(
        "p1", push_endpoint="https://x.example.com/gmail/push", runner=runner,
    )
    assert path == "projects/p1/subscriptions/gmail-push-sub"


# --- orchestration ---


def test_run_wizard_happy_path(monkeypatch, tmp_path: Path):
    # gcloud appears installed + authed.
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/gcloud")

    seen = []

    def runner(args):
        seen.append(args)
        if args[0:2] == ["gcloud", "version"]:
            return _ok("Google Cloud SDK 471.0.0\n")
        if args[0:3] == ["gcloud", "config", "list"]:
            return _ok(json.dumps({"core": {"account": "sol@y76.io", "project": None}}))
        if args[0:3] == ["gcloud", "projects", "create"]:
            return _ok()
        if args[0:3] == ["gcloud", "services", "enable"]:
            return _ok()
        if "describe" in args:
            return _err()  # nothing exists yet
        if "create" in args or "add-iam-policy-binding" in args:
            return _ok()
        return _err()

    # Drop the JSON file immediately so wait_for_client_json finds it.
    (tmp_path / "client_secret_X.json").write_text("{}")
    # Make the wait_for_client_json see a "new" file by faking pre-existing as empty.
    monkeypatch.setattr(
        "demiurge.wizards.google.wait_for_client_json",
        lambda **kw: tmp_path / "client_secret_X.json",
    )
    monkeypatch.setattr("demiurge.wizards.google.time.sleep", lambda s: None)

    inputs = WizardInputs(
        project_id="stevens-test",
        project_name="Stevens Test",
        push_endpoint="https://x.example.com/gmail/push",
        downloads_dir=tmp_path,
        runner=runner,
        confirm=lambda prompt: True,
        ask=lambda prompt: "https://x.example.com/gmail/push",
        say=lambda s: None,
    )
    result = run_wizard(inputs)
    assert result.project_id == "stevens-test"
    assert "topics/gmail-push" in result.topic_path
    assert "subscriptions/gmail-push-sub" in result.subscription_path


def test_run_wizard_aborts_when_operator_says_no(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/gcloud")

    def runner(args):
        if args[0:2] == ["gcloud", "version"]:
            return _ok("Google Cloud SDK 471.0.0\n")
        if args[0:3] == ["gcloud", "config", "list"]:
            return _ok(json.dumps({"core": {"account": "sol@y76.io"}}))
        return _err()

    inputs = WizardInputs(
        project_id="p", runner=runner,
        confirm=lambda prompt: False,   # always no
        say=lambda s: None,
    )
    with pytest.raises(WizardError, match="aborted at project step"):
        run_wizard(inputs)


def test_run_wizard_refuses_without_gcloud(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    inputs = WizardInputs(project_id="p", say=lambda s: None)
    with pytest.raises(WizardError, match="gcloud not found"):
        run_wizard(inputs)


def test_run_wizard_refuses_without_auth(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/gcloud")

    def runner(args):
        if args[0:2] == ["gcloud", "version"]:
            return _ok()
        if args[0:3] == ["gcloud", "config", "list"]:
            return _ok(json.dumps({"core": {}}))   # no account
        return _err()

    inputs = WizardInputs(
        project_id="p", runner=runner, confirm=lambda p: True, say=lambda s: None,
    )
    with pytest.raises(WizardError, match="no account is logged in"):
        run_wizard(inputs)
