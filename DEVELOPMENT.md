# Development guide

This file explains how to actually fill in the skeleton, in order.

## Current status: skeleton

The repo structure, schemas, bus, migrations, Docker setup, and registry are
complete. The three TODO areas are:

1. **Gmail adapter body** — OAuth in `add_account.py`, message fetch loop in
   `main.py:gmail_push`, watch renewal in `watch_renew.py`
2. **Ollama model pull and first-run verification**
3. **End-to-end smoke test** — send email, watch it land as an event, watch
   the agent pick it up

## Day 1: make it boot

```bash
# 1. Copy env template and fill in values
cp .env.example .env
$EDITOR .env  # at minimum, set strong POSTGRES_PASSWORD and LANGFUSE_* secrets

# 2. Install Python deps
uv sync

# 3. Install Node deps for whatsapp adapter
(cd channels/whatsapp && npm install)

# 4. Start postgres + langfuse only
docker compose up -d postgres langfuse-db langfuse

# 5. Apply migrations
docker compose exec -T postgres psql -U assistant -d assistant < resources/migrations/001_init.sql

# 6. Verify
docker compose exec postgres psql -U assistant -d assistant -c "\dt"
# should show: channel_accounts, events, followups, pending_approvals, subscription_cursors
```

At this point, `http://localhost:3000` should show Langfuse's first-run setup
screen. Create an admin user, make a project, copy the public+secret keys into
`.env`, restart langfuse.

## Day 2: Gmail adapter

### 2a. Google Cloud setup

1. Create a GCP project (or reuse an existing one)
2. Enable: Gmail API, Cloud Pub/Sub API
3. Create an OAuth 2.0 Client ID (type: Desktop application). Download JSON.
4. Save the JSON as `./secrets/gmail_oauth_client.json`
5. Create a Pub/Sub topic `gmail-push`
6. Grant `gmail-api-push@system.gserviceaccount.com` the "Pub/Sub Publisher"
   role on this topic (Gmail's push service needs to publish to it)

### 2b. Tailscale Funnel

```bash
# From the host running this repo:
tailscale funnel --bg 8080
# copy the printed https://<machine>.<tailnet>.ts.net URL
```

Put this URL into `.env` as `GMAIL_PUBLIC_URL`. It's what Google posts to.

### 2c. Create the push subscription

```bash
gcloud pubsub subscriptions create gmail-push-sub \
  --topic=gmail-push \
  --push-endpoint="${GMAIL_PUBLIC_URL}/gmail/push" \
  --push-auth-service-account="YOUR_SERVICE_ACCOUNT@YOUR_PROJECT.iam.gserviceaccount.com"
```

The service account's email is what Google signs the JWT with — it's what
`verify_pubsub_jwt` checks in `main.py`.

### 2d. Fill in the TODOs

- `channels/gmail/src/gmail_adapter/add_account.py` — OAuth flow, `users.watch()`
- `channels/gmail/src/gmail_adapter/main.py` — `gmail_push` handler's
  history fetch + event publish

### 2e. Onboard first account

```bash
uv run python -m gmail_adapter.add_account --id gmail.personal --name "Sol personal"
# Browser opens, do OAuth
```

### 2f. Start the adapter

```bash
docker compose up -d gmail-adapter
docker compose logs -f gmail-adapter
```

Send yourself a test email. You should see a push notification arrive and
a row appear in the `events` table.

```bash
docker compose exec postgres psql -U assistant -d assistant \
  -c "SELECT event_id, topic, account_id, published_at FROM events ORDER BY published_at DESC LIMIT 5;"
```

## Day 3: WhatsApp adapter

Mostly already implemented. Just needs to be built and onboarded.

```bash
cd channels/whatsapp
npm install
npm run build  # or just use `tsx` for dev

# Onboard — this prints a QR
npm run add-account -- --id wa.us --name "US number"
# scan with phone → WhatsApp → Linked Devices → Link a Device

# Start the service
docker compose up -d whatsapp-adapter
docker compose logs -f whatsapp-adapter
```

Send yourself a WhatsApp message from another device. Event should land in
the DB.

Repeat for `wa.uae`.

## Day 4: agents

```bash
# Pull the model (takes a while)
ollama pull qwen3:30b-a3b-instruct
ollama list  # verify it's there

# Run the agent runtime locally for fast iteration
uv run python -m agents.runtime
```

Send yourself a test email. Watch the logs — the Email PM should pick it up,
categorize, label, and possibly draft.

## Day 5: onboard remaining accounts + hardening

```bash
uv run python -m gmail_adapter.add_account --id gmail.atheer --name "Atheer"
# ...etc for each account
```

Then set up the daily cron (host-side or docker cron container):

```
0 8 * * *  cd /path/to/assistant && uv run python -m agents.daily_tick
```

(The `daily_tick` module is v0.1.1 — not in the first skeleton. Write it when
you're ready.)

## Debugging

- **Events not arriving?** Check `gmail-adapter` logs first. If nothing, the
  problem is upstream (Pub/Sub, Funnel, watch expired).
- **Agent not firing?** Check `subscription_cursors` for the agent's row.
  If missing, the agent never started. If stale, the agent crashed.
- **LLM slow or weird?** Check Langfuse traces — every LLM call and tool
  call is recorded.
- **Drafts not appearing in Gmail?** Check `agents` logs for exceptions from
  `gmail_create_draft`. Most likely: scope issue — re-run `add_account` with
  `gmail.modify` scope.

## The "add an agent" test

This is the test that matters: how long does it take to add a second agent?

Goal: under 2 hours for a simple subject agent (e.g. flag every email
mentioning "berwyn" and apply a `project/berwyn` label).

Steps:
1. Create `agents/src/agents/berwyn_watcher/agent.py` — maybe 30 lines
2. Add entry to `registry.yaml` — 3 lines
3. Restart agents process
4. Send test email containing "berwyn" — verify label applied

If this takes longer than 2 hours, the architecture is leaking. File an
issue against yourself.
