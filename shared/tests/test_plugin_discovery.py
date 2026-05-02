"""Tests for shared.plugins.discovery — v0.11 step 2."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from shared.plugins import discovery
from shared.plugins.manifest import Manifest, load_manifest_from_text


# ----------------------------- fixtures + helpers ------------------------


GMAIL_POWER_YAML = """\
name: gmail
kind: power
display_name: Gmail
version: "1.0.0"
modes: [webhook, request-based]
runtime:
  webhook:
    path: /gmail/push
    port: 8080
    handler: demiurge_power_gmail.adapter:webhook_handler
capabilities: [gmail.send]
bootstrap: demiurge_power_gmail.bootstrap:install
"""


SIGNAL_POWER_YAML = """\
name: signal
kind: power
display_name: Signal
version: "0.5.0"
modes: [listener]
runtime:
  listener:
    command: demiurge_power_signal.adapter:run_listener
capabilities: [signal.send]
bootstrap: demiurge_power_signal.bootstrap:install
"""


EMAIL_PM_MORTAL_YAML = """\
name: email_pm
kind: mortal
display_name: Email PM
version: "1.0.0"
capabilities: [gmail.draft]
powers: [gmail]
bootstrap: demiurge_mortal_email_pm.bootstrap:hire
"""


def _gmail_manifest() -> Manifest:
    return load_manifest_from_text(GMAIL_POWER_YAML)


def _signal_manifest() -> Manifest:
    return load_manifest_from_text(SIGNAL_POWER_YAML)


def _email_pm_manifest() -> Manifest:
    return load_manifest_from_text(EMAIL_PM_MORTAL_YAML)


def _make_ep(
    name: str,
    value: str,
    target,
    dist_name: str = "demiurge-power-gmail",
    dist_version: str = "1.0.0",
):
    """Construct a fake EntryPoint that .load()s to ``target``.

    We don't try to instantiate a real ``importlib.metadata.EntryPoint`` —
    its constructor signature varies across Python versions. A duck-typed
    MagicMock is faithful enough for the discovery code, which only
    touches ``.name``, ``.value``, ``.load()``, and ``.dist``.
    """
    ep = MagicMock()
    ep.name = name
    ep.value = value
    ep.load.return_value = target
    if dist_name is None:
        ep.dist = None
    else:
        ep.dist = MagicMock()
        ep.dist.metadata = {"Name": dist_name}
        ep.dist.version = dist_version
    return ep


@pytest.fixture
def patched_eps(monkeypatch):
    """Fixture for installing a fake list of entry points per-group."""
    by_group: dict[str, list] = {}

    def fake_select(group: str):
        return by_group.get(group, [])

    monkeypatch.setattr(discovery, "_select_entry_points", fake_select)
    return by_group


# ----------------------------- happy paths -------------------------------


def test_discover_powers_with_manifest_callable(patched_eps):
    """Entry point loads to a callable that returns Manifest — the canonical shape."""
    patched_eps[discovery.POWERS_GROUP] = [
        _make_ep(
            name="gmail",
            value="demiurge_power_gmail:manifest",
            target=_gmail_manifest,  # the callable itself
            dist_name="demiurge-power-gmail",
            dist_version="1.0.0",
        )
    ]
    result = discovery.discover("power")
    assert result.errors == []
    assert len(result.plugins) == 1
    p = result.plugins[0]
    assert p.name == "gmail"
    assert p.kind == "power"
    assert p.manifest.name == "gmail"
    assert p.dist_name == "demiurge-power-gmail"
    assert p.dist_version == "1.0.0"
    assert p.entry_point_value == "demiurge_power_gmail:manifest"


def test_discover_powers_with_manifest_instance(patched_eps):
    """Entry point can also load to a bare ``Manifest`` instance."""
    patched_eps[discovery.POWERS_GROUP] = [
        _make_ep(
            name="gmail",
            value="demiurge_power_gmail:MANIFEST",
            target=_gmail_manifest(),  # already-resolved Manifest
        )
    ]
    result = discovery.discover("power")
    assert result.errors == []
    assert result.plugins[0].name == "gmail"


def test_discover_multiple_powers(patched_eps):
    patched_eps[discovery.POWERS_GROUP] = [
        _make_ep("gmail", "demiurge_power_gmail:manifest", _gmail_manifest),
        _make_ep(
            "signal",
            "demiurge_power_signal:manifest",
            _signal_manifest,
            dist_name="demiurge-power-signal",
            dist_version="0.5.0",
        ),
    ]
    result = discovery.discover("power")
    assert result.errors == []
    assert sorted(result.names()) == ["gmail", "signal"]


def test_discover_mortals_separate_group(patched_eps):
    patched_eps[discovery.POWERS_GROUP] = [
        _make_ep("gmail", "x:manifest", _gmail_manifest)
    ]
    patched_eps[discovery.MORTALS_GROUP] = [
        _make_ep(
            "email_pm",
            "demiurge_mortal_email_pm:manifest",
            _email_pm_manifest,
            dist_name="demiurge-mortal-email-pm",
            dist_version="1.0.0",
        )
    ]
    powers = discovery.discover("power")
    mortals = discovery.discover("mortal")
    assert powers.names() == ["gmail"]
    assert mortals.names() == ["email_pm"]
    assert mortals.plugins[0].kind == "mortal"


def test_discover_empty_group(patched_eps):
    """No plugins installed = empty result, not an error."""
    result = discovery.discover("power")
    assert result.plugins == []
    assert result.errors == []


def test_discover_unknown_kind_raises():
    with pytest.raises(ValueError, match="kind"):
        discovery._group_for_kind("god")  # type: ignore[arg-type]


# ----------------------------- error paths -------------------------------


def test_entry_point_load_raises_is_captured(patched_eps):
    """A broken import doesn't crash discovery — it goes in errors[]."""
    bad_ep = MagicMock()
    bad_ep.name = "broken"
    bad_ep.value = "broken_pkg:manifest"
    bad_ep.load.side_effect = ImportError("no such module 'broken_pkg'")
    bad_ep.dist = None
    patched_eps[discovery.POWERS_GROUP] = [bad_ep]

    result = discovery.discover("power")
    assert result.plugins == []
    assert len(result.errors) == 1
    err = result.errors[0]
    assert err.name == "broken"
    assert "ImportError" in err.error
    assert "no such module" in err.error


