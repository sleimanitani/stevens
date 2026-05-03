"""Email PM Mortal bootstrap hook — v0.11 step 9.

Invoked by Hephaestus during ``demiurge hire spawn email_pm``. v0.11
ships a thin stub: real instance-level setup (subscribing to bus
events, registering in the agents runtime) lands in v0.11.x once the
runtime supervisor's Mortal subprocess main is wired (placeholder
today per step 7.3).
"""

from __future__ import annotations


def hire(manifest):
    print(
        f"[demiurge-mortal-email-pm] bootstrap: manifest={manifest.name!r} "
        f"v{manifest.version}. Subscribes to email.received.* topics; "
        f"requires the gmail power installed (`demiurge powers install gmail`)."
    )
