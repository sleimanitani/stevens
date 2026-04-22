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

## Up next

[`plans/v0.1-sec.md` step 3](plans/v0.1-sec.md#step-3--identity-module-ed25519-verification-) — Identity module (Ed25519 verification + canonical msgpack encoder + agent pubkey registry).

## Blockers

None.

## Open decisions

None active.

- Charter-level security decisions locked 2026-04-22 — see `STEVENS.md` §3.11.
- Open architectural/memory questions tracked in `STEVENS.md` §7 — not blocking current work.

## Housekeeping (non-blocking)

- Local git `user.email` unset; currently passed as `git -c user.email=s@y76.io ...` per commit. Sol can set permanently whenever.
- `assistant_prd_trd.docx` (repo root) and `docs/prd.docx` appear to duplicate. Dedupe when convenient.
