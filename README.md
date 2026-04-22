# Personal Assistant

Multi-agent personal assistant system. One user, local-first, grows one agent at a time.

See `docs/prd.docx` for the full product + technical spec (you should have this already; drop it into `docs/` before your first commit).

## Architecture at a glance

Five layers, top to bottom:

1. **Human interface** — Gmail labels + CLI (v0.1)
2. **Agents** — independent units that subscribe to events and act
3. **Agent runtime** — single Python process reading `registry.yaml`
4. **Tools & channels** — Gmail adapter, WhatsApp adapter, LangChain toolkits
5. **Resources** — Ollama (host), Postgres, Langfuse

Events flow: channels publish to the bus (Postgres table in v0.1, NATS later), agents subscribe by topic pattern, agents call channel action APIs (or LangChain tools) to do work.

## Prerequisites

- Docker + Docker Compose
- Python 3.12 with `uv` installed
- Node.js 22+ (for WhatsApp adapter)
- Ollama running on the host (not in Docker) with `qwen3:30b-a3b-instruct` pulled
- A Google Cloud project with Gmail API + Pub/Sub enabled
- Tailscale with Funnel enabled on this machine
- An empty `.env` file (copy from `.env.example`)

## First-time setup

```bash
# 1. Install Python deps via uv workspace
uv sync

# 2. Install Node deps for WhatsApp adapter
cd channels/whatsapp && npm install && cd ../..

# 3. Start infrastructure (postgres + langfuse)
docker compose up -d postgres langfuse-db langfuse

# 4. Apply migrations
docker compose exec -T postgres psql -U assistant -d assistant < resources/migrations/001_init.sql

# 5. Pull the local model (on host, not in Docker)
ollama pull qwen3:30b-a3b-instruct

# 6. Verify Ollama is reachable
curl http://localhost:11434/api/tags

# 7. Expose Gmail adapter via Tailscale Funnel (once it's running)
tailscale funnel --bg 8080
# Note the https URL it prints — this goes in Google Pub/Sub config
```

## Onboarding accounts

Each channel has an `add_account` CLI. Run it once per account.

### Gmail

```bash
uv run python -m gmail_adapter.add_account \
  --id gmail.personal \
  --name "Sol personal"
```

This opens a browser for OAuth, stores credentials in `channel_accounts`, and registers the account with Google Pub/Sub push.

Repeat for each Gmail account:

```bash
uv run python -m gmail_adapter.add_account --id gmail.atheer --name "Atheer"
```

### WhatsApp

```bash
cd channels/whatsapp
npm run add-account -- --id wa.us --name "US number"
```

A QR code prints in the terminal. Open WhatsApp on your phone → Linked Devices → Link a Device → scan.

Repeat:

```bash
npm run add-account -- --id wa.uae --name "UAE number"
```

## Running

```bash
docker compose up -d
```

This starts: postgres, langfuse, langfuse-db, gmail-adapter, whatsapp-adapter, agents.

For development, run the agents process locally instead of in Docker for faster iteration:

```bash
docker compose up -d postgres langfuse langfuse-db gmail-adapter whatsapp-adapter
uv run python -m agents.runtime
```

## Adding a new agent

1. Create `agents/src/agents/<name>/` with `agent.py`, `prompts.py`, optionally `tools.py`
2. Add an entry to `agents/src/agents/registry.yaml` with subscriptions, schedule, account scope
3. Restart the agents process

See `agents/src/agents/email_pm/` for a reference implementation.

## Adding a new channel

1. Create `channels/<name>/` with adapter service
2. Define event schema(s) in `shared/src/shared/events.py`
3. Implement push → `bus.publish(...)` path
4. Implement action API
5. Implement `add_account` CLI
6. Bind new LangChain tools to agents that want to act on the channel

## Observability

Langfuse runs at `http://localhost:3000`. Every agent invocation, every LLM call, every tool call is a trace. Check here first when debugging agent behavior.

## Repo layout

```
assistant/
├── compose.yaml            # all services
├── pyproject.toml          # uv workspace root
├── .env.example
├── shared/                 # shared Python package (schemas, bus, db)
├── channels/
│   ├── gmail/              # Python + FastAPI
│   └── whatsapp/           # Node.js + Baileys
├── agents/                 # Python agent runtime + agent definitions
├── resources/
│   └── migrations/         # SQL schema
└── docs/
    └── prd.docx            # the full PRD + TRD
```

## Non-goals (v0.1)

- No autonomous sending. Drafts only.
- No cloud LLM calls. Local model only (privacy-first).
- No orchestrator. Agents subscribe directly.
- No interface agent. Gmail is the UI.

See `docs/prd.docx` section 1.3 for the full list.
