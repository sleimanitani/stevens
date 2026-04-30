"""skills wrapper — fetch full Gmail thread via the Security Agent broker."""

from agents.tool_factory import build_gmail_get_thread_tool

TOOL_METADATA = {
    "id": "gmail.get_thread",
    "version": "1.0.0",
    "scope": "shared",
    "safety_class": "read-only",
}


def build_tool():
    return build_gmail_get_thread_tool()
