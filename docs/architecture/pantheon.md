# The Pantheon Architecture

## Why this matters

Over time, you're going to accumulate a lot of agents doing a lot of different things for you. One might help you stick to an exercise routine. Another reads and triages your email. Another tracks your finances or keeps an eye on health records. Some help with family logistics — schedules, school, appointments. Others help with work — a project here, a client there. Still others handle one-off jobs: filing your taxes this year, planning a trip, drafting a contract.

If each of these were just a fully separate, standalone agent doing its own thing, you'd have a real problem. Every one of them would need its own way of handling your secrets, its own connection to email or calendars or your bank, its own memory of what you've told it before. The risk of something leaking — your medical notes ending up somewhere they shouldn't, the tax helper seeing things it has no business seeing — would be enormous. And the chaos of trying to keep them all coordinated would be its own headache.

But if you only had a few big general-purpose agents, you'd lose the other direction: nothing would be focused enough to actually be good at what you need.

The way out is to split responsibilities into two kinds.

The first kind is **shared, repeated responsibilities** — the things that come up no matter what task is being done. Protecting your secrets. Making sure every agent communicates through standard, vetted channels. Making sure each agent gets exactly the information it needs to do its job, and nothing beyond that. Remembering what matters across conversations. These are handled once, centrally, by a small set of trusted parts of the system.

The second kind is **specific, focused responsibilities** — agents built around a particular task or goal. Managing one email account. Helping you hit a health goal. Filing this year's taxes. Running a rental property. Looking after a small business. Some of these stick around for a long time; some only exist for a few hours. But each one has a defined job, a defined scope, and only the access it needs for that job.

When something specific turns out to be useful across many tasks, the reusable part of it gets pulled out and moved into the shared layer — so it's available to everyone, consistently and safely, instead of being reinvented over and over.

To make this easier to think and talk about, we borrow a metaphor from Greek mythology — not because there's anything actually divine going on, but because the Greeks had a clear way of organizing their world into two tiers: **gods** with permanent domains, and **mortals** living specific lives for specific purposes. That maps almost exactly onto the architecture, and it gives us a vocabulary that's easier to remember than "shared infrastructure layer" and "task-scoped agent instances."

## The two tiers

| Greek world | Your assistant | What it means to you |
|---|---|---|
| **Pantheon** — the named gods, each with a domain | **The core** — a small set of permanent, trusted services | The familiar cast. Always there, always the same. You learn them once and they don't change on you. |
| **Mortals** — born for a purpose, live their lives, pass on | **Worker agents** — created for a specific task, project, or goal | You don't meet them by name. You say what you need, and one shows up to do it. Some last hours, some last years, but each is focused on one thing. |

The Pantheon is the *system you've set up*. The Mortals are the *work happening inside it*. You shape the first carefully; you ask for the second casually.

## What separates the two

The line isn't importance, or how long something has been around. It's **who depends on whom**.

In Greek mythology, multiple mortals depend on each god — for the harvest, for safe passage, for wisdom, for love. If fewer mortals need that god, the god's power fades and they are eventually forgotten. And a god rarely interferes directly in the world; they act *through* the mortals who serve them and call on them.

The architecture works the same way:

- **The Pantheon is depended on by the rest of the system.** Its members provide shared services — keeping your secrets, remembering things across conversations, handling connections to the outside world, enforcing the rules about who's allowed to do what. They face *inward*, toward the other agents. They rarely act in the world directly; they act through the Mortals who rely on them.
- **The Mortals depend on the Pantheon, and act in the world themselves.** They have specific missions — manage this inbox, research that topic, file these taxes. They face *outward*. They use the Pantheon's services to get their jobs done, but nothing depends on them in turn.

The rule that keeps this clean: **nothing in the Pantheon should ever depend on a Mortal.** Gods never depend on mortals — except in the sense that they need mortals to remember them and call on them. Mortals depend on the Pantheon, and sometimes on each other, but the core never reaches down into the worker layer. If a Mortal turns out to be something the core needs, that's a sign it should be promoted — its useful part lifted out and made part of the shared services. And if a god in the Pantheon stops being called on by enough Mortals, its domain is fading and it may eventually be retired.

For you, this shows up as two different modes. When you're setting things up — deciding what your assistant *is* and what it can do across the board — you're shaping the Pantheon. When you're asking for something to be done — handle this, watch that, get this filed — you're spawning Mortals.

## How things change over time

