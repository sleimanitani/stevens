"""Hephaestus — creator of Creatures (v0.11).

Submodules (incremental landing across v0.11 step 3):

- ``gods`` (3c) — `GodlyBlessing` adapters for each existing god +
  stubs for the not-yet-shipped ones. Adapter pattern so Hephaestus
  can talk to every god through a single interface regardless of how
  the god's policy machinery is internally shaped.
- ``tool_routing`` (3c) — DEFAULT_ROUTES (prefix→god map) + the
  `BlessedToolWrapper` runtime + `forge_blessed_registry()` composer
  that combines blessings + universal tools into a Mortal's
  `ToolRegistry`.
- ``forge`` (3d/3e) — the orchestrator: `forge_power`, `forge_mortal`,
  `forge_beast`, `forge_automaton`. Coming next.
"""

from .gods import (  # noqa: F401
    ArachneGod,
    EnkiduGod,
    IrisStubGod,
    JanusGod,
    MnemosyneStubGod,
    SphinxGod,
    ZeusStubGod,
)
from .tool_routing import (  # noqa: F401
    DEFAULT_ROUTES,
    BlessedToolWrapper,
    forge_blessed_registry,
)
