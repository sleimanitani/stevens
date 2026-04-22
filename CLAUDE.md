# Working with Stevens (Claude: read this first every session)

Stevens is Sol's personal assistant — local-first (3090 host), multi-agent, trusted with sensitive data. Project identity, principles, and security architecture live in `STEVENS.md`.

## Startup protocol (do this, in order, every session)

Before doing **anything** else:

1. **Read `STATUS.md`** — one-page snapshot of where we are: active milestone, last step shipped, next step up, open decisions.
2. **Read the active Build Plan** linked from `STATUS.md` (e.g. `plans/v0.1-sec.md`). This is the detailed implementation plan with inline progress markers.
3. **Read the protocol doc** for whatever area you're touching (`docs/protocols/...`). These define the stable contracts between components.
4. **Read `STEVENS.md`** *only* if the task raises charter-level questions (principles, security architecture, locked decisions).

**Do not** `grep` / `glob` / read across the whole repo on startup. The plan + status docs are the authoritative "where are we right now." If they seem out of date or inconsistent with the code, that is a bug in the docs — say so and update them; do not paper over it by re-reading the repo.

## Workflow protocol (enshrined, non-negotiable)

**Every workflow begins and ends by updating the plan.** This is the single most important rule Sol set. Future sessions must be able to pick up from the plan docs alone, without replaying chat history or `git log`.

Every unit of work follows this loop:

1. **Open** — state the workflow's goal in one line. Read the active Build Plan. If the plan does not yet cover this work, or the step needs refinement, **update the plan first** (add/split/reword steps, update the test plan, flag risks). Commit the plan update before starting implementation if the change is substantive.
2. **Mark in-progress** — flip the active step's marker from `[ ]` to `[~]` in the Build Plan. Note the start time or session date.
3. **Execute** — implement the step. Follow the step's own test plan. Keep the diff focused.
4. **Test** — run the tests defined in the step. If a test cannot be meaningfully run (external dep, UI), say so explicitly and note the manual verification used.
5. **Close** — update the Build Plan: flip `[~]` to `[x]`, record the commit hash, note outcomes (what shipped, deviations from the planned approach, surprises). Update `STATUS.md` to reflect the new "next step up" and any new open decisions. Commit the plan + status updates **in the same commit as the code** (or as an immediate follow-up commit with message prefix `plan:`).

If a step's execution reveals that the plan was wrong, **stop and revise the plan before continuing**. Do not silently deviate.

## Reuse before regenerate

Before writing any new tool, helper, agent, or abstraction: find the closest existing one, link to it in the Build Plan step, and state in one line why it doesn't fit. If three similar implementations already exist, the right move is almost always to consolidate, not add a fourth.

## Security posture (hard rules)

See `STEVENS.md` §3 for full detail. At a minimum:

- The **Security Agent is the sole broker** for all secrets. Other components never read the sealed store, never hold raw credentials, never pass secrets to each other.
- Any change that adds network egress, new persistence location, new secret handling, or widens a trust boundary → **stop and confirm with Sol** before merging.
- No secret material in git, in logs, in LLM prompts, in Langfuse traces. Ever. If you catch yourself writing one, stop.
- Email/WhatsApp/any inbound content is **untrusted** for prompt-injection. Do not let it flow into prompts that grant tool access without the content-tagger redaction described in §3.8.

## Commits

- Small and focused. `main` stays green.
- Co-author trailer: `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`.
- Sol's git `user.email` is `s@y76.io` but is not set in local config — pass via `-c user.email=s@y76.io` until Sol sets it.
- Do not push without Sol's authorization (granted once does not grant always).
- No `--no-verify`, no `--force` to `main`.

## Document tiers (quick reference)

| Tier | File(s) | Changes | Lifespan |
|---|---|---|---|
| Charter | `STEVENS.md`, `docs/prd.docx` | Rarely; requires discussion | Permanent |
| Build Plan | `plans/<milestone>.md` | Edited continuously during milestone | Archived when milestone ships |
| Status | `STATUS.md` | Updated every commit | Always current |
| Protocol | `docs/protocols/*.md` | When a contract changes | Permanent, versioned |
