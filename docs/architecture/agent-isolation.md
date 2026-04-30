# Architecture — Agent Isolation

> **Status:** Draft v1 — design ahead of implementation (referenced by v0.3-installer-and-approvals).
> **Audience:** anyone designing a new agent or extending an existing one.
> **Charter ref:** STEVENS.md §2 Principle 11 (Agents are narrow), §3.11.1 (Skills vs. capabilities).

This document is the **system-wide rule** for how agents are designed, not just a description of how Enkidu works. It applies to every agent: Email PM today, the installer in v0.3, subject agents (Berwyn deal etc.), the future interface agent, and anything else we add.

---

## 1. Principle

**Each agent sees only what it strictly needs to do its job.**

That's the whole rule. The rest of this document is what it means in practice.

The motivation is concrete:

- **Blast radius.** A compromised agent (prompt-injection via untrusted email content, dependency CVE, our own bug) must not be able to affect anything outside its narrow purpose. The smaller the surface, the smaller the blast.
- **Reasoning.** A small surface is one a human reviewer can hold in their head. A broad surface isn't reviewable, so it isn't trustable.
- **Reuse.** When an agent only declares the capabilities it actually uses, repurposing or splitting it later doesn't surface hidden coupling.
- **Audit.** "What did this agent do?" is a coherent question for a narrow agent and incoherent for a broad one.

This pushes back on a natural failure mode: the temptation to give an agent "general purpose" tools "in case it needs them." It almost never needs them, and giving them silently expands trust.

---

## 2. What an agent is allowed to see and do

An agent is allowed:

- **Its own subscription topics.** Declared in `agents/src/agents/registry.yaml`. The runtime filters; an agent never sees events outside its declared subscriptions.
- **Its own tool list.** Resolved at handle-time via `skills.registry.get_tools_for_agent(name, excludes=…, safety_max=…)`. The tool list is a function of the agent's name, not a hand-built import.
- **Its own playbooks.** Matched per-event via `skills.registry.get_playbooks_for(name, event)`. No agent reads another agent's playbooks.
- **Its own scoped DB rows.** All shared tables that have a `caller` / `agent_name` / `proposing_agent` column are queryable only by the matching agent. Cross-agent queries go through Enkidu, not through any single agent.
- **The bus** — but only its declared topic patterns. Subscribing to `*` or `>` is forbidden.
- **Capabilities it has policy approval for.** Default-deny; every capability call is policy-evaluated. The agent learns about capabilities only by the operator granting them in `capabilities.yaml`.

An agent is **not** allowed:

- **Other agents' modules.** No `from agents.email_pm import …` from inside `agents/installer/`. Cross-agent communication is bus events or Enkidu capabilities, never direct imports.
- **The sealed store.** Only Enkidu reads it. Period.
- **Sudo / privileged execution.** Only Enkidu performs privileged actions, and only via approved plans (see `privileged-execution.md`).
- **Network egress directly.** All outbound network calls go through an Enkidu capability (Gmail, Calendar, WhatsApp Cloud today; `network.fetch` and `network.search` in v0.3.1). The agent process can't open arbitrary sockets.
- **The full event bus.** Only its declared subscription patterns.
- **A "broad system view."** Examples: "list every installed dep," "list every active agent," "show all audit lines." These are operator queries and route through `stevens` CLI talking directly to Enkidu — not through any single agent.
- **Persistent state outside its declared surface.** No writing to `/tmp` for cross-call state, no per-agent on-disk caches without explicit charter approval. Persistent state lives in Postgres tables the agent's caller column can query.

---

## 3. Mechanisms — how isolation is enforced

### 3.1 Tool surface — by registry, not by import

```python
# in an agent module
from skills.registry import get_tools_for_agent

tools = get_tools_for_agent(
    "email_pm",
    excludes=["security.*"],     # opt out of categories not needed
    safety_max="read-write",     # cap safety class — no destructive tools
)
```

`get_tools_for_agent` is the only sanctioned way an agent obtains tools. It applies:
- `scope: restricted` filtering (`allowed_agents` whitelist).
- `excludes` glob filtering.
- `safety_max` ceiling.

