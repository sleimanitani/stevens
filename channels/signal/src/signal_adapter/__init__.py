"""Signal channel adapter — talks to a signal-cli-rest-api daemon.

Architecture: the daemon (a separate Docker container running
``bbernhard/signal-cli-rest-api``) owns the linked Signal session;
we never touch Signal's encryption directly. We talk to it over
HTTP for inbound polling and outbound sends.

Maps inbound messages → ``SignalMessageEvent`` and ``ChannelRoute``
(channel_type='signal'). Implements the ``OutboundAdapter`` Protocol
from ``shared.channels`` so the core can synthesize content.
"""
