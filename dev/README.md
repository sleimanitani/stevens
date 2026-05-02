# `dev/` — developer-only paths

This directory holds tooling that is **not** part of the canonical install
flow. The canonical install is `demiurge bootstrap` (native Postgres +
systemd user units, no docker, no `usermod -aG docker`).

## What's in here

### `compose.yaml`

The original docker-compose file, kept for developers who prefer the
container path. **Production install does not use this.**

Why we kept it instead of deleting:

- Useful for testing in CI without provisioning a full systemd environment.
- Useful for spinning up the Langfuse observability stack alongside
  Demiurge (Langfuse is *not* part of `demiurge bootstrap` — it's a
  developer-time concern).
- Easy reference when the `bootstrap.systemd` unit catalog needs to add
  a new service: cross-check it against the compose service list.

### Caveat — `docker` group is passwordless root

Running this `compose.yaml` requires the operator to either be in the
`docker` group or to use rootless docker. **The `docker` group is
functionally passwordless root** (you can mount `/` into a container and
chroot in as root): see DEMIURGE.md §2 Principle 14.

For that reason, this file is for developers who already understand this
trade-off and choose to make it on a development box that is *not* the
account where they run Demiurge against real data. On the production /
operator account, use `demiurge bootstrap`.

## Migrating from `docker compose` to `demiurge bootstrap`

If you've been running Demiurge via this compose file and want to move to
the native install path:

```bash
# 1. Stop containers
cd dev/ && docker compose down

# 2. Leave the docker group (otherwise bootstrap will refuse to run)
sudo gpasswd -d $USER docker && newgrp $(id -gn)

# 3. Native install. bootstrap prints the one sudo block needed.
uv run demiurge bootstrap

# 4. After running the printed sudo line(s), re-run bootstrap to finish:
uv run demiurge bootstrap

# 5. Bring up Demiurge services via systemd user units:
systemctl --user start demiurge-security
systemctl --user start demiurge-gmail-adapter   # …etc, per channel
```

The data lives on. The native Postgres reads the same migrations
(`resources/migrations/*.sql`) and the same sealed-store directory
(default `/var/lib/demiurge/secrets`). What changes is the runtime — the
services run as you, under your systemd user instance, with no docker.
