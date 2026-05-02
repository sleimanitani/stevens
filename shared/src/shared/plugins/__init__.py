"""Plugin loader, manifest schema, and discovery (v0.11).

Submodules:
- ``manifest`` — Pydantic schema for ``plugin.yaml`` + parser.
- ``discovery`` — entry-point discovery via ``importlib.metadata``.

Demiurge core scans ``demiurge.powers`` and ``demiurge.mortals`` entry-point
groups at startup; whatever's installed is what's available. Powers are
external-world integrations (gmail, calendar, image-generator, RSS reader,
…); Mortals are task-scoped agents.

The manifest declares *what* the plugin needs (capabilities, secrets, system
deps) and *how* it wants to run (modes — webhook/listener/polling/request-
based — plus runtime details). Hephaestus (the forge Pantheon member)
reads this on install and generates the right runtime artifacts.
"""

from .discovery import (  # noqa: F401
    DiscoveryError,
    DiscoveryResult,
    InstalledPlugin,
    MORTALS_GROUP,
    PluginKind,
    POWERS_GROUP,
    discover,
    load_manifest_for_package,
)
from .manifest import (  # noqa: F401
    Manifest,
    ManifestError,
    Mode,
    PowerKind,
    RuntimeBlock,
    SecretSpec,
    load_manifest_from_text,
    load_manifest_from_yaml,
)
