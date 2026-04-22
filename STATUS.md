# Stevens — current status

*One-page snapshot. Updated every commit. Start here in a fresh session.*

**Active milestone:** `v0.1-sec` — Security Agent foundation
**Active Build Plan:** [`plans/v0.1-sec.md`](plans/v0.1-sec.md)
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
| 2026-04-22 | *(this commit)* | Gmail adapter — real `add_account` OAuth flow, real `/gmail/push` handler (broker-mediated), real `watch_renew`; added `gmail.list_history`, `gmail.get_message`, `gmail.watch`, `gmail.get_profile` capabilities; 168/168 tests pass |

## Up next

**v0.1-sec is functionally complete** and the **Gmail adapter** is fully implemented (real OAuth flow, real Pub/Sub push handler, real watch renewal — all broker-mediated, adapter holds zero OAuth tokens).

Remaining before first real email flows:
1. Sol follows `docs/runbooks/gmail-oauth-setup.md` — one-time OAuth client setup.
2. Sol runs `uv run python -m gmail_adapter.add_account --id gmail.personal --name "Sol personal"` per Gmail account — browser OAuth.
3. Sol adds an `email_pm` entry with `gmail.*` allow rules to `security/policy/capabilities.yaml`, generates email_pm's keypair (`uv run python security/scripts/gen_test_keypair.py email_pm`), registers the pubkey (`uv run stevens agent register email_pm --pubkey-file ...`).
4. Start the stack: `docker compose up -d`, then the agents runtime with env `STEVENS_CALLER_NAME=email_pm STEVENS_PRIVATE_KEY_PATH=...`.
5. Send a test email — follow the acceptance checklist in `plans/v0.1-sec.md` step 22.

Future (not blocking the first run):
- WhatsApp adapter (TypeScript/Baileys) — still stubbed, needs the same broker-mediated rewrite.
- Agent runtime integration with Langfuse + redactor wiring.
- Calendar adapter (v0.3 per PRD).
- Subject agents (Berwyn, etc. — v0.2 per PRD).

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

## Housekeeping (non-blocking)

- Local git `user.email` unset; currently passed as `git -c user.email=s@y76.io ...` per commit. Sol can set permanently whenever.
- `assistant_prd_trd.docx` (repo root) and `docs/prd.docx` appear to duplicate. Dedupe when convenient.
