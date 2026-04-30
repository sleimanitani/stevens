# Stevens — current status

*One-page snapshot. Updated every commit. Start here in a fresh session.*

**Active milestone:** `v0.3-installer-and-approvals` — approvals primitive (per-call + standing, orthogonal predicates) + installer agent. Tesseract install is the integration test.
**Active Build Plan:** [`plans/v0.3-installer-and-approvals.md`](plans/v0.3-installer-and-approvals.md)
**Predecessors (complete):**
- [`plans/v0.1-sec.md`](plans/v0.1-sec.md) — security foundation, `133dd78`.
- [`plans/v0.1.6-ergonomics.md`](plans/v0.1.6-ergonomics.md) — operator CLI surface, `9b32865`.
- [`plans/v0.2-skills.md`](plans/v0.2-skills.md) — shared skills layer, `d03a547`.
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
| 2026-04-30 | *(pending)* | `v0.3-installer-and-approvals` shipped — approvals primitive (per-call queue + standing approvals with orthogonal predicates); `apt` mechanism + `system.*` capabilities (`read_environment`, `plan_install`, `execute_privileged`, `write_inventory`); installer agent; `stevens approval` + `stevens dep` CLI handlers; e2e BLOCKED → approve → replay → ok and standing-approval silent execute paths green; 389/389 tests pass (1 skipped) |

## Up next

**v0.1-sec is functionally complete**, and **v0.1.6-ergonomics** shipped a low-overhead operator surface so per-channel + per-agent onboarding is one command + one browser consent.

Remaining before first real email flows (each step is now a single command):
1. Sol does the one-time Google Cloud Console setup (project, enable Gmail/Calendar/Pub/Sub APIs, OAuth consent screen → External + Production, create Desktop OAuth client, link billing). No CLI shortcut for this — Google has no API.
2. `uv run stevens secrets init` — initialize sealed store.
3. `uv run stevens passphrase remember` — opt-in: store passphrase in OS keyring so future calls are silent.
4. `uv run stevens onboard gmail --client-json ~/Downloads/client_secret_X.json --id gmail.personal -- --name "Sol personal"` — ingests OAuth client (and shreds source) the first time, runs the per-account browser flow.
5. `uv run stevens agent provision email_pm --preset email_pm` — keypair + register + apply `gmail.*`+`calendar.*` allow rules + write env profile.
6. `docker compose up -d` then `uv run stevens agent run email_pm`.
7. Send a test email; verify with `uv run stevens audit tail -f` — follow the acceptance checklist in `plans/v0.1-sec.md` step 22.

Run `uv run stevens doctor` at any point for a diagnostic with one-line remediations.

Future (not blocking the first run):
- WhatsApp Baileys adapter (TypeScript) — still stubbed; for personal numbers only. The Python WhatsApp Cloud API adapter (`channels/whatsapp-cloud/`) is now shipped for business numbers.
- Agent runtime integration with Langfuse + redactor wiring.
- Subject agents (Berwyn, etc. — v0.2 per PRD).

Onboarding procedures (all documented per-channel):
- Gmail: `docs/runbooks/gmail-oauth-setup.md`.
- WhatsApp Cloud: Meta Business dashboard → access token → `stevens secrets add whatsapp_cloud.app_secret` + `uv run python -m whatsapp_cloud_adapter.add_account --access-token-stdin ...` per phone.
- Calendar: same OAuth flow shape as Gmail (store `calendar.oauth_client.id/secret` in sealed store → `uv run python -m calendar_adapter.add_account --id calendar.personal ...`).

## Housekeeping (non-blocking)

- Local git `user.email` unset; currently passed as `git -c user.email=s@y76.io ...` per commit. Sol can set permanently whenever.
- `assistant_prd_trd.docx` (repo root) and `docs/prd.docx` appear to duplicate. Dedupe when convenient.
- google.api_core emits a FutureWarning that Python 3.10 support ends 2026-10-04. If Stevens is still on 3.10 by then, bump `requires-python`.

## Blockers

None.

## Open decisions

None active.

- Charter-level security decisions locked 2026-04-22 — see `STEVENS.md` §3.11.
- Open architectural/memory questions tracked in `STEVENS.md` §7 — not blocking current work.
