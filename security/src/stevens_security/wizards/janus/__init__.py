"""Janus — operator-assisted browser automation for OAuth/config dances.

Operator owns the browser session (signs in, approves OAuth, solves
CAPTCHAs); Janus does everything else (nav, fill, click, extract
token, store in sealed store).

Greek mythology: Janus ferries souls across the Styx to where they
need to go. Maps to: ferries the operator across to a new system they
need access to.

Each provider is a ``PlatformRecipe`` registered at module import.
v0.7 ships brave / anthropic / google_oauth_client; new providers are
new modules under ``recipes/``.
"""

from .recipe import (  # noqa: F401
    PlatformRecipe,
    RecipeError,
    RecipeResult,
    get,
    known,
    register,
)
from .runner import execute_recipe  # noqa: F401
from .session import BrowserSession, FakeBrowserSession  # noqa: F401
from .steps import (  # noqa: F401
    Click,
    Extract,
    Fill,
    Nav,
    OperatorAction,
    Step,
    StoreSecret,
    WaitFor,
)

# Side-effect imports: built-in recipes self-register here.
from .recipes import anthropic, brave, google_oauth_client  # noqa: F401, E402
