"""skills wrapper — create Gmail DRAFT reply via the Security Agent broker.

Draft only. Sending requires the operator to send the draft from Gmail's
Drafts folder. No `gmail.send` capability exists in v0.1.
"""

from agents.tool_factory import build_gmail_create_draft_tool

TOOL_METADATA = {
    "id": "gmail.create_draft",
    "version": "1.0.0",
    "scope": "shared",
    "safety_class": "read-write",  # creates a draft (modifies Gmail state, but reversible)
}


def build_tool():
    return build_gmail_create_draft_tool()
