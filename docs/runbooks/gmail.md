# Runbook — Gmail

End state: each Gmail account you onboard is sending events into the bus
(`email.received.<account_id>`), and agents can draft replies through the
Security Agent. Refresh tokens live only in the sealed store; the
adapter process holds none.

Supersedes the older `gmail-oauth-setup.md` (kept for history; this one
matches what the CLI actually does post-v0.6 and v0.7).

## Goal

- One or more Gmail accounts onboarded — personal, Workspace, or both
  (multi-account is first-class).
- Refresh tokens stored as `gmail.<account_id>.refresh_token` in the sealed store.
- A `channel_accounts` row per account with `channel_type='gmail'`.
- Pub/Sub watch active so new messages arrive as bus events instead of being polled.

## Prerequisites

- A Google account with permission to create a GCP project (any personal
  Google account or Workspace user).
- Local machine has: `gcloud` CLI, Docker, Postgres up (compose),
  Stevens migrations applied. See [`README.md`](README.md) §"Fresh-install master flow".
- Janus extra installed: `uv sync --extra janus` + `uv run playwright install chromium`.
- A public webhook URL to receive Gmail's Pub/Sub pushes
  (Tailscale Funnel, Cloudflare Tunnel, ngrok, anything Google can reach over HTTPS).

## Steps

```bash
# 1. authenticate gcloud once (the wizard runs gcloud commands as you)
gcloud auth login
gcloud auth application-default login    # the Pub/Sub calls need this

# 2. run the GCP-side wizard. Creates project, enables APIs, creates
#    Pub/Sub topic + IAM grants + push subscription. Pauses at the
#    OAuth-client step (which Janus will drive in step 3).
uv run stevens wizard google --project-id stevens-personal \
    --push-endpoint https://stevens.example.ts.net/gmail/push

# 3. (in a separate terminal, while the wizard is at the OAuth-client
#    step) Janus drives consent screen + Desktop OAuth client creation.
uv run stevens janus run google_oauth_client --project-id stevens-personal
# Janus opens a browser. Sign in to Google when prompted. Janus walks
# you through the consent-screen scopes + publish, then the credentials
# page + Create Desktop client. At the end it pauses on a popup; YOU
# click "Download JSON" (Janus can't intercept the browser save dialog).

# 4. back in the wizard's terminal — it's polling ~/Downloads/ for
#    client_secret*.json. As soon as the file lands the wizard prints
#    the next command:
uv run stevens onboard gmail \
    --client-json ~/Downloads/client_secret_X.json \
    -- --id gmail.personal --name "Sol personal"
# This ingests the OAuth client into the sealed store, opens a browser
# for the per-account OAuth consent (you sign in to the specific Gmail
# account you want to onboard), stores the refresh token, calls
# users.watch() to register Pub/Sub, and inserts a channel_accounts row.

# 5. (optional, per additional account) repeat step 4 with a new --id:
uv run stevens onboard gmail \
    -- --id gmail.work --name "Sol work"
# (no --client-json needed — it's already in the sealed store from the
#  first run)
```

## Verify

```bash
uv run stevens secrets list
# expected: gmail.oauth_client.id / .secret + gmail.<account_id>.refresh_token per account

uv run stevens audit tail
# expected: an `ok` line for users.watch when the per-account flow ran

uv run stevens doctor
# expected: green
```

Send yourself a test email to one of the onboarded accounts. Within
~seconds the Pub/Sub push lands at your webhook URL, the gmail-adapter
publishes an `email.received.<account_id>` event, and the Email PM
agent (if running) picks it up. You'll see all of this in
`stevens audit tail -f`.

## Multi-account

You can onboard as many Gmail accounts as you want under the same
project + OAuth client. Just rerun step 4 with a different `--id`. Each
account gets its own sealed-store entry and its own `channel_accounts`
row; the adapter handles them all.

If you have **personal + Workspace** accounts: use **External + In
production** for the OAuth consent screen (Janus configures this — the
wizard's recipe locks in this choice for you). Internal-mode would
restrict the OAuth client to a single Workspace org, which doesn't work
for personal Gmail.

## Common issues

- **"Pub/Sub subscription created but no events arrive."** Your webhook
  URL has to be reachable from Google's network. If it's
  `localhost`-only or behind a NAT without a tunnel, Google can't deliver.
  Set up a Tailscale Funnel or similar and pass that URL to the wizard
  via `--push-endpoint`. You can update it later via
  `gcloud pubsub subscriptions update`.
- **"Janus selector not found."** Google's UI changed. Update the
  selectors at the top of
  `security/src/stevens_security/wizards/janus/recipes/google_oauth_client.py`
  and re-run.
- **"Refresh token didn't appear in the OAuth response."** Google
  already had consent on file for that account. Revoke prior consent at
  https://myaccount.google.com/permissions and re-run the per-account
  flow.
- **"My OAuth consent screen is in Testing, will tokens expire after 7 days?"**
  Yes. Move the publishing status to "In production" (the wizard's
  Janus recipe does this for you; if you're on the older runbook, do it
  manually).
- **"Can I use the same OAuth client for Calendar?"** Yes — and that's
  what the [Calendar runbook](calendar.md) assumes. If you'd rather
  isolate Calendar's credential rotation from Gmail's, run the wizard
  again with a separate project id; the Calendar runbook covers both
  options.
