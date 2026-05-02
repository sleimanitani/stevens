"""Policy presets — opinionated allow-rule bundles for `stevens agent provision`.

A preset is a YAML file at ``security/policy/presets/<name>.yaml`` whose
top-level shape is just::

    allow:
      - capability: gmail.search
        accounts: ["gmail.*"]
      - ...

The merger composes one of these into ``capabilities.yaml`` under a given
agent name. Idempotent: re-merging the same preset for the same agent is
a no-op. If the agent already has different rules under those capabilities,
the merger refuses to clobber and raises a clear error — the operator
edits the YAML by hand or removes the entry first.

This module is intentionally narrow: it does not evaluate policy, only
compose it. Evaluation lives in ``policy.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


class PresetError(Exception):
    """Raised on missing/malformed preset files or merge conflicts."""


@dataclass(frozen=True)
class PresetRule:
    capability: str
    accounts: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class Preset:
    name: str
    allow: List[PresetRule] = field(default_factory=list)


def _presets_dir() -> Path:
    """Default presets directory at ``security/policy/presets/``.

    Resolution: walks up from this file (``…/src/demiurge/presets.py``)
    to the ``security/`` package root, then ``policy/presets``. Override
    with ``$STEVENS_SECURITY_PRESETS`` for tests.
    """
    import os

    env = os.environ.get("STEVENS_SECURITY_PRESETS")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "policy" / "presets"


def list_presets(presets_dir: Optional[Path] = None) -> List[str]:
    """Return preset names (without ``.yaml`` suffix) found on disk."""
    d = presets_dir or _presets_dir()
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.yaml"))


def load_preset(name: str, presets_dir: Optional[Path] = None) -> Preset:
    """Load a preset by name. Raises ``PresetError`` if missing or malformed."""
    d = presets_dir or _presets_dir()
    path = d / f"{name}.yaml"
    if not path.exists():
        available = list_presets(d)
        raise PresetError(
            f"unknown preset {name!r} (looked in {d}); available: {available}"
        )
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise PresetError(f"invalid yaml in {path}: {e}") from e
    if data is None:
        return Preset(name=name)
    if not isinstance(data, dict):
        raise PresetError(f"top-level of {path} must be a map")
    allow_raw = data.get("allow") or []
    if not isinstance(allow_raw, list):
        raise PresetError(f"'allow' in {path} must be a list")
    rules: List[PresetRule] = []
    for entry in allow_raw:
        if not isinstance(entry, dict):
            raise PresetError(f"allow entry must be a map: {entry!r}")
        cap = entry.get("capability")
        if not isinstance(cap, str) or not cap:
            raise PresetError(f"allow entry missing 'capability' string: {entry!r}")
        accounts = entry.get("accounts") or []
        if not isinstance(accounts, list) or not all(isinstance(x, str) for x in accounts):
            raise PresetError(
                f"allow entry 'accounts' must be a list of strings: {entry!r}"
            )
        rules.append(PresetRule(capability=cap, accounts=list(accounts)))
    return Preset(name=name, allow=rules)


def _rule_to_dict(rule: PresetRule) -> Dict[str, Any]:
    out: Dict[str, Any] = {"capability": rule.capability}
    if rule.accounts:
        out["accounts"] = list(rule.accounts)
    return out


def _existing_rules_match(existing: List[Dict[str, Any]], preset: Preset) -> bool:
    """True if ``existing`` (raw allow list from yaml) matches the preset exactly."""
    if len(existing) != len(preset.allow):
        return False
    for got, want in zip(existing, preset.allow):
        if got.get("capability") != want.capability:
            return False
        got_accounts = got.get("accounts") or []
        if list(got_accounts) != list(want.accounts):
            return False
    return True


def merge_into_capabilities(
    capabilities_yaml: Path,
    agent_name: str,
    preset: Preset,
) -> bool:
    """Merge the preset under ``agent_name`` in ``capabilities.yaml``.

    Returns True if the file was modified, False if it was already up to date.

    Behavior:
    - If the file does not exist, creates it with a top-level ``agents: []``.
    - If ``agent_name`` is not in the agents list, appends a new entry with
      the preset's allow rules.
    - If ``agent_name`` exists with **identical** allow rules, no-op.
    - If ``agent_name`` exists with **different** rules, raises
      ``PresetError`` — the operator must reconcile by hand. (We don't
      auto-merge: every allow rule has security implications and silent
      composition is exactly the kind of thing this whole architecture
      is meant to prevent.)
    """
    if capabilities_yaml.exists():
        raw = yaml.safe_load(capabilities_yaml.read_text()) or {}
        if not isinstance(raw, dict):
            raise PresetError(
                f"top-level of {capabilities_yaml} must be a map (got {type(raw).__name__})"
            )
    else:
        raw = {}

    agents_list = raw.get("agents")
    if agents_list is None:
        agents_list = []
    if not isinstance(agents_list, list):
        raise PresetError(f"'agents' in {capabilities_yaml} must be a list")

    existing_idx = next(
        (
            i
            for i, e in enumerate(agents_list)
            if isinstance(e, dict) and e.get("name") == agent_name
        ),
        None,
    )

    new_allow = [_rule_to_dict(r) for r in preset.allow]

    if existing_idx is not None:
        existing_entry = agents_list[existing_idx]
        existing_allow = existing_entry.get("allow") or []
        if not isinstance(existing_allow, list):
            raise PresetError(
                f"agent {agent_name!r} 'allow' must be a list in {capabilities_yaml}"
            )
        if _existing_rules_match(existing_allow, preset):
            return False  # idempotent no-op
        raise PresetError(
            f"agent {agent_name!r} already has different allow rules in "
            f"{capabilities_yaml}; refusing to clobber. Remove the entry by hand "
            f"or pick a different agent name."
        )

    agents_list.append({"name": agent_name, "allow": new_allow})
    raw["agents"] = agents_list
    capabilities_yaml.parent.mkdir(parents=True, exist_ok=True)
    capabilities_yaml.write_text(yaml.safe_dump(raw, sort_keys=False))
    return True
