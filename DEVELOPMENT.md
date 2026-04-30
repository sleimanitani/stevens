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

---

## Skills layer: tools and playbooks

The `skills/` package is the substrate every agent is built on. Two
separate first-class concepts: **tools** (Python functions agents call)
and **playbooks** (Markdown procedural knowledge loaded into prompts at
runtime). They are NOT the same thing — different review workflows,
different storage, different failure modes.

See `CLAUDE_skills_layer.md` for the canonical spec.

### Adding a new tool

1. Write the implementation under `skills/proposed/tools/<category>/<slug>-<short>.py`
   using this shape (see `skills/src/skills/tools/pdf/read_pdf.py` for the
   canonical example):
   ```python
   from langchain_core.tools import StructuredTool
   from pydantic import BaseModel

   TOOL_METADATA = {
       "id": "<category>.<name>",
       "version": "1.0.0",
       "scope": "shared",          # or "restricted"
       "safety_class": "read-only", # or "read-write" / "destructive"
   }

   class Inputs(BaseModel): ...

   def _impl(...): ...

   def build_tool() -> StructuredTool:
       return StructuredTool.from_function(...)
   ```
2. (Optional, agents only) Call `propose_skill(kind="tool", title=..., body=...)`
   from inside an agent at runtime — that records the proposal in the
   `skill_proposals` table and writes the body to `skills/proposed/tools/`
   automatically.
3. Get it reviewed: `uv run python scripts/review_skills.py list`,
   `... show <id>`, then `... approve <id> --scope shared --safety read-only`.
   Approval moves the file under `skills/src/skills/tools/<category>/`
   and appends the entry to `skills/registry.yaml`.

### Adding a new playbook

1. Write the body under `skills/proposed/playbooks/<category>/<slug>-<short>.md`
   with `agentskills.io`-compatible frontmatter:
   ```markdown
   ---
   name: email-blocker-triage
   description: Identify threads where Sol is blocking someone else
   version: 1.0.0
   author: email_pm
   license: proprietary
   metadata:
     applies_to_topics: ["email.received.*"]
     applies_to_agents: ["email_pm"]
     triggers:
       - regex: "(?i)(blocking|blocked on|waiting for you)"
     status: active
   ---

   ## When to apply
   ...
   ## Procedure
   ...
   ## Anti-patterns
   ...
   ```
2. Approve it: `uv run python scripts/review_skills.py approve <id> --category email`.
3. The next runtime load will pick it up — no restart of Enkidu needed.

### Reviewing proposals

`uv run python scripts/review_skills.py list` shows pending. For each:

- **Tools**: read every line. Confirm `safety_class` is honest (does it
  modify state? destructive?). Confirm `scope` is right (does another
  agent ever need this?). Confirm no secret material is read or returned
  outside an Enkidu capability call.
- **Playbooks**: read the procedure. Verify each `triggers.regex`
  matches plausible inputs without false-positives. Verify the "anti-
  patterns" section is real.

Approve with `... approve <id>` (defaults: scope=shared, safety=read-only,
category derived from slug). Reject with `... reject <id> --reason "..."`.

### Tesseract for the PDF reader's OCR fallback

`apt install tesseract-ocr` once on the dev machine. Without it, the
PDF reader's OCR fallback skips with a warning rather than failing —
text-based PDFs still work; only scanned PDFs are affected.
