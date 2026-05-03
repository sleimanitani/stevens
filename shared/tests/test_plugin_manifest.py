"""Tests for shared.plugins.manifest — v0.11 step 1."""

from __future__ import annotations

from pathlib import Path

import pytest

from shared.plugins.manifest import (
    Manifest,
    ManifestError,
    Mode,
    load_manifest_from_text,
    load_manifest_from_yaml,
)


# ----------------------------- valid manifests ---------------------------


GMAIL_POWER_YAML = """\
name: gmail
kind: power
display_name: Gmail
version: "1.0.0"
source: https://github.com/example/demiurge-power-gmail
maintainer: Sol
modes: [webhook, request-based]
runtime:
  webhook:
    path: /gmail/push
    port: 8080
    handler: demiurge_power_gmail.adapter:webhook_handler
capabilities:
  - gmail.send
  - gmail.read
  - gmail.draft
secrets:
  - name: gmail.oauth_client.id
    prompt: "Google OAuth client ID"
    onboard_via: "demiurge wizard google"
  - name: gmail.oauth_client.secret
    prompt: "Google OAuth client secret"
system_deps:
  apt: []
bootstrap: demiurge_power_gmail.bootstrap:install
"""


SIGNAL_LISTENER_YAML = """\
name: signal
kind: power
display_name: Signal
version: "0.5.0"
modes: [listener, request-based]
runtime:
  listener:
    command: demiurge_power_signal.adapter:run_listener
    restart: on-failure
capabilities:
  - signal.send
bootstrap: demiurge_power_signal.bootstrap:install
"""


RSS_POLLING_YAML = """\
name: rss_reader
kind: power
display_name: RSS Reader
version: "0.1.0"
modes: [polling]
runtime:
  polling:
    command: demiurge_power_rss.fetch:run_once
    interval: 1h
capabilities:
  - rss.subscribe
bootstrap: demiurge_power_rss.bootstrap:install
"""


IMAGEGEN_REQUEST_ONLY_YAML = """\
name: image_gen
kind: power
display_name: Image Generator
version: "1.0.0"
modes: [request-based]
capabilities:
  - image.generate
bootstrap: demiurge_power_imagegen.bootstrap:install
"""


EMAIL_PM_MORTAL_YAML = """\
name: email_pm
kind: mortal
display_name: Email PM
version: "1.0.0"
capabilities:
  - gmail.draft
  - gmail.label
powers:
  - gmail
bootstrap: demiurge_mortal_email_pm.bootstrap:hire
"""


def test_load_gmail_power():
    m = load_manifest_from_text(GMAIL_POWER_YAML)
    assert m.name == "gmail"
    assert m.kind == "power"
    assert Mode.WEBHOOK in m.modes
    assert Mode.REQUEST_BASED in m.modes
    assert m.runtime.webhook.port == 8080
    assert m.runtime.webhook.path == "/gmail/push"
    assert "gmail.send" in m.capabilities
    assert m.bootstrap == "demiurge_power_gmail.bootstrap:install"
    assert len(m.secrets) == 2
    assert m.secrets[0].onboard_via == "demiurge wizard google"


def test_load_signal_listener():
    m = load_manifest_from_text(SIGNAL_LISTENER_YAML)
    assert Mode.LISTENER in m.modes
    assert m.runtime.listener.command == "demiurge_power_signal.adapter:run_listener"
    assert m.runtime.listener.restart == "on-failure"


def test_load_rss_polling():
    m = load_manifest_from_text(RSS_POLLING_YAML)
    assert m.modes == [Mode.POLLING]
    assert m.runtime.polling.interval == "1h"


def test_load_request_based_only_no_runtime():
    m = load_manifest_from_text(IMAGEGEN_REQUEST_ONLY_YAML)
    assert m.modes == [Mode.REQUEST_BASED]
    # request-based-only: runtime block was not declared and is None.
    assert m.runtime is None


def test_load_email_pm_mortal():
    m = load_manifest_from_text(EMAIL_PM_MORTAL_YAML)
    assert m.kind == "mortal"
    assert m.modes is None
    assert m.runtime is None
    assert m.powers == ["gmail"]
    assert m.bootstrap == "demiurge_mortal_email_pm.bootstrap:hire"


# ----------------------------- invalid: kind / modes ---------------------


def test_power_without_modes_fails():
    yaml_text = """\
name: x
kind: power
display_name: X
version: "1.0"
capabilities: []
bootstrap: x.bootstrap:install
"""
    with pytest.raises(ManifestError, match="modes"):
        load_manifest_from_text(yaml_text)


def test_mortal_with_modes_fails():
    yaml_text = """\
name: x
kind: mortal
display_name: X
version: "1.0"
modes: [webhook]
runtime:
  webhook:
    path: /x
    port: 8080
    handler: x.h:h
capabilities: []
bootstrap: x:hire
"""
    with pytest.raises(ManifestError, match=r"(mortal|Creature).*modes"):
        load_manifest_from_text(yaml_text)


def test_beast_kind_accepted():
    """v0.11 step 3e.1 extended the kind literal to include 'beast'."""
    yaml_text = """\
name: image_gen
kind: beast
display_name: Image Generator
version: "1.0"
capabilities:
  - image.generate
"""
    m = load_manifest_from_text(yaml_text)
    assert m.kind == "beast"
    assert m.modes is None


