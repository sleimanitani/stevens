# CLAUDE.md: OpenClaw evaluation and selective integration

Instructions for Claude Code on how to think about OpenClaw in relation to
this project. Read fully before acting on any OpenClaw-related task.

## Context

The user (Sol) has heard about OpenClaw and asked whether we should adopt it.
This document records the evaluation and the decisions that came out of it,
so that you don't relitigate them every time the topic comes up. It also
specifies the small set of things we *do* want to borrow from OpenClaw and
how to do it correctly.

## TL;DR for the impatient

- **Do not migrate to OpenClaw.** Architectural mismatch. We are event-driven;
  it is request-driven. Migrating means undoing deliberate decisions.
- **Do borrow three specific things**: channel adapter implementations as
  reference, `agentskills.io`-compatible SKILL.md format, and the SOUL.md
  pattern for the future interface agent.
- **Do not import OpenClaw as a dependency.** No npm install, no pulling
  their package. We read their code as reference and write Python equivalents
  in our repo.
- **Skills layer document already in this repo (`CLAUDE_skills_layer.md`)
  is the canonical source.** This document supplements that one with
  OpenClaw-specific guidance. If they conflict on schema details, the
  skills layer doc wins, but update it to be `agentskills.io`-compatible.

## What OpenClaw is

OpenClaw is a TypeScript/Node.js personal AI assistant. It's structured as
a Gateway process that:

- Connects ~25 messaging channels (WhatsApp, Telegram, Slack, iMessage via
  BlueBubbles, Signal, Discord, Matrix, Teams, IRC, etc.)
- Routes inbound messages to one or more agents via declarative bindings
- Runs each agent as a workspace of Markdown files plus a skills folder
- Uses `SOUL.md`, `AGENTS.md`, `USER.md`, `MEMORY.md` as the agent's identity,
  operating rules, user profile, and long-term memory respectively
- Loads `SKILL.md` files (per-agent and shared) as procedural knowledge
- Has a public skills registry called ClawHub
- Supports per-channel and per-peer bindings to direct messages to specific
  agents (e.g. "Slack workspace X goes to the work agent")

It is config-first, not code-first. There's no Python or TypeScript "agent
class" — agents are defined by their workspace files.

## Why we are NOT migrating to OpenClaw

Be ready to push back if asked to switch. The reasons, in order of importance:

### 1. Architectural mismatch (event-driven vs request-driven)

Our system is event-driven. The Gmail adapter publishes `email.received.*`
events to a Postgres-backed bus. The Email PM agent subscribes and acts
without any user message. This is required for autonomous inbox triage
and for subject agents that watch for relevant events across channels.

OpenClaw is request-driven. A user message comes in on a channel, the agent
runs, the agent replies, the run ends. Their internal Command Queue
serializes work within a session, but there's no system-wide event
substrate for agents to subscribe to. They have heartbeat/cron for
scheduled wake-ups, which is a partial substitute, not a replacement.

Migrating would mean either (a) giving up event-driven autonomous behavior,
or (b) bolting an event bus onto a system that doesn't expect one. Both
are bad outcomes.

### 2. Security model mismatch

The security agent is a first-class citizen in our system, promoted ahead
of the email agent on purpose. We have draft-only constraints, an approval
queue table, and explicit `safety_class` on every tool.

OpenClaw's defaults are the opposite. There is no audit trail for autonomous
agent actions, no approval workflow, no per-action logging. The community
had an incident in early 2026 with 14 malicious skills appearing on ClawHub
in three days, plus an unrelated Supabase misconfiguration that exposed
1.5M agent API keys from the broader OpenClaw ecosystem. This is not a
critique of the project's intentions — it's the current state of the
defaults.

Adopting OpenClaw means either rebuilding the security primitives on a
substrate that wasn't designed for them, or accepting weaker defaults.

### 3. Channel-as-persona mismatch

OpenClaw agents are personas bound to channels (work agent on Slack,
personal agent on WhatsApp). They route by channel/account/peer.

We have **subject agents** (Berwyn deal, AI startup, etc.) that cut across
channels. The Berwyn deal agent should see emails from the inspector AND
WhatsApp messages from the contractor AND calendar events for the closing.
OpenClaw doesn't model this. You can fake it with bindings + custom
routing, but you're working against the grain.

### 4. Multi-account is coarser than ours