The system isn't static. Capabilities can rise into the core, fade out of it, get replaced, or get retired. Each kind of change has a real meaning, and the Greek metaphor gives each one a name worth remembering.

| Greek event | What's actually happening | When it happens | What you notice |
|---|---|---|---|
| **Apotheosis** — a mortal earns godhood (like Heracles) | A capability gets promoted into the core | Many different tasks turn out to need the same thing, so it makes sense to provide it once, centrally. Email is the classic case: one helper managing one inbox is a Mortal, but once many helpers need to send and read email, "email" becomes a shared service. | Something you used to ask for case-by-case becomes just *available*. You stop thinking about it; it's part of the furniture. |
| **Succession** — new gods replace old ones | A better version takes over a domain | An improved implementation comes along. The old one isn't destroyed, but it's no longer the one in charge. | Same capability, working better. Old version may still be reachable for things that haven't moved over yet. |
| **Fading** — gods forgotten as their followers move on | A core capability stops being broadly needed | Usage drops. Nobody really depends on it the way they used to. It's still there, but it's no longer pulling its weight as core infrastructure. | It still works if you ask, but it's quietly drifting out of the central picture. A candidate to be retired. |
| **Exile** — a god sent away in punishment | A capability is pulled from the core after a problem | Something went wrong — a security incident, a bug, misuse. It's removed from the trusted set, possibly to return later once cleared. | That capability becomes unavailable or restricted until things are sorted out. |
| **Binding** — gods chained but not killed (like Prometheus) | A capability is officially retired but kept reachable | Something newer has replaced it, but old data or old references still need it. It's there if you reach for it, but nothing new should be built on it. | Read-only access to the old way of doing things. |
| **Ragnarök** — the gods actually die | A capability is removed entirely | The domain is gone, nothing depends on it anymore, no legacy state to preserve. | It disappears from the system. |

## The lifecycle, in one line each

- A **Mortal** is born with a mission, does its work, and ends — unless its capability turns out to be needed across many tasks, in which case it *ascends* into the core.
- A **Pantheon member** holds its domain as long as that domain is genuinely shared — and if usage fades, problems arise, or a successor takes over, it can be *diminished, exiled, bound, or retired*.

## Who carries out the transitions

The metaphor names *what happens*. Two specific Pantheon members name *who does it*:

- **Hephaestus**, smith of the gods, owns the **creation side**. When you spawn a Mortal, when an Apotheosis promotes a Mortal capability into the core, when a Succession installs a new implementation in an existing domain — Hephaestus reads the manifest, registers the capability with Enkidu, generates the runtime artifact (systemd unit, listener subscription, polling timer, depending on what the manifest declares), and wires the bus subscriptions. The forge.
- **Hades**, lord of the dead, owns the **ending side**. When a Mortal's task is done, when a Pantheon member is Faded, Exiled, or undergoes Ragnarök — Hades tears down the runtime, revokes the capabilities, and archives the audit trail and last state to the underworld store. The graveyard with a librarian.

Demiurge — the orchestrator above the Pantheon — *decides* which transition applies based on policy and operator input. The actual mechanics route through Hephaestus or Hades depending on direction (creation or ending). The canonical roster of all Pantheon members and the executor table for each lifecycle event live in [`DEMIURGE.md`](../../DEMIURGE.md) §1.1.

## What it means for you

You interact with the Pantheon **by name and by trust** — these are your standing officers. You know what each one does, you've granted them durable permissions, and you've decided they're trustworthy enough to hold the keys. You interact with Mortals **by intent** — you describe an outcome and the system creates whoever's needed, with just the access required for that job and nothing more. You don't track them individually unless one earns its way into your attention.

The promotions and retirements are how the system *grows with you* without sprawling. Things you keep needing get absorbed into the core, where they're done well and done once. Things in the core that stop mattering get pruned. The Pantheon stays small, named, and meaningful. The Mortal layer stays focused and disposable. And the boundary between the two — between *the trusted core that holds your secrets* and *the workers that just do their jobs* — stays sharp enough to actually keep you safe.

---

## The fuller cosmology (locked 2026-05-03)

The metaphor extends past "two tiers" — it's a five-element architecture. Three layers (Demiurge, the Pantheon, the Creatures), the Creatures split into four kinds, and a future fifth Creature kind reserved.

### The three layers

**Demiurge** is the substrate. *Not* a god — pre-Olympian per Plato, the craftsman who shapes the world the gods inhabit. In code: bootstrap, supervisor, install machinery, package layout, lifecycle manager. No reasoning, no LLM. Faces Sol-as-operator (CLI) and the OS. Stable across all milestones. Demiurge doesn't make decisions; it executes the decisions the gods make.

