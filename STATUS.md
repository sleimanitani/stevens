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

## Up next

**v0.10-bootstrap** is the active milestone — see `plans/v0.10-bootstrap.md`. Goal: drop docker from the documented install path, replace with native Postgres + systemd user units + a single `stevens bootstrap` command. Ten steps; acceptance gate is "fresh box → Stevens up in under 5 minutes with one sudo line and no docker."

After v0.10:
- **v0.11-plugins** — channels + Mortals as entry-point plugins (`stevens channels install <name>`, `stevens hire spawn <spec>`). Existing channels and Mortals migrate from in-tree directories into per-plugin packages under `plugins/`.
- **v0.12-pantheon-expansion** — Mnemosyne (memory + pgvector) and Iris (user-facing persona) join the Pantheon. Mortals get clean memory and dialogue surfaces instead of inventing them per-Mortal.

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
