# Runbook — Signal

> **v0.10 transitional note:** the `signal-cli-rest-api` daemon below still
> runs in docker. The Signal-side native install (apt-installable
> `signal-cli` daemon under a systemd user unit) lands in a follow-up step
> after the v0.10 acceptance gate. Until then, every `docker compose ...`
> command below is run *from the `dev/` directory* — i.e. `cd dev/`
> first, then the `docker compose` command. The rest of Stevens (Postgres,
> Enkidu, the signal-adapter itself) does not need docker —
> `stevens bootstrap` installs and runs them natively.

End state: each Signal phone you onboard publishes inbound message events to the bus (DMs and groups), and agents can send replies. Stevens never touches Signal's encryption directly — the `signal-cli-rest-api` daemon owns the linked Signal session.

## Goal

- One or more Signal phones linked as devices to your Signal account(s).
- Phone + daemon URL stored under `signal.<account_id>.{phone,daemon_url}` in the sealed store.
- A `channel_accounts` row per account with `channel_type='signal'`.
- The signal-adapter container polling the daemon and publishing events.

## Prerequisites

- A real Signal account (the Signal app installed on a real phone — Signal links additional devices to a phone account; it doesn't sign up new accounts via the daemon).
- Stevens already installed: `uv run stevens bootstrap` succeeded, sealed store initialized. The signal-adapter itself runs as the systemd user unit `stevens-signal-adapter`; start it with `systemctl --user start stevens-signal-adapter`.
- Docker available **for the `signal-cli-rest-api` daemon only**, run from `dev/`. Native apt+systemd install for `signal-cli` itself is queued as a v0.10 follow-up — until then, this is the one place in Stevens that still touches docker.
- The `signal-cli-rest-api` service brought up via the dev compose path: `cd dev/ && docker compose up -d signal-cli-rest-api` (port 8084 on host, 8080 inside the docker network).

## Steps

```bash
# 1. bring the signal-cli-rest-api daemon up
(cd dev/; docker compose) up -d signal-cli-rest-api

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

# 4. start the signal-adapter for this account. With the v0.10 native
#    install, the adapter runs as a systemd user unit. Set the per-account
#    env vars in ~/.config/stevens/env (or a drop-in override under
#    ~/.config/systemd/user/stevens-signal-adapter.service.d/), then:
cat >> ~/.config/stevens/env <<'ENV'
SIGNAL_ACCOUNT_ID=signal.personal
SIGNAL_PHONE=+15555551234
SIGNAL_DAEMON_URL=http://localhost:8084
ENV
systemctl --user restart stevens-signal-adapter
journalctl --user -u stevens-signal-adapter -f   # watch it come up
```

(One adapter instance per phone is the simplest pattern. For multi-phone,
duplicate the unit file under a per-account name and adjust the env vars
— there's no built-in multi-account dispatcher in the adapter today.)

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

- **"Daemon not reachable at http://localhost:8084."** Container not up, or compose port binding mismatch. `cd dev/ && docker compose ps signal-cli-rest-api` should show it running. The daemon listens on port 8080 internally; compose maps it to host 8084.
- **"QR code not scanning."** PNG might be too small for your phone's camera. Open it on a larger display (laptop screen). Or open the daemon URL directly in a browser: `http://localhost:8084/v1/qrcodelink/Stevens?number=+15555551234` — it returns the same PNG.
- **"Linked device but no messages arriving."** The adapter polls every 2s by default; first message can take that long. Check `journalctl --user -u stevens-signal-adapter` for errors. The daemon URL the adapter uses comes from `SIGNAL_DAEMON_URL` in `~/.config/stevens/env` — usually `http://localhost:8084` (the host-side port the dev compose file maps).
- **"Adapter restarted, lost the linked session."** The daemon's session lives in a Docker volume (`signal-cli-data`). It survives adapter restarts. If the daemon container is recreated without the volume, the link is lost — re-run step 2.
- **"I want to send media."** v0.5 is text-only. Media support is a v0.5.x follow-up.
