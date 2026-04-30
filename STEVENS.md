# STEVENS

> **Status:** Draft v0 — architectural charter. Living document.
> **Owner:** Sol.
> **Scope:** supersedes conflicting details in `docs/prd.docx` v0.1 where called out; otherwise the PRD remains the operational spec.

Stevens is a personal assistant that runs locally on Sol's hardware (3090 GPU, Docker host). Named after the butler in *The Remains of the Day*. Role: chief of staff, butler, researcher. Will be trusted with credit cards, tax information, credentials, and other highly sensitive personal data — so security, context/memory management, and reuse of proven tools are first-class design concerns, not later hardening passes.

This document defines:

1. The system's identity and guiding principles
2. Architectural dimensions (channels, agents, tools, skills, templates, security)
3. **Detailed security architecture** (§3)
4. Outlines for the other dimensions, to be filled in next
5. How we work: iteration, versioning, testing, and open decisions

---

## 1. Identity

- **System name:** Stevens.
- **User-facing persona:** embodied by the UI agent. All user-visible dialogue signs as Stevens, regardless of which internal agent did the work.
- **Design stance:** small agents, shared infrastructure, many git checkpoints, reuse over rewrite, explicit trust boundaries. Every new capability should *reduce* the marginal cost of the next capability.

### 1.1 Names

Internal agents get human-readable names alongside their snake_case code identifiers. Names are display-only — they appear in logs, CLI banners, audit summaries, and docs. Code identifiers (`security_agent`, `email_pm`, container names, socket paths, capability allow rules) are **not** renamed; that would be churn for no functional benefit and would muddy the audit trail across the rename.

Convention: pick a name from a coherent pantheon — currently mythological, with **Enkidu** (companion to Gilgamesh, sole keeper of the keys) for the Security Agent. Future agents: pick from the same source so names stay legible as a set.

| Code identifier | Display name | Role |
|---|---|---|
| `security_agent` | **Enkidu** | sole broker for secrets and sensitive operations (§3) |
| `email_pm` | *(unnamed)* | inbox triage agent |
| `installer` | *(unnamed)* | system-package installer; proposes plans for Enkidu to execute |
| `web` | **Arachne** | weaver of fetches and searches — async-path web agent (v0.3.1). Greek mythology: mortal weaver who challenged Athena and was transformed into a spider; the spider/weaver imagery maps to crawlers/searchers. |
| `interface` (v0.2+) | *(unnamed)* | the agent that talks to Sol — uses the SOUL.md pattern |
| subject agents (`berwyn_deal`, etc.) | *(unnamed)* | cross-channel agents per topic |

When naming new agents: add a row here and a one-line justification (which character / why it fits). Don't rename code identifiers.

---

## 2. Guiding principles

Carried forward from PRD §1.2 (still authoritative):

1. Small agents, big system.
2. Channels are pipes, not agents.
3. Resources are managed, not embedded.
4. Cheap when possible, capable when needed (local Qwen3-30B default; Claude API when the agent decides).
5. Human-in-the-loop by default. No autonomous sending in v0.1.
6. Boundaries enable upgrades.

Added in this document:

7. **Security is a dimension, not a feature.** A dedicated Security Agent is the sole broker for all secrets and all sensitive actions. No other component reads secret material at rest or holds long-lived credentials. (§3)
8. **Reuse over regenerate.** Before any new tool, agent, or helper is written, we point to the closest existing thing and justify why it doesn't fit. Three similar implementations is a design smell.
9. **Testable or declared untestable.** Every change ships with a test plan; if a change can't be meaningfully tested (external API, UI), we say so out loud and compensate with manual verification steps and observability.
10. **Context and memory are load-bearing.** Stevens's long-term value compounds through what it remembers. Memory is structured, scoped, redacted, and auditable — not a pile of prompt strings. (§4, to be detailed.)
11. **Agents are narrow.** Each agent sees only what it strictly needs to do its job: its own tool list (filtered via `skills.registry`), its own playbooks, its own subscription topics, its own scoped DB rows. No agent has a broad system view. Cross-agent communication is the bus (asynchronous) or Enkidu (synchronous, brokered) — never direct imports. The blast radius of any single compromised agent is bounded by its narrow surface. Operationalized in `docs/architecture/agent-isolation.md`.

