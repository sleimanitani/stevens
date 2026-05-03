# The gods — quick reference

A condensed table of every god (and reserved name) for at-a-glance reference. **Authoritative source is [`DEMIURGE.md`](../../DEMIURGE.md) §1.1**; this file restates the same content in a shorter form for navigation. Update both together.

Also see:
- [`pantheon.md`](pantheon.md) — full architecture (three layers, four creature kinds, angel pattern, observation feed, forge flow, opacity rules)
- [`agent-isolation.md`](agent-isolation.md) — capability grant width + Mortal lifecycle

## At a glance

```
                          ┌─────────────┐
                          │  Demiurge   │  substrate (not a god)
                          └──────┬──────┘
                                 │
            ┌────────────────────┴────────────────────┐
            │              The Pantheon               │
            │                                         │
            │  Enkidu     ← sole secret broker        │
            │  Arachne    ← web fetch + search        │
            │  Sphinx     ← PDF/document strategy     │
            │  Janus      ← operator-assisted browser │
            │  Hephaestus ← creator of Creatures      │
            │  Hades      ← destroyer/archivist       │
            │  Mnemosyne  ← all-history keeper        │
            │  Iris       ← personal UI for Sol       │
            │  Zeus       ← chairman / coordinator    │
            └─────────────────────┬───────────────────┘
                                  │
                  ┌───────────────┴───────────────┐
                  │        The Creatures          │
                  │                               │
                  │  Mortals     full agency      │
                  │  Beasts      model, no agency │
                  │  Automatons  no LLM           │
                  │  Angels      god-extensions   │
                  │  (Prophet)   reserved future  │
                  └───────────────────────────────┘
```

## Demiurge — the substrate (not a god)

| | |
|---|---|
| **What it is** | The pre-Olympian craftsman. The runtime + supervisor + bootstrap + install machinery. |
| **Has LLM?** | No |
| **Reasons?** | No — it executes decisions made by gods |
| **Faces** | Sol-as-operator (CLI), the OS |
| **Status** | Shipped (v0.10 brought native install + systemd user units) |
| **Code** | `demiurge` package (was `stevens_security`); `demiurge` CLI |

## The Pantheon

### Shipped

| God | Code id | Domain | Greek myth | Notes |
|---|---|---|---|---|
| **Enkidu** | `security_agent` | Sole secret broker. Sealed store. Capability dispatch + policy. Audit log integrity. Observes every Creature with a mandatory audit angel. | Companion of Gilgamesh, wild-born guardian | Non-overrideable. Nothing in the system overrides Enkidu's decisions. |
| **Arachne** | `web` | Async web fetch + search. Per-domain allowlist, TTL cache, rate limiter, modular search backends. | Mortal weaver who challenged Athena, transformed into a spider | v0.3.1 |
| **Sphinx** | `pdf` | PDF strategy router — chooses pdfplumber, OCR (tesseract), or IBM Docling per document. | Poser/answerer of riddles | v0.4 |
| **Janus** | `janus` | Operator-assisted browser-driven OAuth/config-screen helper. | Roman: god of doorways, transitions, beginnings; two-faced | v0.7 |

### Planned

| God | Code id | Domain | Greek myth | Milestone |
|---|---|---|---|---|
| **Hephaestus** | `forge` | Creator of Creatures. Validates manifest, gathers blessings, materializes runtime, attaches angels. Owns Apotheosis / Succession (forge) / Binding (forge). | Smith of the gods, builder of automata | v0.11 |
| **Hades** | `underworld` | Destroyer + archivist. Tears down runtime, revokes capabilities, archives state, retires angels. Owns Fading / Exile / Ragnarök / Succession (sever) / Binding (archival). | Lord of the dead, judge of finished lives | v0.11 |
| **Iris** | `interface` | Personal UI agent for Sol. Knows preferences (modality, channels, quiet hours, vocal tone). Translates dialogue ↔ structured intents (handed to Zeus). Notification routing. **Sol-facing only — does not orchestrate gods.** | Messenger goddess, rainbow bridge between gods and mortals | v0.12 |
| **Zeus** | `coordination` | Chairman of the Pantheon. Receives structured intents (from Iris on Sol's behalf, or from Mortals via blessed tools). Reasons about which gods to involve. Dispatches multi-god operations. Judges cross-god conflicts. Only god whose domain is *coordination itself*. | King of the gods, head of the divine council | v0.12 or v0.13 |
| **Mnemosyne** | `memory` | Keeper of all history. Owns the persistent record of what has happened. Assigns each Creature's observation feed to a storage location (sharding + load balancing). Provides `tools.memory.recall(query)`. Commissions a memory angel for every Creature once she ships. | Titaness of memory, mother of the Muses | v0.13 |

### Reserved

These names are claimed for future Pantheon roles; not built.

| Reserved | Possible role | Why reserved |
|---|---|---|
| **Mimir** | Knowledge / wisdom layer distinct from Mnemosyne's raw history. If memory and structured-knowledge ever cleave, Mimir would own the latter. | Norse: severed head of wisdom; predates Aesir-Vanir war. |
| **Atlas** | Substrate-level role distinct from Demiurge — possibly the supervisor / process-tree layer if it ever needs its own god. | Greek: Titan who holds up the heavens. |

## The Creatures — four kinds (+ one reserved)

Created by Hephaestus on a god's blessing, observed by Enkidu's audit angel from birth, retired by Hades.

| Kind | LLM? | Agency? | Visible to Sol? | Visible to Creatures? | Examples |
|---|---|---|---|---|---|
| **Mortal** | yes | yes (scoped) | yes | yes | email_pm, trip_planner, installer |
| **Beast** | yes (model only) | no (function-shaped) | yes | yes (called as blessed tools) | image_gen, embedder, summarizer, OCR |
| **Automaton** | no | no (deterministic) | yes | only via bus events | scheduler, rss_reader, log_shipper |
| **Angel** | optional | bound to one god | **no** (Sol blind without future Prophet) | **no** | Enkidu audit angel, Mnemosyne memory angel |
| *Prophet* (reserved) | yes | privileged perception | yes | sees angels | not yet built |

**Beast vs. tool:** model-driven and stochastic = Beast (Creature with identity, audit trail, retire-able). Deterministic transformation = tool (capability function, no separate identity).

**Angel opacity is hard.** Process-isolated, separate uid where possible; not in `hire show`, not in `audit tail`, not in any Creature's `tools.list()`. Sol sees them only via the future Prophet credential.

## Lifecycle vocabulary

| Term | Meaning | Owner |
|---|---|---|
| **Apotheosis** | Mortal capability promoted into Pantheon (or initial spawn) | Hephaestus |
| **Succession** | New implementation replaces existing Pantheon member in same domain | Hephaestus + Hades |
| **Binding** | Retired but kept reachable for legacy state | Hephaestus + Hades |
| **Fading** | Pantheon member's domain no longer broadly needed | Hades |
| **Exile** | Pantheon member pulled after a problem | Hades |
| **Ragnarök** | Full removal | Hades |

Use the term, not a paraphrase, in plans + docs + commit messages.
