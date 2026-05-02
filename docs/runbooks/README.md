# Runbooks

Operator-facing setup instructions per channel. Read these in the order
that matches what you want to do.

## Fresh-install master flow

If you're starting from a clean machine (or just ran `demiurge reset`):

```bash
# 1. one-time machine prep — bootstrap detects what's needed and prints
#    the one sudo block to run yourself; bootstrap never escalates.
uv run demiurge bootstrap                   # detect; prints sudo block if needed
# (run the printed sudo block — typically `apt-get install postgresql-16
#  postgresql-16-pgvector` + `sudo -u postgres createuser -s $USER`)
uv run demiurge bootstrap                   # re-run to finish (idempotent)
sudo loginctl enable-linger $USER          # services start at boot

# (optional) tools needed by some channels:
gcloud auth login                          # only if you'll onboard Google channels
uv sync --extra janus                      # only if you'll use Janus (Playwright)
uv run playwright install chromium         # only if you'll use Janus

# 2. one-time Demiurge prep
uv run demiurge secrets init                # set passphrase for sealed store
uv run demiurge passphrase remember         # opt-in: silent unlocks via OS keyring

# 3. onboard channel(s) — see the per-channel runbooks below
# 4. provision an agent and run it
uv run demiurge agent provision email_pm --preset email_pm
uv run demiurge agent run email_pm
```

When something doesn't work, run `uv run demiurge doctor` for a
diagnostic with one-line remediations.

To start over, run `uv run demiurge reset` (default = dry-run; pass
`--yes` to actually wipe).

## Channel runbooks

| Channel | Runbook | Status |
|---|---|---|
| **Gmail** | [`gmail.md`](gmail.md) | shipped |
| **Google Calendar** | [`calendar.md`](calendar.md) | shipped |
| **WhatsApp Cloud (business)** | [`whatsapp-cloud.md`](whatsapp-cloud.md) | shipped |
| **Signal** | [`signal.md`](signal.md) | shipped |
| WhatsApp personal (Baileys) | (no runbook) | stub channel; Baileys adapter not yet built |
| Slack / Discord / Telegram / iMessage | (no runbook) | framework ready (v0.4.1); per-channel adapters queued |

Quick discovery via the CLI: `uv run demiurge channels list`.

## Patterns common to every channel

1. **Sealed-store-mediated credentials.** Refresh tokens / API keys / app secrets land in the sealed store under deterministic names (`<channel>.<account_id>.<thing>` for per-account, `<channel>.<thing>` for shared). No agent ever holds raw credentials in memory.
2. **`channel_accounts` row per account.** Postgres-tracked; `demiurge dep list` and the like read from here.
3. **Per-account adapter.** One adapter container per account (or per phone, for Signal); the adapter polls or webhook-receives, publishes bus events.
4. **Multi-account is first-class.** Just run the onboard step again with a different `--id`.

## What if the runbook is wrong?

These docs lead the code. If a CLI flag has changed or a step doesn't work, that's a bug in the docs — file it (or fix it and PR). The CLI's `--help` output is always authoritative.
