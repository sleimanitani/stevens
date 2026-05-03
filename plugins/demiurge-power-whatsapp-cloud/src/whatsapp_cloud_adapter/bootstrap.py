"""WhatsApp Cloud plugin bootstrap hook — v0.11 step 8."""

from __future__ import annotations


def install(manifest):
    print(
        f"[demiurge-power-whatsapp-cloud] bootstrap: manifest={manifest.name!r} "
        f"v{manifest.version}. For account onboarding, run "
        f"`demiurge onboard whatsapp_cloud` (see docs/runbooks/whatsapp-cloud.md)."
    )