---

## 3. Security architecture (detailed)

### 3.1 Threat model

Stevens will hold:

- OAuth tokens for multiple Gmail accounts, Calendar, Drive, and other channels over time
- WhatsApp Baileys session state (device-level credential)
- Payment instruments (card PANs, billing addresses) in v0.2+
- Tax and financial documents
- Correspondence and calendar data for Sol and third parties
- API keys (Anthropic, future providers)
- Infrastructure secrets (Postgres, Langfuse)

Primary adversaries and failure modes we defend against:

- **Malicious or hijacked agent.** A compromised agent (prompt-injection via an incoming email, bad dependency, bug) must not be able to read other agents' secrets, exfiltrate credentials, or send messages autonomously.
- **Prompt injection inside legitimate content.** Email/WhatsApp bodies can contain adversarial instructions. These must never reach a component that holds secrets in a form that can be returned to the attacker.
- **Host compromise of a single container.** Blast radius must be limited to that container's capabilities.
- **Accidental logging / tracing of secrets.** Langfuse traces, stdout logs, LLM prompts must never contain raw secret values.
- **Operator error** — e.g. committing `.env`, cat'ing a credential into a prompt.

Explicitly *out of scope* for v0.1 (accepted risks, documented):

- Physical attacker with root on the host.
- Supply-chain compromise of base images / Python deps (mitigated only by pinning).

### 3.2 The Security Agent is the sole secret broker

One component — the **Security Agent** — owns every secret at rest and in memory. Everything else gets capabilities, not credentials.

Concretely:

- The Security Agent is the **only** process that can read the sealed secret store on disk.
- The Security Agent is the **only** process holding decrypted secret material in memory, and only for the duration of an in-flight request.
- All other agents and adapters reach the Security Agent through a defined RPC surface (see §3.4). They never read `./secrets/`, never decrypt anything, never call `channel_accounts.credentials` directly.
- Raw secrets must never appear in logs, Langfuse traces, LLM prompts, LLM responses, or git.

This is the non-negotiable architectural rule. Any design that routes secret material through a second component — "just this once" — must be rejected or explicitly escalated to Sol.

### 3.3 Capabilities model: act-on-behalf-of, not hand-me-the-key

Two request shapes are supported, in strict order of preference:

1. **`perform(capability, params) → result`** *(preferred)* — the Security Agent executes the sensitive operation itself and returns only the non-sensitive result. Examples:
   - `gmail.send_draft(account_id, draft_id)` — Security Agent loads the OAuth token, calls Gmail, returns `message_id`. The caller never sees the token.
   - `payments.charge(card_ref, amount, merchant)` — Security Agent loads the card, calls the processor, returns a receipt.
   - `anthropic.complete(redacted_prompt)` — Security Agent attaches the API key and forwards.
2. **`get_token(capability, ttl≤N) → short_lived_handle`** *(fallback)* — when the caller genuinely needs to drive a library that insists on holding a credential (e.g. the LangChain `GmailToolkit`), the Security Agent issues a short-lived, narrowly-scoped, single-account handle. Handles:
   - carry a TTL ≤ 15 minutes and a single account + scope
   - are opaque to the caller (not a raw OAuth token — a broker-side reference resolved on use via a sidecar proxy, see §3.4)
   - are bound to the caller's agent identity and revocable instantly by name

**Never** "hand the caller the raw OAuth token and hope." If a pattern in the current scaffolding does this (it does — `tool_factory.get_gmail_tools(account_id)` returns a toolkit bound to real credentials), that pattern gets rewritten in the security milestone below.

### 3.4 Isolation and transport

