"""Capability policy loader and evaluator.

The policy answers: "is agent X allowed to invoke capability Y with these
params?" Default deny — every effective allow must be explicit.

The policy is a yaml file at ``security/policy/capabilities.yaml`` (see the
file for its schema). Loaded once at server start; reloading is not
supported in v1 (restart the Security Agent to change policy — the blast
radius of dynamic reload on a broker like this isn't worth it).

Evaluation ordering:

1. If the caller has no entry in the policy → DENY ``"no policy for caller"``.
2. If the capability appears in that caller's ``deny:`` list → DENY
   ``"explicitly denied"``.  Deny-overrides-allow is deliberate belt-and-
   suspenders.
3. If the capability is in ``allow:``:
   a. If the rule declares ``accounts:``, the request's ``params`` must
      carry ``account_id`` and it must match one of the declared patterns
      (``fnmatch`` glob — ``gmail.*`` matches ``gmail.personal``).
      Missing / mismatched → DENY.
   b. Otherwise → ALLOW, returning the rule so downstream (rate limits,
      constraints) can use it.
4. Else → DENY ``"no rule matches"``.

Rate-limit and budget fields in ``constraints`` are parsed but NOT
enforced here — enforcement lives in the capability registry once real
capabilities exist (step 6).
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


class PolicyError(Exception):
    """Raised on malformed policy yaml."""


@dataclass(frozen=True)
class CapabilityRule:
    name: str
    account_patterns: List[str] = field(default_factory=list)
    constraints: Dict[str, Any] = field(default_factory=dict)
    requires_approval: bool = False
    rationale_required: bool = False

    @property
    def is_account_scoped(self) -> bool:
        return bool(self.account_patterns)


@dataclass(frozen=True)
class AgentPolicy:
    agent: str
    allow: Dict[str, CapabilityRule] = field(default_factory=dict)
    deny: frozenset = field(default_factory=frozenset)


@dataclass(frozen=True)
class Policy:
    agents: Dict[str, AgentPolicy] = field(default_factory=dict)


@dataclass(frozen=True)
class PolicyDecision:
    allow: bool
    reason: str
    rule: Optional[CapabilityRule] = None
    # Set when the matched rule has ``requires_approval: true``. The dispatcher
    # consults the standing-approval matcher next; on miss, it enqueues a
    # per-call request and returns BLOCKED to the caller.
    requires_approval: bool = False
    rationale_required: bool = False


def _parse_rule(raw: Dict[str, Any]) -> CapabilityRule:
    if "capability" not in raw:
        raise PolicyError(f"allow entry missing 'capability': {raw!r}")
    name = raw["capability"]
    if not isinstance(name, str):
        raise PolicyError(f"allow entry 'capability' must be str, got {type(name).__name__}")
    accounts = raw.get("accounts", [])
    if accounts is None:
        accounts = []
    if not isinstance(accounts, list) or not all(isinstance(x, str) for x in accounts):
        raise PolicyError(f"allow entry 'accounts' must be a list of strings: {raw!r}")
    constraints = raw.get("constraints", {}) or {}
    if not isinstance(constraints, dict):
        raise PolicyError(f"allow entry 'constraints' must be a map: {raw!r}")
    requires_approval = bool(raw.get("requires_approval", False))
    rationale_required = bool(raw.get("rationale_required", False))
    return CapabilityRule(
        name=name,
        account_patterns=list(accounts),
        constraints=dict(constraints),
        requires_approval=requires_approval,
        rationale_required=rationale_required,
    )


def _parse_agent(raw: Dict[str, Any]) -> AgentPolicy:
    if "name" not in raw:
        raise PolicyError(f"agent entry missing 'name': {raw!r}")
    agent = raw["name"]
    if not isinstance(agent, str):
        raise PolicyError(f"agent 'name' must be str, got {type(agent).__name__}")

    allow_raw = raw.get("allow") or []
    if not isinstance(allow_raw, list):
        raise PolicyError(f"agent {agent!r} 'allow' must be a list")
    allow: Dict[str, CapabilityRule] = {}
    for entry in allow_raw:
        rule = _parse_rule(entry)
        if rule.name in allow:
            raise PolicyError(
                f"agent {agent!r} has duplicate allow entry for {rule.name}"
            )
        allow[rule.name] = rule

    deny_raw = raw.get("deny") or []
    if not isinstance(deny_raw, list) or not all(isinstance(x, str) for x in deny_raw):
        raise PolicyError(f"agent {agent!r} 'deny' must be a list of strings")

    return AgentPolicy(agent=agent, allow=allow, deny=frozenset(deny_raw))


def load_policy(path: Path) -> Policy:
    """Load ``capabilities.yaml`` into a :class:`Policy`.

    Missing file → empty policy (fail-closed: nothing is allowed).
    Malformed file → :class:`PolicyError`.
    """
    if not path.exists():
        return Policy()
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise PolicyError(f"invalid yaml in {path}: {e}") from e
    if data is None:
        return Policy()
    if not isinstance(data, dict):
        raise PolicyError(f"top-level of {path} must be a map")
    agents_raw = data.get("agents") or []
    if not isinstance(agents_raw, list):
        raise PolicyError(f"'agents' in {path} must be a list")

    agents: Dict[str, AgentPolicy] = {}
    for entry in agents_raw:
        if not isinstance(entry, dict):
            raise PolicyError(f"agent entry must be a map: {entry!r}")
        policy_entry = _parse_agent(entry)
        if policy_entry.agent in agents:
            raise PolicyError(f"duplicate agent entry: {policy_entry.agent}")
        agents[policy_entry.agent] = policy_entry
    return Policy(agents=agents)


def evaluate(
    policy: Policy,
    caller: str,
    capability: str,
    params: Dict[str, Any],
) -> PolicyDecision:
    """Return the policy decision for (caller, capability, params)."""
    agent = policy.agents.get(caller)
    if agent is None:
        return PolicyDecision(allow=False, reason="no policy for caller")

    if capability in agent.deny:
        return PolicyDecision(allow=False, reason="explicitly denied")

    rule = agent.allow.get(capability)
    if rule is None:
        return PolicyDecision(allow=False, reason="no rule matches")

    if rule.is_account_scoped:
        account_id = params.get("account_id")
        if not isinstance(account_id, str) or not account_id:
            return PolicyDecision(
                allow=False,
                reason="rule requires account_id but none provided",
                rule=rule,
            )
        if not any(fnmatch.fnmatchcase(account_id, pat) for pat in rule.account_patterns):
            return PolicyDecision(
                allow=False,
                reason=f"account_id {account_id!r} out of scope",
                rule=rule,
            )

    return PolicyDecision(
        allow=True,
        reason="ok",
        rule=rule,
        requires_approval=rule.requires_approval,
        rationale_required=rule.rationale_required,
    )
