# Stevens — current status

*One-page snapshot. Updated every commit. Start here in a fresh session.*

**Active milestone:** `v0.10-bootstrap` — drop docker as the documented install path, native Postgres + systemd user units, one-command `stevens bootstrap`. Driven by the 2026-05-02 realization that `docker` group membership is functionally passwordless root and is incompatible with running AI agents on the host (locked as STEVENS.md §2 Principle 14).
**Active Build Plan:** [`plans/v0.10-bootstrap.md`](plans/v0.10-bootstrap.md)
**Queued:** v0.11-plugins (channels + Mortals as entry-point plugins; STEVENS.md §2 Principle 13) · v0.12-pantheon-expansion (Mnemosyne + Iris) · v0.5.1-slack · v0.5.2-discord · v0.5.3-telegram · v0.5.4-imessage (these become individual plugins under the v0.11 model rather than in-tree code).
**Architecture framing:** Pantheon vs Mortals — `docs/architecture/pantheon.md` (uploaded by Sol 2026-05-02). Ratified into STEVENS.md §1.1 + Principles 12–14. Pantheon today: Enkidu, Arachne, Sphinx, Janus. Pantheon planned: Mnemosyne (memory, v0.12), Iris (interface, v0.12).
**Predecessors (complete):**
- [`plans/v0.1-sec.md`](plans/v0.1-sec.md) — security foundation, `133dd78`.
- [`plans/v0.1.6-ergonomics.md`](plans/v0.1.6-ergonomics.md) — operator CLI surface, `9b32865`.
- [`plans/v0.2-skills.md`](plans/v0.2-skills.md) — shared skills layer, `d03a547`.
- [`plans/v0.3-installer-and-approvals.md`](plans/v0.3-installer-and-approvals.md) — approvals primitive + installer, `c2f0929`.
- [`plans/v0.3.1-web.md`](plans/v0.3.1-web.md) — Arachne + network.fetch/search, `8d1f64a`.
- [`plans/v0.3.2-postgres.md`](plans/v0.3.2-postgres.md) — Postgres wiring, `902f0d8`.
- v0.4-sphinx, v0.4.1-channels-framework, v0.4.x-injection-guard — see commit log.
- v0.5-signal — Signal channel adapter shipped.
- v0.6-google-wizard — `stevens wizard google` shipped.
- v0.7-janus — Janus operator-assisted browser onboarder shipped.
- v0.8-reset — `stevens reset` for fresh-install testing shipped.
- v0.9-runbooks — per-channel runbooks + `stevens channels list` shipped.
**Charter:** [`STEVENS.md`](STEVENS.md) · PRD: [`docs/prd.docx`](docs/prd.docx)

## Last shipped

