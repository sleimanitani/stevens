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

## Up next

[`plans/v0.1-sec.md` step 7](plans/v0.1-sec.md#step-7--end-to-end-wiring-manual-run-) — End-to-end wiring (Docker image, compose service, `__main__` entrypoint, dev keypair generator for manual verification from the host).

## Blockers

None.

## Open decisions

None active.

- Charter-level security decisions locked 2026-04-22 — see `STEVENS.md` §3.11.
- Open architectural/memory questions tracked in `STEVENS.md` §7 — not blocking current work.

## Housekeeping (non-blocking)

- Local git `user.email` unset; currently passed as `git -c user.email=s@y76.io ...` per commit. Sol can set permanently whenever.
- `assistant_prd_trd.docx` (repo root) and `docs/prd.docx` appear to duplicate. Dedupe when convenient.
