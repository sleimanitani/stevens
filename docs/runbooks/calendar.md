# Runbook — Google Calendar

End state: each Calendar account you onboard publishes `calendar.event.changed.<account_id>` events as events change in Google Calendar; agents can read calendar context through the Security Agent broker.

## Goal

- One or more Calendar accounts onboarded.
- Refresh tokens stored as `calendar.<account_id>.refresh_token` in the sealed store.
- A `channel_accounts` row per account with `channel_type='calendar'`.
- An `events.watch` push channel registered so changes arrive as bus events.

## Prerequisites

- A Google Calendar account (any Google account that has Calendar enabled — it does, by default).
- A GCP project with the Calendar API enabled and an OAuth Desktop client. **You can reuse the same project + OAuth client you set up for Gmail** — just store the secrets under separate `calendar.*` names so credential rotation is independent.
- Stevens already installed: `uv run stevens bootstrap` succeeded, sealed store initialized. See [`README.md`](README.md) §"Fresh-install master flow".
- `gcloud` auth + Janus extra (same as Gmail).
- A public webhook URL to receive Calendar's push channels (separate path from Gmail; e.g. `https://stevens.example.ts.net/calendar/push`).

## Steps

### Path A — reuse the project + OAuth client from Gmail (recommended for personal use)

```bash
# 1. extract the same client_id/secret from your Gmail JSON into the
#    calendar.* sealed-store names. (The wizard handles per-namespace
#    storage; for Calendar we go direct since the JSON is on disk.)
jq -r '.installed.client_id' ~/Downloads/client_secret_X.json \
  | uv run stevens secrets add calendar.oauth_client.id --from-stdin \
      --metadata kind=oauth_client

jq -r '.installed.client_secret' ~/Downloads/client_secret_X.json \
  | uv run stevens secrets add calendar.oauth_client.secret --from-stdin \
      --metadata kind=oauth_client

# 2. onboard each Calendar account
uv run python -m calendar_adapter.add_account \
    --id calendar.personal --name "Sol personal cal" \
    --webhook-url https://stevens.example.ts.net/calendar/push
# Browser opens for OAuth consent — sign in to the Google account whose
# calendar you want to onboard. Refresh token lands in the sealed store
# under calendar.personal.refresh_token; events.watch registers; row in
# channel_accounts.
```

### Path B — separate project + OAuth client (cleaner rotation isolation)

```bash
# Same as the Gmail flow but with a distinct project id. Stevens treats
# Gmail and Calendar OAuth clients as separate sealed-store entries by
# design, so you can rotate one without forcing the other.
uv run stevens wizard google --project-id stevens-calendar \
    --push-endpoint https://stevens.example.ts.net/calendar/push
uv run stevens janus run google_oauth_client --project-id stevens-calendar
# (Click Download JSON in the popup.)

# Then store under calendar.* names:
jq -r '.installed.client_id' ~/Downloads/client_secret_Y.json \
  | uv run stevens secrets add calendar.oauth_client.id --from-stdin
jq -r '.installed.client_secret' ~/Downloads/client_secret_Y.json \
  | uv run stevens secrets add calendar.oauth_client.secret --from-stdin

# Onboard accounts as in Path A step 2.
```

## Verify

```bash
uv run stevens secrets list
# expected: calendar.oauth_client.id / .secret + calendar.<account_id>.refresh_token

# Make a small change to a calendar event in Google Calendar; within
# ~seconds you should see a calendar.event.changed.<account_id> event
# in `stevens audit tail -f`.
```

## Multi-account

Same shape as Gmail — rerun `add_account` per Calendar account with a different `--id`.

## Common issues

- **"events.watch returned 401."** OAuth scope missing. The wizard's Janus recipe adds `calendar` and `calendar.events`; if you took Path A but onboarded Calendar before adding those scopes, re-run the consent screen step.
- **"Push channel expired."** Calendar push channels expire periodically (~1 week). The `calendar_adapter/watch_renew.py` cron renews them; make sure that's running. `stevens doctor` will warn if a channel is past its renewal window.
- **"Sync token invalid."** Google occasionally invalidates them; the adapter handles this by falling back to a full sync. If you see `sync_token_invalid` in audit, the adapter is recovering — no action needed.
