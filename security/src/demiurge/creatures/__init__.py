"""Concrete Creatures shipped by Demiurge core.

v0.11 introduces the first one — the **scheduler Automaton** —
proving the Automaton kind end-to-end. Other Creatures (Mortals like
email_pm + installer; Beasts like the future image_gen) ship as
v0.11 plugins under `plugins/` rather than living in core.

Module map:

- ``scheduler`` — the Scheduler Automaton: holds a subscription
  registry of (creature_id, interval), fires ``creature.tick.<id>``
  events when intervals elapse.
"""

from .scheduler import (  # noqa: F401
    Scheduler,
    Subscription,
    parse_interval,
)