**The Pantheon** is the small named cast of permanent gods. Each owns either a substantive domain (Enkidu owns secrets, Mnemosyne owns memory, Arachne owns web, Sphinx owns documents, Janus owns operator-assisted browser) or a coordination role (Iris translates between Sol and the divine; Zeus chairs the council). Each god is one OS process (its own systemd user unit), with its own keypair, its own policy block, its own audit angel. Gods can read each other's substrates but never write to them — domain integrity is single-owner.

**The Creatures** are forged on demand, do their work, and end. Four kinds:

| Kind | Has LLM? | Has agency? | Visible to Sol? | Visible to other Creatures? | Examples |
|---|---|---|---|---|---|
| **Mortal** | yes (full reasoning loop) | yes, scoped to its blessings | yes (`demiurge hire list`) | yes (bus events; calls to existing Beasts/Automatons) | email_pm, trip_planner, installer, researcher |
| **Beast** | yes (model call, no loop) | no — function-shaped (in → out) | yes (`demiurge beasts list`) | yes (called by Mortals as blessed tools) | image_gen, embedder, classifier, summarizer, OCR, transcription |
| **Automaton** | no | no — deterministic, scheduled | yes (`demiurge automata list`) | not directly (acts only via bus events) | rss_reader, scheduler, port_scanner, log_shipper |
| **Angel** | optional | bound — serves a single god | **no** (without future Prophet credential) | **no** | Enkidu's audit angel, Mnemosyne's memory angel |

A future fifth kind — **Prophet** — is reserved as the only Creature that can perceive Angels. Not built; the architectural seat is held.

The **Beast vs. tool** distinction is about stochasticity: anything model-driven and stochastic / generative is a Beast (a Creature with identity, audit trail, retire-able). Deterministic transformations are tools (functions on the capability registry, no separate identity). Image generators are Beasts; PDF text extraction is a tool (Sphinx-routed).

### Why Demiurge, Iris, and Zeus aren't the same thing

These three are easy to confuse because they all "orchestrate." They're at different layers and have different concerns.

| Layer | Role | Has LLM? | Faces |
|---|---|---|---|
| **Demiurge** | Substrate — bootstrap, supervisor, install machinery, OS-level lifecycle, package layout | No | Sol-as-operator (CLI) and the OS |
| **Zeus** | Chairman of the Pantheon — receives structured intents, reasons about which gods need to be involved, dispatches multi-god operations in parallel, judges cross-god conflicts, makes the call on whether a request proceeds | Yes (judgment) | The other gods (god-to-god coordination) |
| **Iris** | Personal UI agent — knows Sol's preferences (modality, channels, quiet hours, vocal tone, language), translates dialogue ↔ structured intents, owns notification routing, presents results back to Sol | Yes (dialogue + personalization) | Sol (and only Sol) |

Concretely:

- "Bootstrap this host" / "supervise this process" / "install this plugin" → **Demiurge.**
- "Sol said 'spawn a trip planner for Tokyo'" → **Iris** translates → **Zeus** dispatches → individual gods bless → **Hephaestus** forges.
- "Notify me about this on WhatsApp because it's after 9pm" → **Iris.**
- "Allow this gmail.send call" → **Enkidu** alone (single domain, no coordination).

Iris does not know which gods exist or what they do. She knows Zeus exists. Zeus knows the gods.

### The Angel pattern — opacity by design

When Hephaestus forges a Creature, gods that need to observe can commission an Angel attached to it. **Enkidu always commissions an audit angel** for every Creature — non-negotiable, no exception. Mnemosyne (v0.13+) always commissions a memory angel. Other gods opt in.

Angels are **invisible** by design:

- Not in `demiurge hire show <id>` or any Creature listing.
- Not in any host Creature's `tools.list()`.
- Not in `demiurge audit tail`.
- Sol does not see them by default. The future **Prophet** credential is the explicit (not-yet-built) escape hatch.

Opacity is enforced at the OS level, not by Python politeness:

- Angels run as separate processes, supervised by `demiurge-runtime`.
- Read-only attachment: an angel gets read-only handles to the Creature's observation feed. No write-back path exists from angel to Creature.
- Outbound IPC: each angel writes only to its commissioning god's substrate, via a god-only channel the host has no socket access to.
- Lifecycle binding: Hades retires angels along with their host Creature.

### The unified observation feed

