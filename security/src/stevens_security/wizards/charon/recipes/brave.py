"""Brave Search API key recipe.

End state: an API key landed in the sealed store as ``web.brave.api_key``.

Operator interaction during the run:
1. Sign in / sign up at https://api.search.brave.com (Charon opens it,
   then pauses for you).
2. Click "Continue to dashboard" (Charon clicks if it can find the
   button; otherwise pauses).
3. Click "Create new API key" (Charon clicks).
4. Name it (Charon fills "Stevens").
5. Click Generate (Charon clicks).
6. Copy the key shown — Charon extracts it into the slot, then writes
   to sealed store.

If selectors break, the recipe surfaces a clear "selector not found:
&lt;name&gt;; Brave's UI changed — recipe out of date" error so it's
obvious where to look.
"""

from __future__ import annotations

from typing import List

from ..recipe import register
from ..steps import (
    Click,
    Extract,
    Fill,
    Nav,
    OperatorAction,
    Step,
    StoreSecret,
    WaitFor,
)


# Selectors in one place at the top so when Brave changes their UI we
# update them here, not throughout the recipe.
_DASHBOARD_URL = "https://api.search.brave.com/app/dashboard"
_NEW_KEY_BUTTON = "button:has-text('Create new API key')"
_KEY_NAME_INPUT = "input[name='name']"
_GENERATE_BUTTON = "button:has-text('Generate')"
_KEY_DISPLAY = "[data-testid='api-key-value'], code.api-key, pre.api-key"


class BraveRecipe:
    name = "brave"
    description = "Brave Search API key — for the network.search capability"
    prerequisites = ["a Brave account (https://brave.com — free; takes ~30s to sign up)"]

    def available(self) -> bool:
        try:
            import playwright  # noqa: F401
            return True
        except ImportError:
            return False

    def steps(self, **kwargs) -> List[Step]:
        return [
            Nav(_DASHBOARD_URL),
            OperatorAction(
                "Sign in to your Brave account if prompted, then press Enter here."
            ),
            WaitFor(_NEW_KEY_BUTTON, timeout_s=60),
            Click(_NEW_KEY_BUTTON),
            WaitFor(_KEY_NAME_INPUT),
            Fill(_KEY_NAME_INPUT, "Stevens"),
            Click(_GENERATE_BUTTON),
            WaitFor(_KEY_DISPLAY),
            OperatorAction(
                "Brave should now be showing the key. If a CAPTCHA appeared, solve it. "
                "Press Enter here once the key is visible on the page."
            ),
            Extract(_KEY_DISPLAY, into_slot="api_key"),
            StoreSecret(slot="api_key", secret_name="web.brave.api_key",
                        metadata_kind="web_brave_api_key"),
        ]


register(BraveRecipe())
