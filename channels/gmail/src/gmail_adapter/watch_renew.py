"""Gmail users.watch() renewal.

Google expires watches after 7 days. This script runs every 24 hours (via cron
or a loop in the adapter) and renews every active Gmail account's watch.

Implemented day 2.
"""

from __future__ import annotations


async def renew_all_watches() -> None:
    """Iterate active gmail accounts, call users.watch() for each, update metadata."""
    # TODO (day 2)
    ...
