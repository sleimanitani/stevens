# Development guide

> **2026-05-02 note:** the previous version of this file was a v0.0-skeleton
> walkthrough that pre-dates v0.1's security architecture, v0.10's bootstrap
> flow, and most of the channels. It described `docker compose` as the
> install path; that's no longer true. The current install + onboarding
> path lives in [`README.md`](README.md) and [`docs/runbooks/`](docs/runbooks/).
> Pre-v0.10 history is in `git log -- DEVELOPMENT.md`.

## How Stevens runs in dev

After `uv run stevens bootstrap`:

- Postgres is the **native** apt-installed `postgresql@16-main` system service.
- Stevens services (Enkidu, channel adapters, agents runtime) run as
  systemd **user** units under your account. Manage with
  `systemctl --user {start,stop,status,restart} stevens-<name>`.
- Logs land in the journal: `journalctl --user -u stevens-<name> -f`.
- Sealed-store + secrets live under `/var/lib/stevens/secrets/` (or
  `STEVENS_SECURITY_SECRETS`).
- The repo lives wherever you cloned it; the systemd units `WorkingDirectory=`
  point at it, so do *not* move the checkout without re-running
  `uv run python -m stevens_security.bootstrap.systemd --write` to refresh
  the unit files.

For fast iteration on one service, stop its unit and run the process
directly under `uv run`:

```bash
systemctl --user stop stevens-agents
uv run python -m agents.runtime           # foreground; ^C to stop
systemctl --user start stevens-agents     # restore
```

## Testing

```bash
uv run pytest                             # full suite
uv run pytest security/tests/             # one package
uv run pytest -k bootstrap                # by name
DATABASE_URL=postgresql:///assistant uv run pytest   # un-skip integration tests
```

Some tests are gated on `$DATABASE_URL` (the bootstrap-migrate +
bootstrap-postgres integration tests). With it unset they `SKIP`. With it
set they run against your real Postgres — they're written to be idempotent
and not pollute state, but they do exercise real connections.

For the full multi-service end-to-end smoke (publish a Gmail event, watch
it land as a row, watch the agent pick it up), the cleanest path is
to use the per-channel runbooks in `docs/runbooks/` against a clean
sealed store + DB. `stevens reset` (default = dry-run; `--yes` to commit)
wipes everything for a fresh re-onboarding pass.

## Debugging

- **Service won't start?** `systemctl --user status stevens-<name>` first;
  then `journalctl --user -u stevens-<name> -n 200`.
- **Events not arriving?** Check the per-channel adapter's logs; the issue
  is usually upstream (webhook unreachable, Pub/Sub watch expired,
  daemon down). Each runbook has a "Common issues" section that hits the
  recurring causes.
- **Agent not firing?** Check `subscription_cursors` for the agent's row.
  If missing, the agent runtime never picked it up. If stale, the agent
  crashed.
- **LLM slow or weird?** If you've enabled Langfuse (developer-only,
  optional), every LLM + tool call is a trace. Bring it up via
  `cd dev/ && docker compose up -d langfuse-db langfuse` —
  it's deliberately not part of `stevens bootstrap`.
- **Sealed store unlock failing?** `stevens doctor` reports it; if the
  passphrase is in the OS keyring (`stevens passphrase remember`),
  `keyring get stevens master-passphrase` reads it back.

## Adding things

- **New tool / playbook (skill):** see the existing [skills layer
  spec](CLAUDE_skills_layer.md) and the `propose_skill` flow in
  `skills/src/`. Reviewed via `scripts/review_skills.py`.
- **New agent:** drop a directory under `agents/src/agents/<name>/` with
  `agent.py`, `prompts.py`, optionally `tools.py`. Add a `registry.yaml`
  entry. Reference: `agents/src/agents/email_pm/`.
- **New channel:** scaffold under `channels/<name>/`, follow the existing
  channels for shape (FastAPI app for inbound, capability registry entries
  for outbound, an `add_account` CLI). Add the runbook under
  `docs/runbooks/<channel>.md`. In v0.11 channels become entry-point
  plugins under `plugins/`; until then they live in-tree.

## Tesseract for the PDF reader's OCR fallback

`apt install tesseract-ocr` once on the dev machine. Without it, the PDF
reader's OCR fallback skips with a warning rather than failing — text-
based PDFs still work; only scanned PDFs are affected.
