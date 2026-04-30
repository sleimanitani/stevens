"""skills wrapper — list overdue followups."""

from agents.email_pm.tools import build_list_overdue_followups_tool

TOOL_METADATA = {
    "id": "followups.list_overdue",
    "version": "1.0.0",
    "scope": "shared",
    "safety_class": "read-only",
}


def build_tool():
    return build_list_overdue_followups_tool()