def test_automaton_kind_accepted():
    """v0.11 step 3e.1 extended the kind literal to include 'automaton'."""
    yaml_text = """\
name: scheduler
kind: automaton
display_name: Scheduler
version: "1.0"
capabilities: []
"""
    m = load_manifest_from_text(yaml_text)
    assert m.kind == "automaton"
    assert m.modes is None


def test_beast_with_modes_fails():
    yaml_text = """\
name: x
kind: beast
display_name: X
version: "1.0"
modes: [webhook]
runtime:
  webhook:
    path: /x
    port: 8080
    handler: x.h:h
capabilities: []
"""
    with pytest.raises(ManifestError, match=r"(beast|Creature).*modes"):
        load_manifest_from_text(yaml_text)


def test_unknown_mode_fails():
    yaml_text = """\
name: x
kind: power
display_name: X
version: "1.0"
modes: [psychic]
capabilities: []
bootstrap: x:install
"""
    with pytest.raises(ManifestError):
        load_manifest_from_text(yaml_text)


def test_duplicate_modes_fails():
    yaml_text = """\
name: x
kind: power
display_name: X
version: "1.0"
modes: [webhook, webhook]
runtime:
  webhook:
    path: /x
    port: 8080
    handler: x.h:h
capabilities: []
bootstrap: x:install
"""
    with pytest.raises(ManifestError, match="duplicate"):
        load_manifest_from_text(yaml_text)


# ----------------------------- invalid: runtime ↔ modes mismatch ---------


def test_declared_webhook_without_runtime_block_fails():
    yaml_text = """\
name: x
kind: power
display_name: X
version: "1.0"
modes: [webhook]
capabilities: []
bootstrap: x:install
"""
    with pytest.raises(ManifestError, match="webhook.*runtime"):
        load_manifest_from_text(yaml_text)


def test_runtime_block_without_declared_mode_fails():
    yaml_text = """\
name: x
kind: power
display_name: X
version: "1.0"
modes: [request-based]
runtime:
  webhook:
    path: /x
    port: 8080
    handler: x.h:h
capabilities: []
bootstrap: x:install
"""
    with pytest.raises(ManifestError, match="webhook.*declared"):
        load_manifest_from_text(yaml_text)


def test_request_based_only_with_runtime_block_fails():
    """A request-based-only power must not have any runtime block."""
    yaml_text = """\
name: x
kind: power
display_name: X
version: "1.0"
modes: [request-based]
runtime:
  polling:
    command: x.fetch:run_once
    interval: 1h
capabilities: []
bootstrap: x:install
"""
    with pytest.raises(ManifestError):
        load_manifest_from_text(yaml_text)


# ----------------------------- invalid: name / capabilities --------------


def test_uppercase_name_fails():
    yaml_text = """\
name: Gmail
kind: power
display_name: Gmail
version: "1.0"
modes: [request-based]
capabilities: []
bootstrap: x:install
"""
    with pytest.raises(ManifestError, match="name"):
        load_manifest_from_text(yaml_text)


def test_capability_typo_fails():
    yaml_text = """\
name: x
kind: power
display_name: X
version: "1.0"
modes: [request-based]
capabilities:
  - "Gmail-Send"
bootstrap: x:install
"""
    with pytest.raises(ManifestError, match="capability"):
        load_manifest_from_text(yaml_text)


def test_secret_name_invalid_shape_fails():
    yaml_text = """\
name: x
kind: power
display_name: X
version: "1.0"
modes: [request-based]
capabilities: []
secrets:
  - name: NotADottedThing
    prompt: nope
bootstrap: x:install
"""
    with pytest.raises(ManifestError, match="secret name"):
        load_manifest_from_text(yaml_text)


# ----------------------------- invalid: powers field ---------------------


def test_powers_field_on_power_fails():
    yaml_text = """\
name: x
kind: power
display_name: X
version: "1.0"
modes: [request-based]
capabilities: []
powers: [other_power]
bootstrap: x:install
"""
    with pytest.raises(ManifestError, match="powers.*Creature-only"):
        load_manifest_from_text(yaml_text)


def test_power_without_bootstrap_fails():
    yaml_text = """\
name: x
kind: power
display_name: X
version: "1.0"
modes: [request-based]
capabilities: []
"""
    with pytest.raises(ManifestError, match="bootstrap"):
        load_manifest_from_text(yaml_text)


# ----------------------------- file loader / parse errors ----------------


def test_load_from_yaml_file(tmp_path: Path):
    p = tmp_path / "plugin.yaml"
    p.write_text(GMAIL_POWER_YAML)
    m = load_manifest_from_yaml(p)
    assert m.name == "gmail"


def test_load_from_yaml_missing_file(tmp_path: Path):
    with pytest.raises(ManifestError, match="not found"):
        load_manifest_from_yaml(tmp_path / "nope.yaml")


def test_load_invalid_yaml_text():
    with pytest.raises(ManifestError, match="YAML parse failed"):
        load_manifest_from_text("name: gmail\n  bad: indentation: here")


def test_load_yaml_root_must_be_mapping():
    with pytest.raises(ManifestError, match="must be a mapping"):
        load_manifest_from_text("- just\n- a\n- list")


def test_extra_unknown_top_level_field_fails():
    """Manifest is strict — no unknown top-level fields. Catches typos."""
    yaml_text = """\
name: x
kind: power
display_name: X
version: "1.0"
modes: [request-based]
capabilities: []
bootstrap: x:install
unknown_field: surprise
"""
    with pytest.raises(ManifestError):
        load_manifest_from_text(yaml_text)
