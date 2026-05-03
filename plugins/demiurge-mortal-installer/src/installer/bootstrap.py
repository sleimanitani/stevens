"""Installer Mortal bootstrap hook — v0.11 step 9."""

from __future__ import annotations


def hire(manifest):
    print(
        f"[demiurge-mortal-installer] bootstrap: manifest={manifest.name!r} "
        f"v{manifest.version}. Subscribes to system.dep_requested.* topics."
    )
