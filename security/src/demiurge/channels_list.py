"""``demiurge channels list`` — discover what channels you can onboard.

Hardcoded registry of shipped + planned channels. Adding a new channel
is one entry; nothing dynamic to wire up.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class ChannelEntry:
    name: str                  # display name
    code_id: str               # what `--channel` / `channel_type` columns expect
    status: str                # shipped | stub | planned
    runbook: str               # path under docs/runbooks/
    onboard_hint: str          # one-line "to onboard, run X"


_CHANNELS: List[ChannelEntry] = [
    ChannelEntry(
        name="Gmail",
        code_id="gmail",
        status="shipped",
        runbook="docs/runbooks/gmail.md",
        onboard_hint="demiurge wizard google → janus run google_oauth_client → onboard gmail",
    ),
    ChannelEntry(
        name="Google Calendar",
        code_id="calendar",
        status="shipped",
        runbook="docs/runbooks/calendar.md",
        onboard_hint="reuse Gmail's OAuth client OR run a separate wizard pass; then python -m calendar_adapter.add_account",
    ),
    ChannelEntry(
        name="WhatsApp Cloud (business)",
        code_id="whatsapp_cloud",
        status="shipped",
        runbook="docs/runbooks/whatsapp-cloud.md",
        onboard_hint="generate a Meta System User token, then demiurge onboard whatsapp_cloud --app-secret-stdin",
    ),
    ChannelEntry(
        name="Signal",
        code_id="signal",
        status="shipped",
        runbook="docs/runbooks/signal.md",
        onboard_hint="docker compose up signal-cli-rest-api, then python -m signal_adapter.add_account, scan QR with phone",
    ),
    ChannelEntry(
        name="WhatsApp personal (Baileys)",
        code_id="whatsapp",
        status="stub",
        runbook="(no runbook yet)",
        onboard_hint="adapter not built — Baileys integration is queued for a future milestone",
    ),
    ChannelEntry(
        name="Slack",
        code_id="slack",
        status="planned",
        runbook="(no runbook yet)",
        onboard_hint="framework ready (v0.4.1); per-channel adapter queued",
    ),
    ChannelEntry(
        name="Discord",
        code_id="discord",
        status="planned",
        runbook="(no runbook yet)",
        onboard_hint="framework ready (v0.4.1); per-channel adapter queued",
    ),
    ChannelEntry(
        name="Telegram",
        code_id="telegram",
        status="planned",
        runbook="(no runbook yet)",
        onboard_hint="framework ready (v0.4.1); per-channel adapter queued",
    ),
    ChannelEntry(
        name="iMessage (via BlueBubbles)",
        code_id="imessage",
        status="planned",
        runbook="(no runbook yet)",
        onboard_hint="framework ready (v0.4.1); per-channel adapter queued",
    ),
]


def all_channels() -> List[ChannelEntry]:
    return list(_CHANNELS)


def render() -> str:
    """Format the channel list for `demiurge channels list` stdout."""
    lines = ["Channels — what's shipped vs planned:\n"]
    by_status: dict = {}
    for c in _CHANNELS:
        by_status.setdefault(c.status, []).append(c)
    for status in ("shipped", "stub", "planned"):
        rows = by_status.get(status) or []
        if not rows:
            continue
        lines.append(f"## {status}")
        for c in rows:
            lines.append(f"  {c.name}  ({c.code_id})")
            lines.append(f"    runbook: {c.runbook}")
            lines.append(f"    onboard: {c.onboard_hint}")
        lines.append("")
    lines.append("Master flow: docs/runbooks/README.md")
    return "\n".join(lines)
