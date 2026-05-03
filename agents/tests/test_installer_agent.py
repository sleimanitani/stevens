"""Installer agent tests — fully mocked, no Enkidu / no apt."""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import patch

import pytest

from installer import agent as installer_agent
from installer.plan_builder import PlanBuildError, build_apt_plan
from shared.events import SystemDepRequestedEvent
from shared.security_client import (
    BlockedError,
    DenyError,
    SecurityClientError,
)


class FakeSecurityClient:
    """Records calls and returns scripted responses keyed by capability."""

    def __init__(self, responses: Dict[str, Any]) -> None:
        # responses: {capability_name: response_or_exception (callable or value)}
        self._responses = responses
        self.calls: List[tuple[str, Dict[str, Any]]] = []

    async def call(self, capability: str, params: Dict[str, Any] | None = None) -> Dict[str, Any]:
        params = params or {}
        self.calls.append((capability, params))
        if capability not in self._responses:
            raise AssertionError(f"unexpected call to {capability}")
        v = self._responses[capability]
        if callable(v):
            v = v(params)
        if isinstance(v, BaseException):
            raise v
        return v


class FakeBus:
    def __init__(self) -> None:
        self.published: List[Any] = []

    async def publish(self, event_obj: Any) -> Any:
        self.published.append(event_obj)
        return None


@pytest.fixture
def fake_bus(monkeypatch):
    fb = FakeBus()
    monkeypatch.setattr(installer_agent.bus, "publish", fb.publish)
    yield fb


def _make_event(package: str = "tesseract-ocr") -> SystemDepRequestedEvent:
    return SystemDepRequestedEvent(
        account_id="system",
        package=package,
    )


_OS_RELEASE_UBUNTU_22 = {"id": "ubuntu", "version_id": "22.04"}


# --- happy path ---


@pytest.mark.asyncio
async def test_install_ok_publishes_installed(fake_bus):
    fc = FakeSecurityClient(
        {
            "system.read_environment": {
                "os_release": _OS_RELEASE_UBUNTU_22,
                "dpkg_status": {"tesseract-ocr": {"installed": False}},
            },
            "system.plan_install": {"validated": True, "plan_id": "p-1"},
            "system.execute_privileged": {"outcome": "ok", "inventory_id": "inv-1"},
        }
    )
    installer_agent._set_client_for_tests(fc)

    await installer_agent.handle(_make_event(), config={})

    topics = [e.topic for e in fake_bus.published]
    assert topics == ["system.dep.installed.tesseract-ocr"]
    assert fake_bus.published[0].inventory_id == "inv-1"


# --- already installed ---


@pytest.mark.asyncio
async def test_already_installed_short_circuits(fake_bus):
    fc = FakeSecurityClient(
        {
            "system.read_environment": {
                "os_release": _OS_RELEASE_UBUNTU_22,
                "dpkg_status": {
                    "tesseract-ocr": {"installed": True, "version": "5.3.0-2"}
                },
            },
        }
    )
    installer_agent._set_client_for_tests(fc)

    await installer_agent.handle(_make_event(), config={})

    assert len(fake_bus.published) == 1
    e = fake_bus.published[0]
    assert e.topic == "system.dep.installed.tesseract-ocr"
    assert e.reason == "already_installed"
    assert e.version == "5.3.0-2"


# --- BLOCKED awaits approval ---


@pytest.mark.asyncio
async def test_blocked_publishes_awaiting_approval(fake_bus):
    fc = FakeSecurityClient(
        {
            "system.read_environment": {
                "os_release": _OS_RELEASE_UBUNTU_22,
                "dpkg_status": {"tesseract-ocr": {"installed": False}},
            },
            "system.plan_install": {"validated": True, "plan_id": "p-1"},
            "system.execute_privileged": BlockedError(
                "BLOCKED", "approval pending", "trace-1",
                approval_request_id="req-42",
            ),
        }
    )
    installer_agent._set_client_for_tests(fc)

    await installer_agent.handle(_make_event(), config={})

    assert len(fake_bus.published) == 1
    e = fake_bus.published[0]
    assert e.topic == "system.dep.awaiting_approval.tesseract-ocr"
    assert e.approval_request_id == "req-42"
    assert e.plan_id == "p-1"


