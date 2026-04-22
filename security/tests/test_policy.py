"""Tests for the capability policy loader and evaluator."""

import pytest
import yaml

from stevens_security.policy import (
    AgentPolicy,
    CapabilityRule,
    Policy,
    PolicyError,
    evaluate,
    load_policy,
)


def make_policy(**agents) -> Policy:
    return Policy(agents=agents)


def test_default_deny_no_policy_at_all():
    p = Policy()
    d = evaluate(p, "email_pm", "gmail.read", {"account_id": "gmail.personal"})
    assert d.allow is False
    assert "no policy" in d.reason


def test_default_deny_for_unknown_caller():
    p = make_policy(
        email_pm=AgentPolicy(
            agent="email_pm",
            allow={"ping": CapabilityRule(name="ping")},
        )
    )
    d = evaluate(p, "ghost_agent", "ping", {})
    assert d.allow is False
    assert "no policy" in d.reason


def test_default_deny_for_unknown_capability():
    p = make_policy(
        email_pm=AgentPolicy(
            agent="email_pm",
            allow={"ping": CapabilityRule(name="ping")},
        )
    )
    d = evaluate(p, "email_pm", "gmail.send", {})
    assert d.allow is False
    assert "no rule matches" in d.reason


def test_allow_simple():
    p = make_policy(
        email_pm=AgentPolicy(
            agent="email_pm",
            allow={"ping": CapabilityRule(name="ping")},
        )
    )
    d = evaluate(p, "email_pm", "ping", {})
    assert d.allow is True
    assert d.rule is not None and d.rule.name == "ping"


def test_deny_overrides_allow():
    rule = CapabilityRule(name="gmail.send")
    p = make_policy(
        email_pm=AgentPolicy(
            agent="email_pm",
            allow={"gmail.send": rule},
            deny=frozenset({"gmail.send"}),
        )
    )
    d = evaluate(p, "email_pm", "gmail.send", {})
    assert d.allow is False
    assert d.reason == "explicitly denied"


def test_account_wildcard_match():
    rule = CapabilityRule(name="gmail.read", account_patterns=["gmail.*"])
    p = make_policy(
        email_pm=AgentPolicy(
            agent="email_pm",
            allow={"gmail.read": rule},
        )
    )
    d = evaluate(p, "email_pm", "gmail.read", {"account_id": "gmail.personal"})
    assert d.allow is True
    d2 = evaluate(p, "email_pm", "gmail.read", {"account_id": "gmail.atheer"})
    assert d2.allow is True


def test_account_wildcard_mismatch():
    rule = CapabilityRule(name="gmail.read", account_patterns=["gmail.*"])
    p = make_policy(
        email_pm=AgentPolicy(
            agent="email_pm",
            allow={"gmail.read": rule},
        )
    )
    d = evaluate(p, "email_pm", "gmail.read", {"account_id": "wa.main"})
    assert d.allow is False
    assert "out of scope" in d.reason


def test_account_scoped_rule_requires_account_id():
    rule = CapabilityRule(name="gmail.read", account_patterns=["gmail.*"])
    p = make_policy(
        email_pm=AgentPolicy(
            agent="email_pm",
            allow={"gmail.read": rule},
        )
    )
    d = evaluate(p, "email_pm", "gmail.read", {})
    assert d.allow is False
    assert "account_id" in d.reason


def test_unscoped_rule_ignores_account_id_param():
    # Capability without accounts: does not care about account_id.
    rule = CapabilityRule(name="anthropic.complete")
    p = make_policy(
        email_pm=AgentPolicy(
            agent="email_pm",
            allow={"anthropic.complete": rule},
        )
    )
    d = evaluate(p, "email_pm", "anthropic.complete", {"account_id": "whatever"})
    assert d.allow is True


def test_multiple_account_patterns():
    rule = CapabilityRule(
        name="notify.send",
        account_patterns=["wa.us", "wa.uae"],
    )
    p = make_policy(
        notifier=AgentPolicy(
            agent="notifier",
            allow={"notify.send": rule},
        )
    )
    assert evaluate(p, "notifier", "notify.send", {"account_id": "wa.us"}).allow is True
    assert evaluate(p, "notifier", "notify.send", {"account_id": "wa.uae"}).allow is True
    assert evaluate(p, "notifier", "notify.send", {"account_id": "wa.fr"}).allow is False


