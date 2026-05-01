"""Google OAuth client recipe — drives the manual half of v0.6-google-wizard.

End state: a Desktop OAuth client created in the operator's GCP project,
with consent screen configured (External + Production + the four scopes
Stevens needs), and a ``client_secret*.json`` downloaded to the
operator's Downloads folder.

The download itself uses the browser's native save dialog — Charon can't
intercept that. Operator clicks "Download JSON" when prompted; the v0.6
wizard's ``wait_for_client_json`` picks up from there.

This recipe is invoked **after** the v0.6 wizard's API-able steps
complete (so the project + APIs already exist).

Pass ``project_id`` via ``recipe_kwargs={"project_id": "..."}``.
"""

from __future__ import annotations

from typing import List

from ..recipe import register
from ..steps import (
    Click,
    Fill,
    Nav,
    OperatorAction,
    Step,
    WaitFor,
)


_CONSENT_URL_FMT = "https://console.cloud.google.com/apis/credentials/consent?project={project_id}"
_CREDENTIALS_URL_FMT = "https://console.cloud.google.com/apis/credentials?project={project_id}"

_EXTERNAL_RADIO = "input[type='radio'][value='EXTERNAL']"
_APP_NAME_INPUT = "input[name='applicationName'], input[aria-label*='App name' i]"
_CREATE_CREDENTIALS_BTN = "button:has-text('Create credentials'), button:has-text('CREATE CREDENTIALS')"
_OAUTH_CLIENT_OPTION = "text=OAuth client ID"
_APP_TYPE_DROPDOWN = "[aria-label*='Application type' i], select[name='applicationType']"
_DESKTOP_OPTION = "text=Desktop app"
_CLIENT_NAME_INPUT = "input[aria-label*='Name' i][type='text']"
_CREATE_BTN = "button:has-text('Create'), button:has-text('CREATE')"


class GoogleOAuthClientRecipe:
    name = "google_oauth_client"
    description = (
        "Drive the manual half of `stevens wizard google` — OAuth consent "
        "screen + Desktop OAuth client creation + JSON download."
    )
    prerequisites = [
        "a GCP project (run `stevens wizard google` first to create one)",
        "you're already signed into Google in the browser Charon will open",
    ]

    def available(self) -> bool:
        try:
            import playwright  # noqa: F401
            return True
        except ImportError:
            return False

    def steps(self, *, project_id: str, **kwargs) -> List[Step]:
        consent_url = _CONSENT_URL_FMT.format(project_id=project_id)
        creds_url = _CREDENTIALS_URL_FMT.format(project_id=project_id)
        return [
            # === OAuth consent screen ===
            Nav(consent_url),
            OperatorAction(
                "If you see a 'CREATE' button (consent screen not yet configured), "
                "click it. Otherwise the consent screen is already set up — confirm "
                "it shows External + In production, then press Enter."
            ),
            OperatorAction(
                "Verify scopes include: gmail.modify, gmail.send, calendar, "
                "calendar.events. If missing, add them via 'Edit App' → Scopes "
                "→ Add or Remove Scopes. Press Enter when done."
            ),
            OperatorAction(
                "If status is 'Testing', click 'PUBLISH APP' → confirm. (This "
                "gives you long-lived refresh tokens; we already decided "
                "External + Production is right for both personal + Workspace.) "
                "Press Enter when done."
            ),
            # === OAuth client creation ===
            Nav(creds_url),
            WaitFor(_CREATE_CREDENTIALS_BTN, timeout_s=30),
            Click(_CREATE_CREDENTIALS_BTN),
            WaitFor(_OAUTH_CLIENT_OPTION),
            Click(_OAUTH_CLIENT_OPTION),
            WaitFor(_APP_TYPE_DROPDOWN),
            OperatorAction(
                "Select 'Desktop app' from the Application type dropdown — "
                "GCP's combobox is finicky to script reliably. Press Enter when "
                "Desktop app is selected."
            ),
            WaitFor(_CLIENT_NAME_INPUT),
            Fill(_CLIENT_NAME_INPUT, "Stevens Desktop"),
            Click(_CREATE_BTN),
            OperatorAction(
                "GCP should now show a popup with your client_id and a "
                "'Download JSON' button. Click Download JSON — the file lands "
                "in your Downloads folder. Then press Enter; `stevens wizard "
                "google` will pick it up from there."
            ),
        ]


register(GoogleOAuthClientRecipe())
