"""Google Calendar channel adapter.

Packaged as the ``demiurge-power-calendar`` plugin (v0.11).

Inbound: Google Calendar POSTs to ``/calendar/push`` whenever an event
changes on a watched calendar. The push body is empty — we get
``X-Goog-*`` headers and then pull changes via ``events.list`` with a
``syncToken``. Each resulting change is published as a
``CalendarEventChangedEvent``.

Outbound: every API call (watch, list, insert, patch, delete) flows
through the Security Agent. This adapter never holds an OAuth token.
"""

from __future__ import annotations


def manifest():
    """Entry-point target for ``demiurge.powers``."""
    from shared.plugins.discovery import load_manifest_for_package

    return load_manifest_for_package("calendar_adapter")