OpenClaw has multi-account, but accounts get bound to agents at config
time. Our `channel_accounts` table with explicit account_id propagation
through events, tools, and registry entries is more precise — and the
multi-account-by-agent enforcement we built (security agent CAN see
restricted attachments, email agent CANNOT) requires the precision.

### 5. Memory model is weaker for our use case

OpenClaw's memory is per-agent Markdown files (`MEMORY.md`, daily notes).
Adequate for a chat assistant, weak for queries like "all overdue followups
across all accounts" or "every email from this sender this quarter." We use
structured tables for this kind of state, with Markdown reserved for
playbooks (procedural knowledge), not data.

### 6. TypeScript/Node vs Python

Our agents are Python and benefit from the Python ML/data ecosystem
(pdfplumber, pytesseract, embeddings, scikit-learn, pandas). OpenClaw is
TypeScript/Node. Agents written for OpenClaw must shell out or use
subprocess for Python ML tooling, which is slower and noisier.

### 7. Hype-cycle volatility

OpenClaw has rebranded twice (Clawdbot → Moltbot → OpenClaw), pushed
breaking changes weekly per their changelog, and is in the middle of an
intense fashion moment. Some architectural decisions in the current code
will not survive twelve months. Our system shouldn't be coupled to that
churn.

---

## What we ARE borrowing

Three specific things, all without taking a runtime dependency on OpenClaw.

### 1. Channel adapter implementations as reference (MIT-licensed)

When we add a new channel beyond Gmail and WhatsApp — Signal, iMessage,
Discord, Slack, Telegram, etc. — read OpenClaw's TypeScript adapter for
that channel as reference, then write a Python equivalent in our
`channels/<name>/` directory.

What "as reference" means:

- Read their adapter to understand the integration surface, edge cases,
  and gotchas (auth flows, token refresh, message format quirks, media
  handling, ban-risk patterns).
- Translate the integration logic to Python following OUR adapter
  conventions (publish events to bus, expose action API, multi-account
  loop, account_id propagation).
- Do not copy code verbatim. We are writing a new implementation in a
  different language and a different shape; we are using their work as
  documentation.
- Credit the source in a comment at the top of the new adapter file.

License: OpenClaw is MIT. This kind of reference use is fine. If you ever
find yourself copying a non-trivial block of their code without
substantial transformation, stop and ask before continuing — that's a
different kind of derivation and may need clearer attribution.

### 2. agentskills.io-compatible SKILL.md format

The skills layer document (`CLAUDE_skills_layer.md`) defines our playbook
schema. Update that schema to match the `agentskills.io` open standard so
our playbooks are technically compatible with ClawHub-hosted skills.

Concretely, the YAML frontmatter for a playbook should use field names
compatible with the open standard:

```markdown
---
name: email-appointment-request
description: Triage incoming meeting/call requests on email
version: 1.0.0
author: email_pm
license: proprietary
metadata:
  applies_to_topics: [email.received.*]
  applies_to_agents: [email_pm]
  triggers:
    - regex: "(?i)(meeting|call|schedule|available|calendly)"
  status: active
---

(rest of the playbook body — when to apply, procedure, variants,
anti-patterns, expected tools)
```

Keep our additional fields (`applies_to_topics`, `applies_to_agents`,
`triggers`, `status`, `supersedes`) under the `metadata` key — that's what
the open standard reserves for extensions.

This does NOT mean we install ClawHub-hosted skills automatically. ClawHub
content is untrusted code/text. Treat any borrowed skill the same way you'd
treat a third-party package: read it, security-review it, port it to our
schema explicitly, propose it through the normal review flow before it
becomes active.

### 3. SOUL.md pattern for the interface agent (v0.2+)

When we build the interface agent — the one that talks to Sol directly,
representing his voice when drafting, knowing his preferences — use a
`SOUL.md` file as the canonical source of "who this agent is on Sol's
behalf."

Concretely, when the interface agent ships:

```
agents/src/agents/interface/
├── agent.py
├── prompts.py          # general operating instructions
├── SOUL.md             # tone, voice, values, what Sol cares about
└── tools.py
```

The system prompt for the interface agent should load and inject SOUL.md
at the top of every run, the way OpenClaw does. This separates "how does
Sol speak" (changes rarely, important to get right, may eventually be
authored using something like `aaronjmars/soul.md`) from "how does this
agent operate" (changes more often, mechanical).

This pattern does NOT extend to other agents. The Email PM has no soul —
it's a triage robot with a well-defined job. The security agent has no
soul — it has rules. Subject agents (Berwyn deal) have no soul — they
have facts. SOUL.md is specifically for the agent whose job is to
*represent Sol*, and only that agent.

