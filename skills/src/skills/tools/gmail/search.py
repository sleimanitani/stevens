"""skills wrapper — Gmail search via the Security Agent broker."""

from agents.tool_factory import build_gmail_search_tool

TOOL_METADATA = {
    "id": "gmail.search",
    "version": "1.0.0",
    "scope": "shared",
    "safety_class": "read-only",
}


def build_tool():
    return build_gmail_search_tool()