| Date | Commit | What |
|---|---|---|
| 2026-04-22 | `c8fd584` | v0.0 baseline — existing scaffolding + STEVENS.md charter + locked security decisions |
| 2026-04-22 | `8172da9` | Plan/status discipline + `v0.1-sec` build plan laid down |
| 2026-04-22 | `18e3ded` | `v0.1-sec` step 1 — `security/` package scaffolding; smoke tests pass; dropped to Python 3.10 |
| 2026-04-22 | `e19a6b1` | `v0.1-sec` step 2 — UDS server shell + msgpack framing; 18/18 tests pass |
| 2026-04-22 | `6aa443f` | `v0.1-sec` step 3 — Ed25519 identity + canonical msgpack + nonce replay; 49/49 tests pass; protocol doc `docs/protocols/security-agent.md` shipped |
| 2026-04-22 | `47daeef` | `v0.1-sec` step 4 — Policy loader + evaluator (default-deny, account-scope wildcards, deny-overrides-allow); 69/69 tests pass |
| 2026-04-22 | `3237c62` | `v0.1-sec` step 5 — Audit writer (JSONL, daily rollover, asyncio-locked, sensitive-param hashing, file mode 0o600); 82/82 tests pass |
| 2026-04-22 | `d23a2a4` | `v0.1-sec` step 6 — Capability registry + `ping` + dispatch orchestration; first end-to-end through UDS works; 101/101 tests pass |
| 2026-04-22 | `5bb044a` | `v0.1-sec` step 7 — Docker + compose + `__main__` entrypoint + dev keypair gen; security service isolated (`network_mode: none`); Docker build declared manual |
| 2026-04-22 | `79c3a3d` | `v0.1-sec` step 8 — `shared.security_client` library + canonical encoder moved to `shared/`; 108/108 tests pass |
| 2026-04-22 | `8dc5ec2` | `v0.1-sec` steps 9-12 (merged) — sealed secret store (Argon2id KDF, libsodium secretbox, rotation/revocation); 133/133 tests pass |
| 2026-04-22 | `0a65504` | `v0.1-sec` steps 13-14 — stevens admin CLI (secrets init/add/list/rotate/revoke/delete + agent register); 143/143 tests pass |
| 2026-04-22 | `c99f83b` | `v0.1-sec` steps 15-17 — Outbound sidecar (httpx) + Gmail capabilities + CapabilityContext; 149/149 tests pass |
| 2026-04-22 | `802bebe` | `v0.1-sec` steps 18-21 — credentials_ref migration + OAuth-setup runbook + tool_factory rewrite (broker-mediated) + Langfuse redactor; 168/168 tests pass; step 22 (E2E) declared manual |
| 2026-04-22 | `935c115` | Gmail adapter — real `add_account` OAuth flow, real `/gmail/push` handler (broker-mediated), real `watch_renew`; added `gmail.list_history`, `gmail.get_message`, `gmail.watch`, `gmail.get_profile` capabilities; 168/168 tests pass |
| 2026-04-23 | `133dd78` | WhatsApp Cloud API adapter + Google Calendar adapter — both broker-mediated, same pattern as Gmail; migration 003 adds `whatsapp_cloud` channel_type; new `CalendarEventChangedEvent` schema; 184/184 tests pass |
| 2026-04-29 | `9b32865` | `v0.1.6-ergonomics` shipped — Enkidu naming convention; policy presets; `stevens onboard / agent provision / agent run / passphrase remember / audit tail / doctor / status`; 241/241 tests pass |
| 2026-04-30 | `d03a547` | `v0.2-skills` shipped — `skills/` package (tools + playbooks separated); `propose_skill` + `scripts/review_skills.py`; PDF reader (acceptance gate, 3/4 cases — OCR skipped without tesseract); Email PM rewired through registry with 6 starter playbooks; STEVENS.md skills-vs-capabilities boundary documented; 297/297 tests pass (1 skipped) |
| 2026-04-30 | `fb47326` | docs — three new architecture/protocol docs (agent-isolation, approvals, privileged-execution) + STEVENS.md §1.1 (Names) and §3.13 (Approval gates) |
| 2026-04-30 | `c2f0929` | `v0.3-installer-and-approvals` shipped — approvals primitive (per-call queue + standing approvals with orthogonal predicates); `apt` mechanism + `system.*` capabilities (`read_environment`, `plan_install`, `execute_privileged`, `write_inventory`); installer agent; `stevens approval` + `stevens dep` CLI handlers; e2e BLOCKED → approve → replay → ok and standing-approval silent execute paths green; 389/389 tests pass (1 skipped) |
| 2026-04-30 | `8d1f64a` | `v0.3.1-web` shipped — **Arachne** async-path web agent + `network.fetch` / `network.search` capabilities + modular search backend (Brave default) + in-memory TTL cache + per-domain rate limiter + `web_fetch`/`web_search` skills + PDF corpus regression script. Cache-sharing future shape and Browser Harness reference noted in `docs/architecture/agent-isolation.md`. 446/446 tests pass (1 skipped) |
| 2026-04-30 | `902f0d8` | `v0.3.2-postgres` shipped — production wiring for the v0.3 primitives: Postgres-backed `ApprovalStore`, `PlanStore`, `Inventory`; `__main__` selects Postgres when `$DATABASE_URL` is set; `stevens approval` / `stevens dep` CLI wired through Postgres; `_admin.refresh_approvals` / `_admin.mark_request_approved` capabilities; `scripts/db_migrate.sh` runner. Operator unblock for first real installer run. 446/446 tests pass (1 skipped) |
| 2026-05-02 | `d77cfa4` | `plan:` Pantheon/Mortals architecture ratified — `docs/architecture/pantheon.md` adopted; STEVENS.md §1.1 rewritten + Principles 12–14 added; agent-isolation.md revised with Mortal lifecycle + capability-grant-width split; v0.10-bootstrap, v0.11-plugins, v0.12-pantheon-expansion plans drafted. No code change. |
| 2026-05-02 | `f37231a` | `v0.10` step 1 — `psql`-free migrate script. New `stevens_security.bootstrap.migrate` Python module replaces the shell-out; `scripts/db_migrate.sh` is now a 4-line `exec uv run python -m` wrapper. Verified end-to-end against the freshly-installed native Postgres 16 + pgvector on this box (docker fully removed, `engineer` no longer in `docker` group). 609/609 tests pass (3 skipped). |
| 2026-05-02 | `5bc371a` | `v0.10` step 2 — native Postgres detector + provisioner. New `stevens_security.bootstrap.postgres` module: `detect()` probes psql/pg_isready/dpkg/pgvector pkg + psycopg-probes the assistant role/DB/extension; `install_instructions()` returns the exact multi-line sudo block per platform (debian/macos/windows) or `None` when ready; `ensure_role_and_database()` idempotently creates the role+DB+`CREATE EXTENSION vector` via peer auth; `write_env_file()` writes `~/.config/stevens/env` at 0600. CLI: `python -m stevens_security.bootstrap.postgres [--ensure] [--write-env]`. End-to-end verified on this box. 645 passed, 2 skipped (+35 unit + 1 integration test). |
| 2026-05-02 | _pending_ | `v0.10` step 5 — docker-group refusal in doctor. New `stevens_security.bootstrap.preflight` module (shared detector); `stevens doctor` gains a `docker-group` check (info=True warning, doesn't fail the report); bootstrap retains hard-fail. Bonus cleanup: `doctor`'s `enkidu-running` remediation now points at `systemctl --user start stevens-security` instead of `docker compose`. 693 passed, 2 skipped. |
| 2026-05-02 | `c7f7fc9` | `v0.10` step 4 — `stevens bootstrap` CLI. New `stevens_security.bootstrap.cli_bootstrap` module: `preflight()` (Python, uv, **not in docker group** per Principle 14), `run_bootstrap(*, dry_run, repo_root)` orchestrates Steps 1+2+3 into a single one-command flow with rc=0/1/2 semantics (ready / operator-action-needed / hard-fail). Wired into top-level CLI as `stevens bootstrap [--dry-run] [--repo-root P]`. End-to-end verified on this box. 687 passed, 2 skipped (+16 unit tests). |
| 2026-05-02 | `372d875` | `v0.10` step 3 — systemd user-unit generator. New `stevens_security.bootstrap.systemd` module: `DEFAULT_SERVICES` catalog (security + gmail/calendar/whatsapp-cloud/signal adapters + agents runtime); `render_unit()` pure renderer; `write_units()` idempotent (created/updated/unchanged); `is_lingering()` + `enable_linger_command()` for the one-time `loginctl enable-linger` grant; `reload_user_daemon()` for `systemctl --user daemon-reload`. CLI: `python -m stevens_security.bootstrap.systemd [--write]` defaults to dry-run. macOS/Windows raise `NotImplementedError` for now — Linux-first per the v0.10 plan. End-to-end verified: dry-run + `--write --target-dir /tmp/...` both work. 671 passed, 2 skipped (+26 unit tests). |

## Up next

**v0.10-bootstrap** is the active milestone — see `plans/v0.10-bootstrap.md`. Goal: drop docker from the documented install path, replace with native Postgres + systemd user units + a single `stevens bootstrap` command. Eight steps; acceptance gate is "fresh box → Stevens up in under 5 minutes with one sudo line and no docker."

**Step 1 shipped** (`f37231a`, 2026-05-02): `psql`-free migrate script via new `stevens_security.bootstrap` subpackage.

**Step 2 shipped** (`5bc371a`, 2026-05-02): native Postgres detector + provisioner module.

**Step 3 shipped** (`372d875`, 2026-05-02): systemd user-unit generator.

**Step 4 shipped** (`c7f7fc9`, 2026-05-02): `stevens bootstrap` CLI orchestrator.

**Step 5 shipped** (2026-05-02, this commit): docker-group refusal in `stevens doctor` + shared `bootstrap.preflight` module. Bonus: doctor's `enkidu-running` remediation switched from `docker compose` to `systemctl --user start stevens-security`.

**Next: step 6** — relocate `compose.yaml` → `dev/compose.yaml`. Add a tiny `dev/README.md` explaining "this is the developer compose path; production install uses `stevens bootstrap`." Update README + any top-level docs that point at the file. Trivial — almost all the work is in step 7 (the runbook overhaul that uses the new `stevens bootstrap` flow).

After v0.10:
- **v0.11-plugins** — channels + Mortals as entry-point plugins (`stevens channels install <name>`, `stevens hire spawn <spec>`). Existing channels and Mortals migrate from in-tree directories into per-plugin packages under `plugins/`.
- **v0.12-pantheon-expansion** — Mnemosyne (memory + pgvector) and Iris (user-facing persona) join the Pantheon. Mortals get clean memory and dialogue surfaces instead of inventing them per-Mortal.

## Host state — engineer@Leopard3090 (Sol's dev box)

For session resumption: this box is where Stevens runs in development. Current state as of 2026-05-02:

- **Docker:** fully removed (`apt purge`d). `engineer` is no longer in the `docker` group. Compose plugin removed. This is by design — STEVENS.md §2 Principle 14 forbids docker-group membership for any account that runs Stevens (it's functionally passwordless root).
- **Postgres:** native install via PGDG apt repo. Postgres 16 + `postgresql-16-pgvector` 0.8.2. systemd unit `postgresql@16-main` running. Peer auth as `engineer` (which is a SUPERUSER). Database `assistant` owned by `engineer`, `vector` extension created.
- **`DATABASE_URL`:** `postgresql:///assistant` (empty host = unix socket = peer auth, no password). Persisted in `~/.bashrc`.
- **Stevens migrations:** all 9 applied against the `assistant` DB. `stevens secrets init` not yet run (no sealed store, no agent keypairs registered, no channels onboarded). The box is at "fresh after migrations" state — perfect for testing the rest of v0.10's bootstrap flow.
- **`compose.yaml`:** still in repo root. Will move to `dev/compose.yaml` in v0.10 step 6.

Onboarding procedures (current; **will be replaced by `stevens channels install <name>` in v0.11**):
- Gmail: `docs/runbooks/gmail.md`.
- Calendar: `docs/runbooks/calendar.md`.
- WhatsApp Cloud: `docs/runbooks/whatsapp-cloud.md`.
- Signal: `docs/runbooks/signal.md`.

## Housekeeping (non-blocking)

- Local git `user.email` unset; currently passed as `git -c user.email=s@y76.io ...` per commit. Sol can set permanently whenever.
- `assistant_prd_trd.docx` (repo root) and `docs/prd.docx` appear to duplicate. Dedupe when convenient.
- google.api_core emits a FutureWarning that Python 3.10 support ends 2026-10-04. If Stevens is still on 3.10 by then, bump `requires-python`.

## Blockers

None.

## Open decisions

None blocking.

- Charter-level security decisions locked 2026-04-22 — see `STEVENS.md` §3.11.
- Pantheon/Mortals tier model + no-passwordless-root-equivalent locked 2026-05-02 — see STEVENS.md §1.1, §2 Principles 12–14, `docs/architecture/pantheon.md`.
- Open architectural/memory questions tracked in `STEVENS.md` §7 — not blocking current work.
- Open questions per upcoming milestone live in their respective plan files (v0.10/v0.11/v0.12 each have an "Open questions" section).
