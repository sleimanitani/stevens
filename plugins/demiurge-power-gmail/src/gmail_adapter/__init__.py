"""Gmail channel adapter — packaged as the ``demiurge-power-gmail`` plugin (v0.11)."""

from __future__ import annotations


def manifest():
    """Entry-point target for ``demiurge.powers``.

    Returns a parsed ``Manifest`` for this plugin. Used by
    ``shared.plugins.discovery`` at startup; called by Hephaestus
    during forge to learn what runtime artifact to produce.
    """
    from shared.plugins.discovery import load_manifest_for_package

    return load_manifest_for_package("gmail_adapter")
