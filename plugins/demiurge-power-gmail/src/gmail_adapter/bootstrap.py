"""Gmail plugin bootstrap hook — v0.11 step 8.

Invoked by Hephaestus during ``demiurge powers install gmail``. v0.11
ships a thin stub: prints a hint pointing the operator at the existing
runbook (``docs/runbooks/gmail.md``) for the OAuth dance, since the
account-level onboarding still uses the legacy ``demiurge onboard
gmail`` flow.

Step 9 (or v0.11.x) will replace this with a real install hook that
codifies the existing ``add_account.py`` flow into something
``demiurge powers install gmail`` runs end-to-end. For v0.11 we
preserve the existing onboarding path so the migration is structural,
not behavioral.
"""

from __future__ import annotations


def install(manifest):
    """Bootstrap hook entry point. Called once on plugin install."""
    print(
        f"[demiurge-power-gmail] bootstrap: manifest={manifest.name!r} "
        f"v{manifest.version}. For account onboarding, run "
        f"`demiurge onboard gmail` (see docs/runbooks/gmail.md)."
    )
