"""WhatsApp Cloud API channel adapter.

Packaged as the ``demiurge-power-whatsapp-cloud`` plugin (v0.11).

Inbound: POST webhook from Meta (Graph API). Signature verified via the
Security Agent, then parsed into ``WhatsAppMessageEvent`` and published
to the bus.

Outbound: all requests (send_text, send_template, mark_read, get_media)
flow through the Security Agent; this adapter never holds the access
token.
"""

from __future__ import annotations


def manifest():
    from shared.plugins.discovery import load_manifest_for_package

    return load_manifest_for_package("whatsapp_cloud_adapter")