Every Creature has one event log, written by the supervisor under Enkidu's control, at:

```
~/.local/state/demiurge/creatures/<id>/events.jsonl
```

mode 0640, owned by Enkidu's uid, group-readable by an `angels` group. Every observable event the Creature does — capability call start/end, `think()`, LLM exchange, lifecycle event — appended with a stable envelope:

```json
{ "schema_version": 1, "creature_id": "...", "event_id": "<UUIDv7>", "ts": "<ISO8601 µs>",
  "kind": "tool.call.start | tool.call.end | think | llm.exchange | lifecycle",
  "correlation_id": "<event_id of parent action>", "data": { /* kind-specific */ } }
```

UUIDv7 is sortable and embeds the millisecond timestamp, so cross-referencing audit and narrative is a one-field `JOIN ON event_id`. Single time source, single ID namespace, single envelope schema. Angels don't have to coordinate — the feed is the synchronizer.

Angels are *projections* of the feed:

- **Enkidu's audit angel** projects `tool.call.*` rows into the tamper-evident audit log (the existing `audit/<date>.jsonl`).
- **Mnemosyne's memory angel** (v0.13+) projects the full stream into Mnemosyne's narrative store, summarizing locally before upload.
- Future angels project their own slices.

The Creature has no journal, no diary, no scratchpad. Its `think("...")` calls are *spoken aloud for the audit angel* — they land in the feed as `kind: think` events. There is no journal API in any Creature's code. Standardization comes for free: every Creature looks the same to its angels, regardless of what it does.

### Permission immutability and the no-summon rule

A Creature's blessings are set at forge time and **do not change for its lifetime**. Want different permissions? Retire (Hades) and forge fresh (Hephaestus). No live mutation. Only gods grant blessings; only Sol approves the underlying policy.

A Mortal **cannot** summon another Creature directly. Spawning is a god-level act:

- v0.11: only Sol can spawn (via `demiurge hire install / spawn` CLI).
- v0.12+: Mortals can request a spawn from Zeus via a blessed `zeus.request_spawn`-shaped tool. Zeus evaluates, gathers blessings, asks Hephaestus to forge. Iris is in the path *only* when the request originates from Sol (she translates dialogue → structured intent → Zeus).

Mortals working *with* each other within their existing capabilities is fine — they communicate via the bus or call existing Beasts/Automatons via blessed tools. Static authority graph, no dynamic permission lending. **Very microservices.**

### Hephaestus's forge flow (canonical)

Every forge follows the same order, fail-fast at the first failure:

1. **validate** the manifest (schema, capability shape, secret-name shape, mode/runtime alignment for powers)
2. **authorize the caller** — is the entity that requested this forge allowed to spawn this kind of Creature? In v0.11 the caller is always Sol via CLI, so this is trivial. In v0.12+ Zeus is the typical caller; a Mortal-originated request goes through Zeus's blessing first.
3. **mint the `creature_id`** (UUIDv7) + write the registry row + create the supervisor record (default-deny policy block)
4. **collect blessings** (parallel) — for each requested capability, route to the owning god, get a `Blessing | Denial`. Any required denial fails the forge with a structured "god X denied Y for reason Z."
5. **collect angel commissions** (parallel) — each god decides whether it wants an angel attached. Enkidu always says yes. Mnemosyne (v0.13+) always says yes. Others optional.
6. **operator approval** if any blessing requires Sol's confirmation per existing approvals primitive.
7. **install missing system deps** if the manifest requires apt/brew packages not present, via the installer Mortal's plan-and-approve flow.
8. **materialize** the runtime artifact (systemd unit per `runtime` block: webhook → uvicorn unit; listener → long-running service; polling → service + timer; request-based-only → no unit). Spawn supervisor entries for Creature + angels. Start the audit angel first.
9. **activate** — start the Creature's process. Registry row → status `alive`.

Idempotent. Re-forging an already-alive Creature is a no-op. Restart-from-aborted-step works.

### Cross-god access rules

- **Reads:** allowed. Enkidu can read Mnemosyne's narrative for incident investigation. Mnemosyne can read Enkidu's audit for cross-referencing.
- **Writes:** forbidden. Each god owns its own substrate; no god writes to another's storage. This keeps the integrity guarantees clear: Enkidu's audit log is integrity-protected because Enkidu is the only writer.
- **Mortals see one source of truth for memory:** `tools.memory.recall(query)` hits Mnemosyne. They never see the audit log, the observation feed, or any god's internal state directly.
