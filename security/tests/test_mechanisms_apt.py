"""Tests for the apt mechanism — pure validation + data-shape checks."""

from __future__ import annotations

import pytest

from demiurge.mechanisms import get
from demiurge.mechanisms.base import ExecResult, ValidationError


@pytest.fixture
def apt():
    return get("apt")


def _good_plan():
    return {
        "operation": "install",
        "packages": ["tesseract-ocr"],
        "source": {
            "repo": "deb.debian.org/debian",
            "suite": "bookworm",
            "component": "main",
        },
        "flags": ["--no-install-recommends"],
    }


def _good_rollback():
    return {
        "operation": "purge",
        "packages": ["tesseract-ocr"],
        "flags": [],
    }


# --- happy path ---


def test_valid_plan_accepted(apt):
    p = apt.validate_plan(_good_plan(), _good_rollback())
    assert p.mechanism == "apt"
    assert p.plan_body["packages"] == ["tesseract-ocr"]


def test_executor_argv_shape(apt):
    plan = apt.validate_plan(_good_plan(), _good_rollback())
    ex = apt.build_executor(plan)
    # apt-get install -y --no-install-recommends tesseract-ocr
    assert ex.argv[0] == "apt-get"
    assert ex.argv[1] == "install"
    assert "tesseract-ocr" in ex.argv
    assert "-y" in ex.argv
    assert ex.env["DEBIAN_FRONTEND"] == "noninteractive"
    assert ex.timeout_seconds >= 60


def test_health_probe_uses_dpkg_query(apt):
    plan = apt.validate_plan(_good_plan(), _good_rollback())
    hc = apt.health_check_spec(plan)
    probe = apt.build_health_probe(hc)
    assert probe.argv[0] == "dpkg-query"
    assert "tesseract-ocr" in probe.argv


def test_health_check_passes_on_installed(apt):
    plan = apt.validate_plan(_good_plan(), _good_rollback())
    hc = apt.health_check_spec(plan)
    probe_result = ExecResult(
        exit_code=0,
        stdout=b"tesseract-ocr install ok installed\n",
        stderr=b"",
    )
    install_result = ExecResult(exit_code=0, stdout=b"", stderr=b"")
    assert apt.evaluate_health_check(hc, install_result, probe_result) is True


def test_health_check_fails_on_uninstalled(apt):
    plan = apt.validate_plan(_good_plan(), _good_rollback())
    hc = apt.health_check_spec(plan)
    probe_result = ExecResult(
        exit_code=1, stdout=b"", stderr=b"package not installed\n",
    )
    assert apt.evaluate_health_check(hc, ExecResult(0, b"", b""), probe_result) is False


def test_health_check_fails_on_partial(apt):
    plan_body = _good_plan()
    plan_body["packages"] = ["tesseract-ocr", "poppler-utils"]
    plan = apt.validate_plan(plan_body, {"operation": "purge", "packages": ["tesseract-ocr", "poppler-utils"]})
    hc = apt.health_check_spec(plan)
    probe_result = ExecResult(
        exit_code=0,
        stdout=b"tesseract-ocr install ok installed\npoppler-utils unknown ok not-installed\n",
        stderr=b"",
    )
    assert apt.evaluate_health_check(hc, ExecResult(0, b"", b""), probe_result) is False


# --- validation: bad inputs ---


def test_invalid_package_name_rejected(apt):
    bad = _good_plan()
    bad["packages"] = ["EvilName!"]
    with pytest.raises(ValidationError, match="invalid"):
        apt.validate_plan(bad, _good_rollback())


def test_forbidden_flag_rejected(apt):
    bad = _good_plan()
    bad["flags"] = ["--force-yes"]
    with pytest.raises(ValidationError, match="forbidden flag"):
        apt.validate_plan(bad, _good_rollback())


def test_unknown_flag_rejected(apt):
    bad = _good_plan()
    bad["flags"] = ["--evil"]
    with pytest.raises(ValidationError, match="not on allow-list"):
        apt.validate_plan(bad, _good_rollback())


def test_unknown_source_repo_rejected(apt):
    bad = _good_plan()
    bad["source"]["repo"] = "evil.example.com/debian"
    with pytest.raises(ValidationError, match="not on allow-list"):
        apt.validate_plan(bad, _good_rollback())


def test_unknown_suite_rejected(apt):
    bad = _good_plan()
    bad["source"]["suite"] = "weirdsuite"
    with pytest.raises(ValidationError, match="suite"):
        apt.validate_plan(bad, _good_rollback())


def test_rollback_must_be_inverse_op(apt):
    bad_rollback = _good_rollback()
    bad_rollback["operation"] = "install"
    with pytest.raises(ValidationError, match="inverse"):
        apt.validate_plan(_good_plan(), bad_rollback)


def test_rollback_packages_must_subset(apt):
    bad_rollback = _good_rollback()
    bad_rollback["packages"] = ["tesseract-ocr", "extra-not-in-plan"]
    with pytest.raises(ValidationError, match="subset"):
        apt.validate_plan(_good_plan(), bad_rollback)


def test_unknown_operation_rejected(apt):
    bad = _good_plan()
    bad["operation"] = "magic"
    with pytest.raises(ValidationError, match="operation"):
        apt.validate_plan(bad, _good_rollback())


def test_validate_rollback_re_runs(apt):
    plan = apt.validate_plan(_good_plan(), _good_rollback())
    rollback_validated = apt.validate_rollback(plan)
    assert rollback_validated.plan_body["operation"] == "purge"


def test_unknown_mechanism_lookup_raises():
    from demiurge.mechanisms import get

    with pytest.raises(KeyError, match="unknown mechanism"):
        get("conda")