def test_entry_point_resolves_to_non_manifest(patched_eps):
    """Target is neither a Manifest nor a callable returning one."""
    patched_eps[discovery.POWERS_GROUP] = [
        _make_ep("gmail", "x:something", target="just a string")
    ]
    result = discovery.discover("power")
    assert result.plugins == []
    assert len(result.errors) == 1
    assert "expected Manifest" in result.errors[0].error


def test_callable_returning_wrong_type(patched_eps):
    patched_eps[discovery.POWERS_GROUP] = [
        _make_ep("gmail", "x:f", target=lambda: {"name": "gmail"})
    ]
    result = discovery.discover("power")
    assert result.plugins == []
    assert "expected Manifest" in result.errors[0].error


def test_kind_mismatch_is_error(patched_eps):
    """A Mortal manifest registered under powers group is an error."""
    patched_eps[discovery.POWERS_GROUP] = [
        _make_ep(
            "email_pm",
            "demiurge_mortal_email_pm:manifest",
            _email_pm_manifest,  # kind=mortal
        )
    ]
    result = discovery.discover("power")
    assert result.plugins == []
    assert len(result.errors) == 1
    assert "kind=" in result.errors[0].error


def test_entry_point_name_mismatch_is_error(patched_eps):
    """Entry-point key 'gmail' must match manifest's name field."""
    patched_eps[discovery.POWERS_GROUP] = [
        _make_ep(
            "gmail-typo",  # entry point says "gmail-typo"
            "demiurge_power_gmail:manifest",
            _gmail_manifest,  # manifest says "gmail"
        )
    ]
    result = discovery.discover("power")
    assert result.plugins == []
    assert "doesn't match manifest" in result.errors[0].error


def test_partial_failure_other_plugins_still_load(patched_eps):
    """Discovery is fault-tolerant: one broken plugin doesn't mask the others."""
    bad_ep = MagicMock()
    bad_ep.name = "broken"
    bad_ep.value = "broken_pkg:manifest"
    bad_ep.load.side_effect = RuntimeError("kaboom")
    bad_ep.dist = None
    good_ep = _make_ep("gmail", "x:manifest", _gmail_manifest)

    patched_eps[discovery.POWERS_GROUP] = [bad_ep, good_ep]
    result = discovery.discover("power")
    assert result.names() == ["gmail"]
    assert len(result.errors) == 1
    assert result.errors[0].name == "broken"


def test_dist_lookup_failure_yields_unknown_version(patched_eps):
    ep = _make_ep("gmail", "x:manifest", _gmail_manifest, dist_name=None)
    patched_eps[discovery.POWERS_GROUP] = [ep]
    result = discovery.discover("power")
    assert len(result.plugins) == 1
    assert result.plugins[0].dist_name == "<unknown>"
    assert result.plugins[0].dist_version == "unknown"


# ----------------------------- load_manifest_for_package -----------------


def test_load_manifest_for_package_reads_plugin_yaml(tmp_path: Path, monkeypatch):
    """Create a fake package layout with plugin.yaml and load it via importlib.resources."""
    pkg_dir = tmp_path / "fake_power"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    (pkg_dir / "plugin.yaml").write_text(GMAIL_POWER_YAML)
    monkeypatch.syspath_prepend(str(tmp_path))

    # Fresh import in case a previous test cached something.
    sys.modules.pop("fake_power", None)
    m = discovery.load_manifest_for_package("fake_power")
    assert m.name == "gmail"


def test_load_manifest_for_package_missing_yaml(tmp_path: Path, monkeypatch):
    """Package exists but ships no plugin.yaml."""
    pkg_dir = tmp_path / "no_yaml_pkg"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop("no_yaml_pkg", None)

    from shared.plugins.manifest import ManifestError

    with pytest.raises(ManifestError, match="no plugin.yaml"):
        discovery.load_manifest_for_package("no_yaml_pkg")


def test_load_manifest_for_package_unimportable():
    from shared.plugins.manifest import ManifestError

    with pytest.raises(ManifestError, match="not importable"):
        discovery.load_manifest_for_package("definitely_not_a_real_package_xyz_123")