# --- denied ---


@pytest.mark.asyncio
async def test_denied_publishes_failed(fake_bus):
    fc = FakeSecurityClient(
        {
            "system.read_environment": {
                "os_release": _OS_RELEASE_UBUNTU_22,
                "dpkg_status": {"tesseract-ocr": {"installed": False}},
            },
            "system.plan_install": {"validated": True, "plan_id": "p-1"},
            "system.execute_privileged": DenyError("DENY", "rejected by sol", "t-1"),
        }
    )
    installer_agent._set_client_for_tests(fc)

    await installer_agent.handle(_make_event(), config={})

    assert len(fake_bus.published) == 1
    e = fake_bus.published[0]
    assert e.topic == "system.dep.failed.tesseract-ocr"
    assert e.reason == "denied"


# --- plan validation failure ---


@pytest.mark.asyncio
async def test_plan_validation_failure_publishes_failed(fake_bus):
    fc = FakeSecurityClient(
        {
            "system.read_environment": {
                "os_release": _OS_RELEASE_UBUNTU_22,
                "dpkg_status": {"tesseract-ocr": {"installed": False}},
            },
            "system.plan_install": {"validated": False, "errors": ["bad plan"]},
        }
    )
    installer_agent._set_client_for_tests(fc)

    await installer_agent.handle(_make_event(), config={})

    assert fake_bus.published[0].reason == "plan_invalid"


# --- unsupported OS ---


@pytest.mark.asyncio
async def test_unsupported_os_publishes_failed(fake_bus):
    fc = FakeSecurityClient(
        {
            "system.read_environment": {
                "os_release": {"id": "alpine", "version_id": "3.19"},
                "dpkg_status": {"tesseract-ocr": {"installed": False}},
            },
        }
    )
    installer_agent._set_client_for_tests(fc)

    await installer_agent.handle(_make_event(), config={})

    e = fake_bus.published[0]
    assert e.topic == "system.dep.failed.tesseract-ocr"
    assert e.reason == "plan_build_failed"


# --- non-dep events ignored ---


@pytest.mark.asyncio
async def test_non_dep_event_is_silently_ignored(fake_bus):
    from shared.events import EmailReceivedEvent

    installer_agent._set_client_for_tests(FakeSecurityClient({}))
    irrelevant = EmailReceivedEvent(
        account_id="gmail.x", message_id="m", thread_id="t",
        **{"from": "x@y.z"}, raw_ref="r",
    )
    await installer_agent.handle(irrelevant, config={})
    assert fake_bus.published == []


# --- plan_builder unit tests ---


def test_plan_builder_ubuntu_22_jammy():
    plan, rollback = build_apt_plan(
        package="tesseract-ocr",
        env_snapshot={"os_release": _OS_RELEASE_UBUNTU_22},
    )
    assert plan["operation"] == "install"
    assert plan["packages"] == ["tesseract-ocr"]
    assert plan["source"]["suite"] == "jammy"
    assert plan["source"]["repo"].startswith("archive.ubuntu.com")
    assert rollback["operation"] == "purge"


def test_plan_builder_debian_12_bookworm():
    plan, rollback = build_apt_plan(
        package="ffmpeg",
        env_snapshot={"os_release": {"id": "debian", "version_id": "12"}},
    )
    assert plan["source"]["suite"] == "bookworm"
    assert plan["source"]["repo"].startswith("deb.debian.org")


def test_plan_builder_unknown_os_raises():
    with pytest.raises(PlanBuildError, match="unsupported OS"):
        build_apt_plan(
            package="x",
            env_snapshot={"os_release": {"id": "windows", "version_id": "11"}},
        )


def test_plan_builder_unknown_version_raises():
    with pytest.raises(PlanBuildError, match="unsupported OS version"):
        build_apt_plan(
            package="x",
            env_snapshot={"os_release": {"id": "ubuntu", "version_id": "16.04"}},
        )
