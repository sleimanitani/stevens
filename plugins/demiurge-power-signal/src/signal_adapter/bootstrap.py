"""Signal plugin bootstrap hook — v0.11 step 8."""

from __future__ import annotations


def install(manifest):
    print(
        f"[demiurge-power-signal] bootstrap: manifest={manifest.name!r} "
        f"v{manifest.version}. For account onboarding, run "
        f"`demiurge onboard signal` (see docs/runbooks/signal.md). "
        f"Note: signal-cli-rest-api still runs in docker (see dev/) "
        f"until the native install recipe lands as a follow-up."
    )