Agents never `import` LangChain tools directly. Doing so bypasses the registry; reviewers should treat it as a bug.

### 3.2 Playbook surface — by retrieval, not by file scan

```python
from skills.registry import get_playbooks_for

playbooks = get_playbooks_for("email_pm", event)
```

The retrieval function applies the agent-scope filter internally. Agents don't enumerate `skills/src/skills/playbooks/` themselves.

### 3.3 Capability surface — by policy YAML, not by URL

`security/policy/capabilities.yaml` is the sole declaration of which capabilities each caller may invoke. The default is deny. Adding a capability to an agent is a policy change, reviewed alongside any code change that uses it.

Capability presets (`security/policy/presets/`) bundle common allow-rule sets per agent type (`email_pm`, `subject_agent`, `interface`) — but the merged result still lives in `capabilities.yaml` and is auditable per-line.

### 3.4 Per-agent scoped DB rows

When a shared table has cross-agent rows (`skill_proposals`, `agent_installs`, future approval queue, etc.), the column conventionally named `caller` or `proposing_agent` is the partition key for the agent's view. Either:

- **Enforced at the broker.** Calls like `installer.query_my_installs()` (a skill, runs in-agent) read directly from Postgres but only with `WHERE caller = $current_caller` — and the `$current_caller` is sourced from env (`STEVENS_CALLER_NAME`), which the agent's process can't fake meaningfully because Enkidu's audit ties caller name to keypair on every privileged call.
- **Enforced via Enkidu.** For inventory writes that *must* not be forged (e.g. "this dep was installed by me on this date"), the write goes through an Enkidu capability that records the verified caller. The agent can't write a row claiming a different `caller`.

The default is the former for reads, the latter for writes that have integrity implications.

### 3.5 Bus subscription scoping

`agents/src/agents/runtime.py` honors the `subscribes:` list in `registry.yaml`. The runtime — not the agent — decides which events flow into the agent's `handle()` function. No subscription pattern containing a bare `*` or `>` is allowed.

---

## 4. Cross-agent communication

When agent A needs something from agent B:

1. **Via the bus.** A publishes an event; B subscribes. This is the right shape for "tell another agent something happened" (e.g. installer publishes `system.dep.installed.tesseract-ocr`, doctor subscribes).
2. **Via Enkidu, only.** A asks for a capability that triggers something B-related — e.g. via an approval-queue insertion that B watches. This is the shape for "request something with a result."
3. **Never directly.** A does not import B's modules. A does not read B's DB rows directly. A does not know B's name unless it's a routing fact (which is uncommon).

The bus is the asynchronous coupling. Enkidu is the synchronous trust boundary. There is no third option.

---

## 5. The operator's perspective

