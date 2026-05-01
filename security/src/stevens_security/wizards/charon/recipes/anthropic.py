"""Anthropic API key recipe.

End state: an API key landed in the sealed store as
``compress.anthropic.api_key`` (Stevens uses the Anthropic key for
LLM-based content compression in the network.compress capability;
naming reflects that). Same key works for any other Anthropic call
we add later — operator can re-key under different names with
``--rotate``.
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


_KEYS_URL = "https://console.anthropic.com/settings/keys"
_NEW_KEY_BUTTON = "button:has-text('Create Key')"
_NAME_INPUT = "input[name='name'], input[placeholder*='name' i]"
_GENERATE_BUTTON = "button:has-text('Create Key')"
_COPY_BUTTON = "button:has-text('Copy')"
_KEY_DISPLAY = "[data-testid='api-key-display'], code"


class AnthropicRecipe:
    name = "anthropic"
    description = "Anthropic API key — for compression + future Anthropic calls"
    prerequisites = ["an Anthropic account (https://console.anthropic.com)"]

    def available(self) -> bool:
        try:
            import playwright  # noqa: F401
            return True
        except ImportError:
            return False

    def steps(self, **kwargs) -> List[Step]:
        return [
            Nav(_KEYS_URL),
            OperatorAction(
                "Sign in to your Anthropic console if prompted, then press Enter."
            ),
            WaitFor(_NEW_KEY_BUTTON, timeout_s=60),
            Click(_NEW_KEY_BUTTON),
            WaitFor(_NAME_INPUT),
            Fill(_NAME_INPUT, "Stevens"),
            Click(_GENERATE_BUTTON),
            WaitFor(_KEY_DISPLAY, timeout_s=15),
            OperatorAction(
                "Anthropic should now be showing your new key (this is the only "
                "time it'll be visible). Press Enter once you see it."
            ),
            Extract(_KEY_DISPLAY, into_slot="api_key"),
            StoreSecret(
                slot="api_key", secret_name="compress.anthropic.api_key",
                metadata_kind="anthropic_api_key",
            ),
        ]


register(AnthropicRecipe())