- The Security Agent runs in its own container, with its own filesystem, its own user, and no inherited env from the other services' `.env`.
- It exposes a gRPC or HTTP-over-UDS surface on a **Unix domain socket** bind-mounted only into callers that need it. No TCP port, no host network exposure.
- For capability shape (2), a **sidecar proxy** in the Security Agent's container terminates outbound calls (Gmail API, Anthropic API, etc.) and attaches the real credential there. The handle the caller holds is a short opaque ID that the sidecar resolves. This keeps raw tokens out of other containers even when an upstream SDK demands them.
- The container has read-only root fs except for its secret store volume, which is not mounted into any other container.

### 3.5 Identity, authentication, authorization

- **Identity.** Every caller (each agent, each adapter) runs as a distinct OS user inside its container and presents a signed agent identity — an Ed25519 keypair **generated per-install on first boot** and persisted to the caller's local state volume. The private key never leaves the host and is never embedded in a container image. On first boot, the agent hands its public key to the Security Agent; Sol acknowledges the registration once via `stevens agent register <name>` (trust-on-first-use gated by an explicit acknowledgement). Agents do not self-claim names; the Security Agent verifies the signature on every subsequent request.
- **Authorization.** A policy file (version-controlled, human-reviewable) maps `(agent_identity, capability, account_scope)` → `allow | deny`. Default deny. Example:
  ```yaml
  - agent: email_pm
    capabilities:
      - gmail.read:      { accounts: ["gmail.*"] }
      - gmail.label:     { accounts: ["gmail.*"] }
      - gmail.draft:     { accounts: ["gmail.*"] }
      - anthropic.complete: { max_tokens_per_day: 200000 }
    deny:
      - gmail.send
      - gmail.delete
  ```
- **Rate and budget limits** live in the same policy, enforced server-side. The Security Agent refuses, not the caller.

### 3.6 Audit log

- Every request to the Security Agent produces one append-only audit record: `timestamp, caller_identity, capability, param_hashes, account_id, outcome, latency_ms, rejection_reason?`.
- Parameter **hashes**, not raw values, for anything sensitive. Non-sensitive params (account_id, capability name) logged in clear.
- Log is WORM-style: append-only file + daily rollover + optional off-box replication. Readable by Sol via a `stevens audit` CLI.
- Any `deny` or `rate-limit` outcome raises an alert through the UI agent.

### 3.7 Secret lifecycle

- **Provisioning.** Secrets enter Stevens through `stevens secrets add <name>` CLI → prompts on TTY → writes sealed into the store. Never through `.env`, never via copy-paste into a file path, never through a prompt.
- **At rest.** libsodium secretbox per secret, keyed from a root key unlocked at Security Agent startup. Root key source for v0.1: local TPM-sealed blob, or passphrase entered at boot, or macOS/Linux keyring — **decide in §3.11 below**.
- **Rotation.** Each secret has a `rotate_at` target (e.g. 90 days for API keys, OAuth refresh tokens auto-rotate on use). Overdue rotations surface as UI-agent notifications.
- **Revocation.** `stevens secrets revoke <name>` invalidates immediately across the system — handles die, policy denies further issuance.
- **Deletion.** Tombstoned in the store; audit record retained.

### 3.8 LLM context and redaction

