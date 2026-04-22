"""WhatsApp Cloud API channel adapter.

Inbound: POST webhook from Meta (Graph API). Signature verified via the
Security Agent, then parsed into ``WhatsAppMessageEvent`` and published
to the bus.

Outbound: all requests (send_text, send_template, mark_read, get_media)
flow through the Security Agent; this adapter never holds the access
token.
"""
