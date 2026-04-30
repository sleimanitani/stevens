"""skills wrapper — record a followup row in the followups table.

Restricted: Email PM and subject agents care about followups; the Security
Agent and the future interface agent don't. Add agent names to
``allowed_agents`` in registry.yaml as new consumers come online.
"""

from agents.email_pm.tools import build_log_followup_tool

TOOL_METADATA = {
    "id": "followups.log_followup",
    "version": "1.0.0",
    "scope": "restricted",
    "safety_class": "read-write",
}


def build_tool():
    return build_log_followup_tool()
