# Runbook — Gmail OAuth setup (with sealed store) — **SUPERSEDED**

> **⚠️ This runbook is from v0.1-sec era and predates `demiurge wizard google` + `demiurge janus`.**
> **Use [`gmail.md`](gmail.md) instead.** This file is kept for git history; the modern path is shorter.

---

Goal: get Demiurge's Security Agent holding the Gmail OAuth credentials so the Email PM agent can draft replies without ever seeing a raw token.

## Prerequisites

- Docker / compose up, including the `security` service.
- `~/.local/bin/uv`.
- `uv sync --all-packages --all-extras` succeeded.
- `demiurge` CLI available (`uv run demiurge --help`).
- A Google Cloud project with Gmail API enabled, a Pub/Sub topic, and an OAuth 2.0 Client ID of type **Desktop application**.

## One-time setup: store the OAuth client in the sealed store

Google gives you a `client_secret_XXX.json` when you create the OAuth client. Its two load-bearing fields are `client_id` and `client_secret`. Instead of mounting the JSON file into `./secrets/`, extract those two values and put them in the sealed store:

```bash
# First time: initialize the sealed store.
uv run demiurge secrets init

# Feed the two fields in, each as its own named secret.
jq -r '.installed.client_id'     ~/Downloads/client_secret_XXX.json \
  | uv run demiurge secrets add gmail.oauth_client.id --from-stdin --metadata kind=oauth_client

jq -r '.installed.client_secret' ~/Downloads/client_secret_XXX.json \
  | uv run demiurge secrets add gmail.oauth_client.secret --from-stdin --metadata kind=oauth_client

# Shred the JSON file from disk.
shred -u ~/Downloads/client_secret_XXX.json
```

Verify:

```bash
uv run demiurge secrets list
# expected:
# <id>  gmail.oauth_client.id      [live]  ...
# <id>  gmail.oauth_client.secret  [live]  ...
```

## Per-account: add a Gmail account

Running `uv run python -m gmail_adapter.add_account --id gmail.personal --name "Sol personal"` does the OAuth browser flow against the client in the sealed store. When OAuth completes, the adapter extracts the refresh token and stores **only that** in the sealed store under `gmail.personal.refresh_token`, then inserts a row in `channel_accounts` with `credentials_ref = 'gmail.personal.refresh_token'` and `credentials = '{}'`.

You should never see the raw refresh token or the access token on your screen, in your shell history, or in any file under `./`.

## What changed from the old flow

Old (see `DEVELOPMENT.md` §2a):

- OAuth client JSON file lived at `./secrets/gmail_oauth_client.json`, bind-mounted into the `gmail-adapter` container.
- Refresh + access tokens were stored in `channel_accounts.credentials` as plaintext JSONB.
- Agents loaded these credentials directly via `tool_factory.get_gmail_tools()` and attached them to googleapiclient calls.

New (this runbook + DEMIURGE.md §3):

- OAuth client id + secret live only in the sealed store, unlocked at Security Agent boot.
- Per-account refresh tokens live only in the sealed store, by name `<account_id>.refresh_token`.
- `channel_accounts.credentials` holds only an opaque `credentials_ref`.
- Agents call Gmail capabilities (`gmail.search`, `gmail.create_draft`, etc.) through the Security Agent's UDS. They never hold a Gmail token in process memory.

## Rotation + revocation

Rotation (e.g. Google revoked a refresh token):

```bash
# Get the old id.
uv run demiurge secrets list | grep gmail.personal.refresh_token
# Rotate — the new value comes in through the add_account flow; or paste by hand.
new_token="$(cat fresh_refresh_token.txt)"
echo -n "$new_token" | uv run demiurge secrets rotate <old-id> --from-stdin
```

Revoke (lost device / compromised token):

```bash
uv run demiurge secrets revoke <id>
# Agents immediately lose the ability to act on that account — the Security
# Agent's get_by_name refuses to return the tombstoned secret.
```