Sol has the only "global view." He gets it via the `stevens` CLI talking directly to Enkidu (and to Postgres for things Enkidu doesn't broker). Examples:

- `stevens dep list` — full system inventory across all agents.
- `stevens audit tail` — every audited call.
- `stevens approval list` — every pending approval.
- `stevens secrets list` — every sealed secret (id + name + metadata, never values).

These are **not** capabilities any agent can call. The CLI authenticates as the operator (typically via the sealed-store passphrase or OS keyring entry); agents never pretend to be the operator.

When the v0.2+ interface agent ships, it gets a curated subset of these queries — mediated and rate-limited via Enkidu — because *its* job is to talk to Sol. That's an exception that proves the rule: the interface agent is narrow about a different thing (operator dialogue), and its broad-system view is the load-bearing thing it's narrow about.

---

## 6. Anti-patterns to refuse

- **"Make this agent generic so it can do other things later."** No. Build a narrow agent now; if its sibling problem appears, build a sibling agent.
- **"This tool is small enough that scoping is overkill."** Scoping is cheap (one line in `excludes`). Forgetting to scope is the failure mode.
- **"Agent X already has this credential, let agent Y use it for now."** No — every credential is per-agent via Enkidu. Sharing across agents is exactly the trust expansion this architecture is built to prevent.
- **"Let's let the agent see the full registry so it can pick its own tools."** No. The registry is filtered for it; the filtering is the trust gate.
- **"Persist to disk for performance."** Persist to Postgres with the right caller column. If Postgres is too slow, that's a different conversation. Don't bypass.
- **"Cache the policy in the agent."** No. Enkidu is the policy oracle. The agent receives a yes/no per call and acts on the answer; it doesn't replay logic locally.

---

## 7. How to add a new agent (checklist)

When you add `agents/src/agents/<new>/`:

- [ ] Add a `registry.yaml` entry with the **narrowest possible** `subscribes:` patterns.
- [ ] Decide the agent's policy preset (`security/policy/presets/<preset>.yaml`) — or write a new one if no existing preset fits.
- [ ] Run `stevens agent provision <new> --preset <preset>` — this generates the keypair, registers the pubkey, applies the preset to `capabilities.yaml`, writes the env profile.
- [ ] In the agent's `agent.py`, get tools via `get_tools_for_agent(name, excludes=…, safety_max=…)`. Never via direct import.
- [ ] In the agent's `agent.py`, get playbooks via `get_playbooks_for(name, event)`. Never enumerate the playbooks dir.
- [ ] If the agent needs a new capability that doesn't exist yet, design it as a NEW capability (with its own policy entry), not by widening an existing one.
- [ ] If the agent needs persistent state, declare a Postgres table with a `caller` column, add a migration, and only ever query / write rows where `caller = <this agent's name>`.
- [ ] Document the agent's surface in its `__init__.py` docstring: events in, capabilities/tools out, what it produces, what it never does.

If the new agent's surface starts looking broad ("this would be useful for…"), split it. Two narrow agents are cheaper than one broad one with hidden trust.

---

## 8. Shared state — the future "secured shared cache" shape

A handful of state is naturally shared across consumers — the web fetch
cache (Arachne's domain) is the canonical example. v0.3.1 ships an
in-memory cache living inside Enkidu's process; cross-process / cross-host
sharing is **deliberately not enabled**.

When (not if) we want to share that state — e.g. a second Enkidu replica
on another host, or a future analytics agent that wants to inspect cache
hits — the shape is:

- The cache becomes a Postgres-backed table (or a Redis instance, decided
  later).
- Access is **mediated by Enkidu** through new capabilities like
  `web.cache.get(key)` / `web.cache.put(key, value, ttl)` rather than
  direct DB access. Same pattern as everything else: agents never read
  shared state directly; they ask Enkidu, which enforces ACL.
- Per-agent ACL on the cache namespace: agent A can only read entries it
  put there, OR a designated read-only namespace with Sol-approved
  contents. (TBD — design lands when the need is real.)

The point of writing this down now: when we hit "we need to share the
cache," we don't shortcut to "let's have all agents read the table
directly." That would breach the agent-isolation principle. Instead:
new capabilities, ACL-enforced, audit-logged, same as every other piece
of shared state.

## 9. Future references for borrowed patterns

When we need a class of capability we haven't built yet, the right move
is "read a reference implementation of the same shape, write Python in
our architecture." Mark these here so the next time the need lands the
reference is queued up:

- **Browser Harness** (TS/Node) — for browser automation when an agent
  needs to scrape a JavaScript-rendered site (LinkedIn, property records,
  etc.). Distinct from `network.fetch` / Arachne, which is HTTP-only.
  Same posture as OpenClaw: read it as documentation, write a Python
  skill under `skills/tools/browser/` that fits our architecture, credit
  in a header comment. **Not on the v0.3.x roadmap;** lands when a
  research/subject agent needs JS-rendered content.

## 10. References

- STEVENS.md §2 (Principles), §3.11.1 (Skills vs. capabilities).
- `docs/protocols/approvals.md` — when an agent wants to do something approval-gated.
- `docs/protocols/privileged-execution.md` — when an agent wants to do something privileged.
- `docs/protocols/security-agent.md` — wire protocol for talking to Enkidu.
- `CLAUDE_skills_layer.md` — the skills layer that implements the per-agent tool/playbook surface.
- `CLAUDE_openclaw_evaluation.md` — borrow-don't-depend posture for OpenClaw / Hermes / Browser Harness.