- No raw secret ever enters an LLM prompt. Ever.
- Any call that *does* need a secret to be present (e.g. asking the local model to help format an email that happens to contain Sol's address) runs inside the Security Agent's sidecar: the sidecar attaches the sensitive value, calls the model, redacts on the way out before returning.
- Inbound content (email bodies, WhatsApp messages) is treated as **untrusted user input** for prompt-injection purposes. Before any such content is fed to an LLM, a **content tagger** wraps it with a delimiter + instruction-inversion preamble, and the agent's system prompt is constructed so that instructions inside delimited content are ignored. This is defense-in-depth, not a guarantee.
- Langfuse trace payloads run through the same redactor before publish.

### 3.9 What lives where (ownership table)

| Thing | Owned by | Accessible to others? |
|---|---|---|
| `.env` for Postgres / Langfuse infra | compose-time only, not copied into Security Agent | No. Infra creds stay in infra. |
| OAuth tokens (Gmail, Calendar, Drive…) | Security Agent sealed store | Only via `perform(...)` or short-lived handle |
| WhatsApp Baileys session dir | Security Agent sealed volume, mounted read-only into WhatsApp adapter **only at runtime** via the sidecar proxy (if feasible); otherwise adapter runs *inside* the Security Agent isolation boundary | Treated as a secret |
| Payment instruments | Security Agent sealed store | Only via `payments.*` capabilities |
| Anthropic / other API keys | Security Agent sealed store | Only via `perform(...)` |
| User PII at rest (emails, calendar) | application Postgres | In-cluster only; outbound egress requires Security-Agent-issued capability |
| Audit log | Security Agent append-only volume | Read-only via `stevens audit` |

### 3.10 What this means for the existing scaffolding (migration)

The current `docs/prd.docx` v0.1 plan has several patterns that violate the rule above. They need to change **before we ship any agent that touches sensitive data**:

| Current (PRD v0.1) | Problem | New plan |
|---|---|---|
| `channel_accounts.credentials` JSONB readable by any DB user | Every service with the Postgres DSN can read OAuth tokens | Move credentials out of `channel_accounts` into the Security Agent's sealed store. `channel_accounts.credentials_ref` holds only a reference. |
| `./secrets/gmail_oauth_client.json` mounted into `gmail-adapter` | Adapter container holds client secrets on disk | Move to sealed store; `gmail-adapter` receives short-lived handles via sidecar. |
| `tool_factory.get_gmail_tools(account_id)` returns a toolkit bound to real OAuth creds inside the agent process | Raw tokens live inside the agent process memory | Toolkit's HTTP client is replaced with one that talks to the Security Agent's sidecar proxy — raw tokens never enter the agents container. |
| `LANGFUSE_*`, `POSTGRES_*` spread across `.env` and every service | Broad fan-out of infra creds | Keep for v0.1 (infra, not user secrets) but document the trust zone and plan to move to per-service secrets in v0.2. |
| PRD Appendix B: "Encryption at rest for credentials — deferred to v0.2" | Incompatible with the stated trust level | **Promoted to v0.1 blocker.** No real credentials land on disk unsealed. |
| Langfuse traces include full LLM prompts including tool I/O | Will leak secrets and PII | Trace publisher runs through redactor; sensitive tool arguments hashed. |

### 3.11 Security decisions (recorded 2026-04-22)

1. **Sequencing — security first.** Security Agent + sidecar + sealed store land before Email PM. Milestone label: v0.1-sec. Email PM gets built on top of the broker, not retrofitted. Rationale: retrofitting credential flows after agents already depend on them is painful and error-prone.
2. **Root key source — passphrase at boot.** Root key unlocked from a passphrase entered at Security Agent startup. Simple, no TPM dependency, Sol on the console at boot time. Upgrade to TPM-sealed for v0.2 when unattended restarts matter.
3. **Sidecar proxy scope — general shape, day one.** Outbound proxy built as a reusable pattern on day one; Gmail is the first consumer. Every future channel (Calendar, Drive, WhatsApp Cloud API, payment processors) plugs into the same shape.
4. **Audit log destination — local only for v0.1.** Append-only file on the Security Agent's volume, daily rollover, readable via `stevens audit`. Off-box replication deferred until Stevens runs on more than one host.
5. **Agent identity keypair — per-install, first boot.** Each agent generates its own Ed25519 keypair on first boot, persisted to its local state volume. Sol acknowledges the public key once via `stevens agent register <name>`. Private keys never in images, never in git.

### 3.11.1 Skills vs. capabilities (boundary)

The skills layer (`skills/` — see `CLAUDE_skills_layer.md` and `plans/v0.2-skills.md`) and Enkidu's capability registry are distinct, non-overlapping systems. Don't move things between them.

| | Capabilities (Enkidu) | Skills (`skills/`) |
|---|---|---|
| Lives in | `security/src/stevens_security/capabilities/` | `skills/src/skills/` |
| Form | RPC handlers (deterministic Python functions) | LangChain tools + Markdown playbooks |
| Caller | broker-mediated (signed UDS request) | direct in-process import |
| Can hold secrets? | yes (sealed store) | no — must call a capability for any secret-bearing operation |
| Reviewed by | Sol via `capabilities.yaml` allow rules | Sol via `scripts/review_skills.py approve` |
| Lifecycle | created at design time | proposed by agents, reviewed by Sol, promoted into the registry |

A skill *may* call a capability (the Gmail tool wrappers do exactly this — they're skills that invoke `gmail.search` / `gmail.create_draft` / etc. capabilities through the broker). A capability never calls a skill. Capabilities are the trust boundary; skills are agent-facing surface area.

Nothing in `security/` should migrate to `skills/`. If you find yourself wanting to, that's a sign Enkidu is leaking concerns it shouldn't.

### 3.12 Security milestone (proposed sequence)

1. Security Agent skeleton: container, UDS server, identity verification, policy loader, audit writer.
2. Sealed secret store (libsodium secretbox, passphrase-unlocked).
3. `stevens secrets` CLI (add / list / rotate / revoke).
4. Outbound sidecar proxy with a single capability shape: Gmail.
5. Migrate `channel_accounts.credentials` → sealed store + ref column.
6. Migrate `gmail_oauth_client.json` → sealed store.
7. Rewrite `tool_factory.get_gmail_tools` to issue sidecar-bound handles.
8. Redactor for Langfuse traces.
9. Manual end-to-end: Email PM drafts a reply without ever having held a raw OAuth token.

Each step = its own commit + its own test.

### 3.13 Approval gates and privileged execution

Some capabilities are too consequential for a static allow rule — system-level installs, payment authorization, autonomous-send, credential rotation. These are **approval-gated**: each call goes through Sol unless a **standing approval** covers it.

The approvals primitive is cross-cutting (used by the installer agent in v0.3, by future payment / credential / autonomous-send capabilities later). Standing approvals are predicate-bounded (orthogonal matchers on mechanism, source, packages, custom params) and revocable. There is no "trust forever, no questions" — only "trust until I revoke, scoped to these conditions."

The privileged-execution protocol is the agent ↔ Enkidu shape for any privileged action: agent proposes a structured **plan** (data, not code); Enkidu validates, gates, executes, runs a **structural** health check, and records to a per-agent inventory. Agents never have sudo; only Enkidu does.

Detail in `docs/protocols/approvals.md` and `docs/protocols/privileged-execution.md`. Wire-level details in `docs/protocols/security-agent.md`.

---

## 4. Other dimensions (outline — to be filled in next)

### 4.1 Channels
As in PRD §3.4: pipes with an event stream in and an action API out. No intelligence. Ownership: adapter teams. *Stevens addition:* every action API call a channel exposes must route through the Security Agent for credentials.

### 4.2 Agents
As in PRD §3.4 — core vs subject. Core agents identified so far:
- **Context Management Agent** — owns Stevens's structured memory across sessions and channels. Defines what is remembered, for how long, in what scope, under what redaction rules. TBD.
- **Life Management Agent** — the chief-of-staff planner: followups, projects, calendar, priorities. Subsumes the PRD's "Email PM" eventually; Email PM is a channel-flavored specialization for v0.1. TBD.
- **UI Agent (Stevens persona)** — the single user-facing voice. All external-facing messages sign as Stevens. Responsible for approvals, clarifying questions, daily briefings. TBD.

Subject agents (Berwyn deal, a specific relationship, etc.) ride on top of these.

### 4.3 Tools
Small, composable, reusable LangChain `BaseTool` subclasses in `shared/tools/`. Rule: **before writing a new tool, link to the existing one you considered and explain why it doesn't fit.**

### 4.4 Skills and templates
Reusable prompt + tool-selection bundles (think: "draft a reply in Sol's voice," "extract a followup from a thread"). Live in `shared/skills/`. Versioned. An agent composes skills; it doesn't reinvent them.

### 4.5 Context and memory management
Load-bearing enough to deserve its own charter doc (`MEMORY.md`). To cover: short-term conversation context, per-channel thread memory, per-subject long-term memory, per-person memory, cross-channel identity resolution, redaction-before-recall, forgetting policy. *Deferred to next draft.*

---

## 5. How we work

### 5.1 Document tiers

Three levels of living documentation. Future sessions pick up from these alone — no repo spelunking or chat replay required.

| Tier | File(s) | Purpose | Update cadence |
|---|---|---|---|
| **Charter** | `STEVENS.md`, `docs/prd.docx` | Principles, architecture, locked decisions | Rarely; changes need discussion |
| **Build Plan** | `plans/<milestone>.md` | Detailed steps + test plan per step + inline progress markers + protocol contracts for the milestone | Continuously during milestone; archived when milestone ships |
| **Status** | `STATUS.md` | One-page snapshot: active milestone, last step shipped, next step up, blockers, open decisions | Every commit |
| **Protocol** (supporting) | `docs/protocols/*.md` | Stable RPC / event / API contracts between components | On contract change, versioned |

Startup ritual (baked into `CLAUDE.md`): read **STATUS → active Build Plan → relevant protocol doc → Charter only if needed**. Never start by reading the whole repo.

### 5.2 Plan-before / plan-after (the core workflow rule)

**Every workflow begins and ends by updating the plan.** This is the rule Sol set explicitly and the one that makes the rest of the system legible across sessions.

The loop:

1. **Open.** State the goal. Read the active Build Plan. If the plan doesn't cover this work or the step needs refinement, *update the plan first* and commit that update before implementing.
2. **Mark in-progress.** Flip the step from `[ ]` to `[~]` in the Build Plan.
3. **Execute.** Smallest viable diff, following the step's own test plan.
4. **Test.** Run the planned tests. If a test can't be meaningfully run, say so explicitly and document the manual verification used.
5. **Close.** Flip `[~]` to `[x]` with the commit hash. Record outcomes (what shipped, deviations, surprises). Update `STATUS.md`. Plan + status updates go in the same commit as the code (or an immediate `plan:` follow-up).

If execution reveals the plan was wrong, *stop and revise the plan* before continuing. Do not silently deviate.

### 5.3 Versions locked

- **Python 3.10+** across all workspace members. Supersedes PRD's `>=3.12` (which was default-to-latest, not a real requirement — nothing we're doing in v0.x needs a 3.11+ feature). Revisit when a specific 3.11+ feature is worth the bump.
- **Project venv:** `./.venv/` managed by `uv`. No raw `python3`, no system-site-packages.

### 5.4 Other disciplines

- **Small commits, main stays green.** Every change is a full plan → implement → test → commit loop.
- **Reuse-first.** New tool/helper/agent requires a pointer to the closest existing thing and a one-line reason it doesn't fit.
- **Security gate.** Any change that adds network egress, widens the trust boundary, introduces new persistence, or touches secret handling stops and confirms with Sol before merging.
- **Test or declare.** If a change can't be meaningfully tested, say so explicitly and add observability (Langfuse trace, audit entry) to compensate.
- **Memory.** Claude's MEMORY system carries decisions and working-contract rules across sessions. These plan docs carry project state across sessions. The two are complementary: memory is Claude's, plan docs are the team's.

---

## 6. Non-goals (still)

All non-goals from PRD §1.3 stand: no autonomous sending, no cloud LLM by default, no orchestrator agent, no multi-user, no cloud deployment.

Added:

- No new abstraction layer until there are three concrete uses for it.
- No secret material in git, in logs, in LLM prompts, in traces. Ever.

---

## 7. Open decisions (running list)

Security: all §3.11 items resolved 2026-04-22.

Architecture:
1. Is the Stevens UI agent cross-channel (CLI + Gmail + Telegram later) or channel-specific per surface?
2. Where does Context Management live — its own container, or a library the core agents import?

Memory (deferred to `MEMORY.md`):
3. Scope model for memory (per-channel / per-subject / global)
4. Forgetting policy
5. Identity resolution across channels (Sol's contacts)

---

*Next up: confirm §3.11 decisions, then start milestone in §3.12 step 1.*