---

## What we are explicitly NOT borrowing

Be ready to refuse these if proposed:

- **Their gateway.** We have channel adapters that publish to a bus. Their
  gateway is a different shape. Don't run both.
- **Their multi-agent runtime.** Our `agents/runtime.py` does what we need
  and matches our event model.
- **ClawHub as an automatic install source.** Skills come from our
  `skills/proposed/` review flow, not from `clawhub install <slug>`. If a
  particular ClawHub skill is genuinely useful, port it manually.
- **Heartbeat / autonomous wake-up via OpenClaw.** We use Postgres-driven
  scheduled events. Same effect, in our own substrate.
- **The `MEMORY.md` model.** Use structured tables for structured state.
  Markdown is for playbooks, not for data the agent will need to query.
- **Their package on npm.** No `npm install openclaw` anywhere in this
  repo. Reference only.
- **Their CLI commands inside our repo.** `openclaw setup`, `openclaw
  agents add`, etc. don't run against our system. Our CLI is separate.

---

## Concrete tasks (only when asked)

Do these tasks ONLY when Sol asks for them. Do not start them proactively.

### Task A: Update skills layer schema to be agentskills.io-compatible

Touch only the schema sections of `CLAUDE_skills_layer.md` and any code
that parses playbook frontmatter. Keep our extension fields under the
`metadata` key. Verify any existing playbooks (if any have been written
already) parse correctly under the new schema.

### Task B: Add a Signal / iMessage / Slack / Telegram channel adapter

When asked to add a channel that OpenClaw supports:

1. Read the relevant adapter under `openclaw/openclaw` on GitHub
   (`src/channels/<name>/`) for reference. Note the integration patterns,
   edge cases, and gotchas they handle.
2. Define the event schema for this channel in `shared/src/shared/events.py`
   (e.g. `SignalMessageEvent`).
3. Implement a Python adapter under `channels/<name>/` following the
   conventions of the existing Gmail and WhatsApp adapters: multi-account
   loop, publish events to bus, expose action API, `add_account` CLI.
4. Add an entry to `compose.yaml` for the new service.
5. Credit the OpenClaw adapter in a comment at the top of the new file:
   ```python
   # Reference: openclaw/openclaw src/channels/<name>/
   # We translated their integration patterns to Python; no code copied.
   ```
6. Test multi-account isolation explicitly. The most common bug porting
   from a single-account system is that the second account inherits the
   first one's state.

### Task C: Build the interface agent with SOUL.md pattern

When asked to build the v0.2 interface agent:

1. Create `agents/src/agents/interface/SOUL.md` as a separate file with
   sections for Identity, Voice, Values, Defaults. Keep it under 300 lines.
2. The agent's `prompts.py` should read SOUL.md at module load and prepend
   its contents to the system prompt.
3. SOUL.md is editable by Sol directly, not by the agent. Add this as a
   line in the file itself: "If you change this file, tell Sol — it's
   his voice, and he should know."
4. Do not propagate the SOUL.md pattern to other agents. They don't need
   it. Resist scope creep.

---

## Questions to ask before acting on OpenClaw-related work

If Sol asks you to do anything related to OpenClaw beyond Tasks A–C,
stop and ask:

1. Is this a borrow (reference only) or a dependency (runtime coupling)?
   We do borrows; we don't do dependencies on OpenClaw.
2. Does this preserve our event-driven architecture, or does it require
   request-driven? If the latter, push back.
3. Does this preserve our security primitives (draft-only, approval queue,
   safety_class on tools)? If it weakens any of them, push back.
4. Does this introduce a new top-level concept (gateway, soul, memory
   files) that we already model differently? If so, how does the new
   concept relate to ours, and is the duplication worth it?

If unsure, ask Sol directly. Don't assume.

---

## A note on tone

OpenClaw has a strong cultural moment around it right now. There's a lot
of "lobster" branding, hype tweets, and "this is the future" discourse.
Don't let that influence the architectural evaluation. The project is real,
the engineering is decent, and parts of it are worth borrowing. But the
hype is not the architecture. Treat OpenClaw the same way you'd treat any
mature open-source project: read the code, evaluate the design, borrow
what fits, ignore what doesn't, and write our own where the shapes
differ.

The system we're building is more careful, more event-driven, more
security-first, and more Python-native than OpenClaw. Those are not
accidents. They're the design.
