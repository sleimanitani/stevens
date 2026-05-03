"""Calendar plugin bootstrap hook — v0.11 step 8."""

from __future__ import annotations


def install(manifest):
    print(
        f"[demiurge-power-calendar] bootstrap: manifest={manifest.name!r} "
        f"v{manifest.version}. For account onboarding, run "
        f"`demiurge onboard calendar` (see docs/runbooks/calendar.md)."
    )
