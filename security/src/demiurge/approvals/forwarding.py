"""Approval-forwarding config + matcher.

When a per-call approval lands, the dispatcher publishes one
``ApprovalRequestedEvent`` per matching forwarding target. Targets are
declared in ``security/policy/approval_forwarding.yaml`` with an
OpenClaw-shaped schema (see the YAML's header for the contract).

This module is a pure config loader + matcher; the actual event
publication lives in the dispatcher.
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .queue import ApprovalRequest


class ForwardingConfigError(Exception):
    """Raised on malformed approval_forwarding.yaml."""


_VALID_MODES = {"session", "targets", "both"}


@dataclass(frozen=True)
class ForwardingTarget:
    channel: str
    account_id: str
    thread_id: Optional[str] = None


@dataclass(frozen=True)
class ForwardingRule:
    mode: str
    targets: List[ForwardingTarget] = field(default_factory=list)
    agent_filter: List[str] = field(default_factory=list)
    capability_filter: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class ForwardingConfig:
    rules: List[ForwardingRule] = field(default_factory=list)


def _default_config_path() -> Path:
    env = os.environ.get("DEMIURGE_APPROVAL_FORWARDING")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "policy" / "approval_forwarding.yaml"


def load_config(path: Optional[Path] = None) -> ForwardingConfig:
    """Load and validate the forwarding config. Missing file → empty config."""
    p = path or _default_config_path()
    if not p.exists():
        return ForwardingConfig()
    try:
        raw = yaml.safe_load(p.read_text()) or {}
    except yaml.YAMLError as e:
        raise ForwardingConfigError(f"{p}: invalid yaml: {e}") from e
    if not isinstance(raw, dict):
        raise ForwardingConfigError(f"{p}: top-level must be a mapping")
    rules_raw = raw.get("rules") or []
    if not isinstance(rules_raw, list):
        raise ForwardingConfigError(f"{p}: 'rules' must be a list")
    rules: List[ForwardingRule] = []
    for entry in rules_raw:
        if not isinstance(entry, dict):
            raise ForwardingConfigError(f"{p}: rule must be a mapping; got {entry!r}")
        mode = entry.get("mode")
        if mode not in _VALID_MODES:
            raise ForwardingConfigError(
                f"{p}: rule mode must be one of {sorted(_VALID_MODES)}; got {mode!r}"
            )
        targets_raw = entry.get("targets") or []
        if not isinstance(targets_raw, list):
            raise ForwardingConfigError(f"{p}: rule.targets must be a list")
        targets: List[ForwardingTarget] = []
        for t in targets_raw:
            if not isinstance(t, dict):
                raise ForwardingConfigError(f"{p}: target must be a mapping; got {t!r}")
            ch = t.get("channel")
            ac = t.get("account_id")
            if not isinstance(ch, str) or not isinstance(ac, str):
                raise ForwardingConfigError(
                    f"{p}: target requires channel + account_id strings"
                )
            targets.append(ForwardingTarget(
                channel=ch, account_id=ac,
                thread_id=t.get("thread_id") if isinstance(t.get("thread_id"), str) else None,
            ))
        if mode in ("targets", "both") and not targets:
            raise ForwardingConfigError(
                f"{p}: rule mode={mode!r} requires non-empty targets"
            )
        agent_filter = entry.get("agent_filter") or []
        if not isinstance(agent_filter, list) or not all(isinstance(x, str) for x in agent_filter):
            raise ForwardingConfigError(f"{p}: agent_filter must be a list of strings")
        capability_filter = entry.get("capability_filter") or []
        if not isinstance(capability_filter, list) or not all(isinstance(x, str) for x in capability_filter):
            raise ForwardingConfigError(f"{p}: capability_filter must be a list of strings")
        rules.append(ForwardingRule(
            mode=mode,
            targets=targets,
            agent_filter=list(agent_filter),
            capability_filter=list(capability_filter),
        ))
    return ForwardingConfig(rules=rules)


def matching_targets(
    config: ForwardingConfig,
    request: ApprovalRequest,
    *,
    origin_channel: Optional[str] = None,
    origin_account_id: Optional[str] = None,
    origin_thread_id: Optional[str] = None,
) -> List[ForwardingTarget]:
    """Return the list of forwarding targets for ``request``.

    ``session`` mode requires the dispatcher to know the origin channel /
    account / thread of the call; if not supplied, session-mode rules
    contribute nothing (the call wasn't from a chat channel — likely from
    the bus or from a CLI-triggered event).
    """
    out: List[ForwardingTarget] = []
    for rule in config.rules:
        if rule.agent_filter and request.caller not in rule.agent_filter:
            continue
        if rule.capability_filter and not any(
            fnmatch.fnmatchcase(request.capability, p) for p in rule.capability_filter
        ):
            continue
        if rule.mode in ("session", "both") and origin_channel and origin_account_id:
            out.append(ForwardingTarget(
                channel=origin_channel,
                account_id=origin_account_id,
                thread_id=origin_thread_id,
            ))
        if rule.mode in ("targets", "both"):
            out.extend(rule.targets)
    return out
