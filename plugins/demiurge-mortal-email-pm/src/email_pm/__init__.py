"""Email PM Mortal — packaged as ``demiurge-mortal-email-pm`` (v0.11)."""

from __future__ import annotations


def manifest():
    from shared.plugins.discovery import load_manifest_for_package

    return load_manifest_for_package("email_pm")
