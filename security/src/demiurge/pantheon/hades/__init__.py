"""Hades — destroyer + archivist of Creatures (v0.11).

Mirror image of Hephaestus. Where Hephaestus mints identity, writes
policy, generates runtime artifacts and observation feeds, Hades
revokes identity, removes policy, tears down runtime, archives the
observation feed, and renames/drops the per-Creature Postgres schema.

Public surface:

- ``archive_power(name, ...) → ArchiveResult`` — for Powers.
- ``archive_mortal(creature_id, ...) → ArchiveResult`` — for Mortals.
- ``archive_beast(creature_id, ...)`` and ``archive_automaton(...)``
  — for Beasts and Automatons.
- ``fade_pantheon_member``, ``exile_pantheon_member``, ``ragnarok`` —
  stubs in v0.11 (no Pantheon member is currently fading; promote
  these to real implementations when the first Succession or Fading
  happens).

Each archive function is idempotent: a second call on an already-
archived Creature reports ``unchanged`` for each artifact rather than
erroring. This matches Hephaestus's `unchanged`/`updated`/`created`
shape so operator output reads consistently across forge + archive.
"""

from .archive import (  # noqa: F401
    ArchiveAction,
    ArchiveError,
    ArchiveResult,
    archive_automaton,
    archive_beast,
    archive_mortal,
    archive_power,
    exile_pantheon_member,
    fade_pantheon_member,
    ragnarok,
)
