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

- Linux (Debian/Ubuntu primary target; macOS best-effort). Windows: see `dev/`.
- Python 3.10+ with `uv` installed (https://docs.astral.sh/uv/)
- Ollama running on the host with `qwen3:30b-a3b-instruct` pulled
- A Google Cloud project with Gmail API + Pub/Sub enabled (for Gmail channel)
- Tailscale with Funnel enabled (for inbound webhooks)

> **Not a prerequisite anymore:** Docker. Demiurge v0.10+ installs natively
> via `demiurge bootstrap` (native Postgres + systemd user units). Docker is
> deliberately *not* used — `docker` group membership is functionally
> passwordless root (see DEMIURGE.md §2 Principle 14). The legacy
> `docker compose` path lives under `dev/` for developers who want it.

## First-time setup

```bash
# 1. Install Python deps via uv workspace
uv sync

# 2. Run the bootstrap. It detects what's missing on this host and prints
#    the one sudo block you need to run yourself. (bootstrap never escalates.)
uv run demiurge bootstrap

# 3. Run the printed sudo block. On a fresh Debian/Ubuntu box it looks like:
#       sudo apt-get install -y postgresql-16 postgresql-16-pgvector
#       sudo -u postgres createuser -s $USER
#    macOS uses `brew install postgresql@16 pgvector`.

# 4. Re-run bootstrap to finish setup (creates assistant role+DB, applies
#    migrations, writes ~/.config/demiurge/env, generates systemd user units).
uv run demiurge bootstrap

# 5. One-time per machine: enable systemd user lingering so services start at boot.
sudo loginctl enable-linger $USER

# 6. Initialize the sealed store (you'll be prompted for a passphrase).
uv run demiurge secrets init

# 7. Bring up Enkidu (the Security Agent).
systemctl --user start demiurge-security

# 8. Pull the local model (on host).
ollama pull qwen3:30b-a3b-instruct
```

After this, onboard channels — see `docs/runbooks/` for per-channel guides
(`gmail.md`, `calendar.md`, `whatsapp-cloud.md`, `signal.md`). In v0.11
those become `demiurge channels install <name>`.

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

After `demiurge bootstrap`, every Demiurge service runs as a systemd user
unit. Manage them like any other systemd service — no sudo needed:

```bash
# Start individual services
systemctl --user start demiurge-security
systemctl --user start demiurge-gmail-adapter
systemctl --user start demiurge-agents

# Status / logs
systemctl --user status demiurge-security
journalctl --user -u demiurge-agents -f

# Stop / restart
systemctl --user restart demiurge-gmail-adapter
```

The catalog of available units lives in
`security/src/demiurge/bootstrap/systemd.py` (`DEFAULT_SERVICES`).

For development you can run the agents runtime directly instead of through
the unit, for faster iteration:

```bash
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
├── pyproject.toml          # uv workspace root
├── .env.example
├── shared/                 # shared Python package (schemas, bus, db)
├── security/               # Enkidu (Security Agent) + bootstrap subpackage
├── channels/
│   ├── gmail/              # Python + FastAPI
│   ├── calendar/           # Python + FastAPI
│   ├── whatsapp-cloud/     # Python + FastAPI
│   ├── signal/             # Python adapter for signal-cli-rest-api
│   └── whatsapp/           # Node.js + Baileys (legacy; moves to v0.11 plugin)
├── agents/                 # Python agent runtime + agent definitions
├── resources/
│   └── migrations/         # SQL schema
├── dev/
│   └── compose.yaml        # legacy docker-compose path (developer-only)
└── docs/
    ├── prd.docx            # the full PRD + TRD
    └── runbooks/           # per-channel onboarding
```

## Non-goals (v0.1)

- No autonomous sending. Drafts only.
- No cloud LLM calls. Local model only (privacy-first).
- No orchestrator. Agents subscribe directly.
- No interface agent. Gmail is the UI.

See `docs/prd.docx` section 1.3 for the full list.
