# Runbook — Signal

End state: each Signal phone you onboard publishes inbound message events to the bus (DMs and groups), and agents can send replies. Stevens never touches Signal's encryption directly — the `signal-cli-rest-api` daemon owns the linked Signal session.

## Goal

- One or more Signal phones linked as devices to your Signal account(s).
- Phone + daemon URL stored under `signal.<account_id>.{phone,daemon_url}` in the sealed store.
- A `channel_accounts` row per account with `channel_type='signal'`.
- The signal-adapter container polling the daemon and publishing events.

## Prerequisites

- A real Signal account (the Signal app installed on a real phone — Signal links additional devices to a phone account; it doesn't sign up new accounts via the daemon).
- Docker (for the `bbernhard/signal-cli-rest-api` daemon).
- The `signal-cli-rest-api` service brought up in compose (port 8084 on host, 8080 inside the docker network).

## Steps

```bash
# 1. bring the signal-cli-rest-api daemon up
docker compose up -d signal-cli-rest-api

# 2. onboard the phone
uv run python -m signal_adapter.add_account \
    --id signal.personal --name "Sol personal" \
    --phone +15555551234 \
    --daemon-url http://localhost:8084
# This stores phone + daemon URL in the sealed store, fetches a QR PNG
# from the daemon, saves it to a temp path, and prints "Open this PNG
# and scan with the Signal app on your phone." Then it polls the daemon
# until it reports the phone is linked, then inserts the channel_accounts
# row.

# 3. on your phone:
#    Signal app → Settings → Linked devices → "Link new device"
#    → scan the QR from the PNG path the script printed.

# 4. start the signal-adapter container with this account's env vars
#    (one container per phone — the adapter is single-account today):
docker compose run --rm \
  -e SIGNAL_ACCOUNT_ID=signal.personal \
  -e SIGNAL_PHONE=+15555551234 \
  signal-adapter
# Or update compose.yaml to set these env vars on the signal-adapter
# service definition and `docker compose up -d signal-adapter`.
```

## Verify

```bash
uv run stevens secrets list
# expected: signal.personal.daemon_url + signal.personal.phone

# Send a Signal message FROM another account TO your linked phone.
# Within ~2s (the polling interval), the adapter publishes
# signal.message.received.signal.personal to the bus.
uv run stevens audit tail -f
```

## Multi-account / multi-phone

Each phone needs its own linked-device session in `signal-cli-rest-api`. Re-run step 2 with a different `--id` and `--phone`. The daemon supports multiple linked accounts; the adapter is one-container-per-phone for now (that's a v0.5.x improvement when needed).

## Common issues

- **"Daemon not reachable at http://localhost:8084."** Container not up, or compose port binding mismatch. `docker compose ps signal-cli-rest-api` should show it running. The daemon listens on port 8080 internally; compose maps it to host 8084.
- **"QR code not scanning."** PNG might be too small for your phone's camera. Open it on a larger display (laptop screen). Or open the daemon URL directly in a browser: `http://localhost:8084/v1/qrcodelink/Stevens?number=+15555551234` — it returns the same PNG.
- **"Linked device but no messages arriving."** The adapter polls every 2s by default; first message can take that long. Check `docker logs signal-adapter` for errors. The daemon URL the adapter uses is the docker-internal `http://signal-cli-rest-api:8080` (NOT `localhost:8084` — that's only for your browser).
- **"Adapter restarted, lost the linked session."** The daemon's session lives in a Docker volume (`signal-cli-data`). It survives adapter restarts. If the daemon container is recreated without the volume, the link is lost — re-run step 2.
- **"I want to send media."** v0.5 is text-only. Media support is a v0.5.x follow-up.