def test_rule_carries_constraints():
    rule = CapabilityRule(
        name="anthropic.complete",
        constraints={"max_tokens_per_day": 200000},
    )
    p = make_policy(
        email_pm=AgentPolicy(
            agent="email_pm",
            allow={"anthropic.complete": rule},
        )
    )
    d = evaluate(p, "email_pm", "anthropic.complete", {})
    assert d.allow is True
    assert d.rule is not None
    assert d.rule.constraints == {"max_tokens_per_day": 200000}


# --- Loader tests ---


def test_load_policy_missing_file(tmp_path):
    path = tmp_path / "nope.yaml"
    p = load_policy(path)
    assert p.agents == {}


def test_load_policy_empty_agents(tmp_path):
    path = tmp_path / "capabilities.yaml"
    path.write_text("agents: []\n")
    p = load_policy(path)
    assert p.agents == {}


def test_load_policy_full(tmp_path):
    path = tmp_path / "capabilities.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "agents": [
                    {
                        "name": "email_pm",
                        "allow": [
                            {"capability": "gmail.read", "accounts": ["gmail.*"]},
                            {
                                "capability": "anthropic.complete",
                                "constraints": {"max_tokens_per_day": 200000},
                            },
                        ],
                        "deny": ["gmail.send", "gmail.delete"],
                    }
                ]
            }
        )
    )
    p = load_policy(path)
    assert set(p.agents) == {"email_pm"}
    agent = p.agents["email_pm"]
    assert set(agent.allow) == {"gmail.read", "anthropic.complete"}
    assert agent.allow["gmail.read"].account_patterns == ["gmail.*"]
    assert agent.allow["anthropic.complete"].constraints == {"max_tokens_per_day": 200000}
    assert agent.deny == frozenset({"gmail.send", "gmail.delete"})


def test_load_policy_duplicate_agent_rejected(tmp_path):
    path = tmp_path / "capabilities.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "agents": [
                    {"name": "dup", "allow": []},
                    {"name": "dup", "allow": []},
                ]
            }
        )
    )
    with pytest.raises(PolicyError, match="duplicate"):
        load_policy(path)


def test_load_policy_duplicate_allow_entry_rejected(tmp_path):
    path = tmp_path / "capabilities.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "agents": [
                    {
                        "name": "email_pm",
                        "allow": [
                            {"capability": "gmail.read"},
                            {"capability": "gmail.read"},
                        ],
                    }
                ]
            }
        )
    )
    with pytest.raises(PolicyError, match="duplicate allow entry"):
        load_policy(path)


def test_load_policy_missing_capability_field(tmp_path):
    path = tmp_path / "capabilities.yaml"
    path.write_text(
        yaml.safe_dump(
            {"agents": [{"name": "email_pm", "allow": [{"accounts": ["gmail.*"]}]}]}
        )
    )
    with pytest.raises(PolicyError, match="missing 'capability'"):
        load_policy(path)


def test_load_policy_bad_yaml(tmp_path):
    path = tmp_path / "capabilities.yaml"
    path.write_text("agents: [this is: not valid yaml\n")
    with pytest.raises(PolicyError, match="invalid yaml"):
        load_policy(path)


def test_load_policy_wrong_toplevel_type(tmp_path):
    path = tmp_path / "capabilities.yaml"
    path.write_text("- just\n- a list\n")
    with pytest.raises(PolicyError, match="must be a map"):
        load_policy(path)


def test_load_policy_accounts_not_list(tmp_path):
    path = tmp_path / "capabilities.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "agents": [
                    {
                        "name": "email_pm",
                        "allow": [{"capability": "gmail.read", "accounts": "gmail.*"}],
                    }
                ]
            }
        )
    )
    with pytest.raises(PolicyError, match="'accounts' must be a list"):
        load_policy(path)
