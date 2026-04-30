"""skills wrapper — add a label to a Gmail thread."""

from agents.tool_factory import build_gmail_add_label_tool

TOOL_METADATA = {
    "id": "gmail.add_label",
    "version": "1.0.0",
    "scope": "shared",
    "safety_class": "read-write",
}


def build_tool():
    return build_gmail_add_label_tool()
