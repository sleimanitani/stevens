"""Google Calendar channel adapter.

Inbound: Google Calendar POSTs to ``/calendar/push`` whenever an event
changes on a watched calendar. The push body is empty — we get
``X-Goog-*`` headers and then pull changes via ``events.list`` with a
``syncToken``. Each resulting change is published as a
``CalendarEventChangedEvent``.

Outbound: every API call (watch, list, insert, patch, delete) flows
through the Security Agent. This adapter never holds an OAuth token.
"""
