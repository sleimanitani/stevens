# CLAUDE.md: Skills and shared tools layer

Instructions for adding a shared skills system to the personal assistant repo.
This document is the spec. Read it fully before starting. Ask before deviating.

## Context (what you already know)

The repo is a multi-agent personal assistant. Five layers: human interface,
agents, agent runtime, tools/channels, resources. Events flow through a
Postgres-backed bus. Agents subscribe by topic pattern. Multi-account is
first-class. All v0.1 agents are draft-only — no autonomous sends.

The **security agent** was built first, ahead of the email agent, and is
a first-class citizen of the system. Any new infrastructure you add must
work with the security agent on day 1, not as an afterthought. When this
document refers to "existing agents," that includes the security agent.
Do not regress its behavior.

## What you are adding

A `skills/` top-level package that holds two kinds of reusable knowledge,
both shared across agents:

- **Tools** — real Python functions that agents call. Code.
- **Playbooks** — procedural knowledge in Markdown. Loaded into agent context
  at runtime when relevant.

**The primary goal is tool reuse.** The motivating example: if we build a
robust PDF reader that handles bad scans, handwriting, and tables split
across pages, every agent that ever encounters a PDF should import and use
that exact tool. No agent should reinvent PDF reading. The same applies to
LinkedIn lookups, DNS queries, property records, receipt parsing, whatever
emerges. Write once, use everywhere.

Playbooks are secondary but present — they solve the prompt-bloat problem
for agents that handle many situation types (the security agent judging
different threat classes, the email agent routing appointment vs followup
vs task vs blocker, etc.).

---

## Core principle: tools ≠ playbooks

**Do not conflate them.** Hermes and Browser Harness both call their
learned artifacts "skills" and mix code with procedural knowledge. We
separate them because they have different review workflows, different
storage, different retrieval, and different failure modes.

| | Tools | Playbooks |
|---|---|---|
| Form | Python functions | Markdown documents |
| Review | Code review, type checks, tests | Content review, dry run |
| Retrieval | Imported statically or via registry | Loaded into prompt context |
| Versioning | Semantic versions, deprecation | Timestamps, supersede chain |
| Failure mode | Exceptions, wrong output | Prompt bloat, drift, wrong procedure |
| Owned by | Code reviewers | Agent authors + operator |

Treat them as two separate systems that happen to share a top-level
directory.

---

## Directory layout

Add this at the repo root, next to `shared/`, `agents/`, `channels/`:

```
skills/
├── pyproject.toml              # uv workspace member
├── src/skills/
│   ├── __init__.py
│   ├── registry.py             # loads registry.yaml, exposes lookup API
│   ├── retrieval.py            # playbook retrieval (trigger-match for v1)
│   ├── tools/
│   │   ├── __init__.py         # exports all tools as LangChain BaseTools
│   │   ├── pdf/
│   │   │   ├── __init__.py
│   │   │   └── read_pdf.py     # the motivating example
│   │   ├── research/
│   │   │   └── ...
│   │   ├── security/
│   │   │   └── ...             # whatever the security agent has extracted
│   │   └── email/
│   │       └── ...
│   └── playbooks/
│       ├── __init__.py
│       ├── loader.py           # markdown + frontmatter parser
│       ├── email/
│       │   └── *.md
│       └── security/
│           └── *.md
├── registry.yaml               # single index of all tools + playbooks
└── proposed/                   # agent-proposed additions awaiting review
    ├── tools/
    └── playbooks/
```

Add `skills` to the workspace members in the root `pyproject.toml`.

---

## Tools: schema and sharing

### Every tool is a Python file with a standard shape

```python
# skills/src/skills/tools/pdf/read_pdf.py
"""Robust PDF reader.

Handles: text PDFs, scanned PDFs (OCR via tesseract), tables that span
multiple pages (merged with page-boundary detection), handwriting where
legible.

Does not handle: encrypted PDFs (returns structured error), PDFs larger
than 500 pages (returns partial with warning).
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from langchain_core.tools import StructuredTool


# --- Metadata: the single source of truth about this tool ---
TOOL_METADATA = {
    "id": "pdf.read_pdf",
    "version": "1.0.0",
    "created_by": "security_agent",       # first agent to need this
    "approved_by": "sol",
    "approved_at": "2026-05-01",
    "scope": "shared",                     # "restricted" | "shared"
    "allowed_agents": None,                # null = all agents (if shared)
    "external_deps": ["pdfplumber", "pytesseract", "pillow"],
    "safety_class": "read-only",           # "read-only" | "read-write" | "destructive"
}


class ReadPDFInput(BaseModel):
    path: str = Field(description="Absolute path to the PDF file")
    mode: Literal["text", "tables", "both"] = "both"
    ocr_fallback: bool = Field(default=True, description="Fall back to OCR if text extraction yields < 100 chars")


def _read_pdf(path: str, mode: str = "both", ocr_fallback: bool = True) -> dict:
    """Implementation. Pure function. No side effects outside the file system."""
    # ... actual extraction ...
    return {"text": "...", "tables": [...], "pages": N, "used_ocr": bool}


def build_tool() -> StructuredTool:
    return StructuredTool.from_function(
        func=_read_pdf,
        name="read_pdf",
        description=(
            "Extract text and tables from a PDF, including scanned PDFs and "
            "tables that span multiple pages. Returns {text, tables, pages, "
            "used_ocr}. Use this any time you encounter a PDF — do not write "
            "your own PDF reader."
        ),
        args_schema=ReadPDFInput,
    )
```

### The sharing rule

- **`scope: restricted`** — only the creating agent can see this tool.
  This is the default for agent-proposed tools until promoted.
- **`scope: shared`** — any agent whose `registry.yaml` entry includes
  `tools: ["*"]` or names this tool gets it.
- **`allowed_agents`** — optional whitelist when a tool is shared but
  not universal (e.g. a tool that accesses sensitive data only
  specific agents should touch).

### Top-level `skills/registry.yaml`

```yaml
tools:
  - id: pdf.read_pdf
    path: skills/src/skills/tools/pdf/read_pdf.py
    scope: shared
    safety_class: read-only

  - id: research.linkedin_lookup
    path: skills/src/skills/tools/research/linkedin_lookup.py
    scope: shared
    safety_class: read-only
    external_deps: [browser-harness]

  - id: security.check_sender_reputation
    path: skills/src/skills/tools/security/check_sender_reputation.py
    scope: restricted
    allowed_agents: [security_agent]
    safety_class: read-only

playbooks:
  - id: email.appointment_request
    path: skills/src/skills/playbooks/email/appointment_request.md
    applies_to_topics: [email.received.*]
    applies_to_agents: [email_pm]

  - id: security.suspicious_attachment
    path: skills/src/skills/playbooks/security/suspicious_attachment.md
    applies_to_topics: [email.received.*]
    applies_to_agents: [security_agent]
```

`registry.py` exposes two lookups:

```python
def get_tools_for_agent(agent_name: str) -> list[BaseTool]:
    """Return all tools this agent has access to."""

def get_playbooks_for(agent_name: str, event: BaseEvent) -> list[Playbook]:
    """Return relevant playbooks for this agent + event, trigger-matched."""
```

Agents in `agents/src/agents/*/agent.py` stop constructing their own tool
lists directly — they call `get_tools_for_agent(self.name)`.

---

## Playbooks: schema and retrieval

Playbooks are Markdown with YAML frontmatter:

```markdown
---
id: email/appointment_request
version: 1
created_by: email_pm
created_at: 2026-05-02
approved_by: sol
approved_at: 2026-05-02
applies_to_topics: [email.received.*]
applies_to_agents: [email_pm]
triggers:
  - regex: "(?i)(meeting|call|schedule|available|calendly|book a time)"
supersedes: null          # id of older playbook this replaces
status: active            # proposed | active | deprecated
---

## When to apply
An incoming email requesting a meeting, call, or appointment.

## Procedure
1. If the sender included a Calendly link, acknowledge and note Sol will book.
2. Otherwise, draft a reply asking for 2–3 time windows they prefer.
3. Apply label pm/appointment-pending.
4. Log a followup (direction=waiting_on_me, deadline=2 business days).

## Variants
- Urgent language ("ASAP", "today"): also apply pm/urgent.
- Recurring meeting: do not draft. Flag for Sol.

## Anti-patterns
- Do not suggest specific times — Sol's calendar isn't integrated yet.
- Do not commit to a day/time ("tomorrow works"). Only ask for preferences.

## Tools this playbook expects
- gmail.create_draft
- gmail.add_label
- email_pm.log_followup
```

### Retrieval (v1: trigger-match)

`skills/src/skills/retrieval.py`:

```python
def get_playbooks_for(agent_name: str, event: BaseEvent) -> list[Playbook]:
    """
    For v1, match by:
      1. agent_name must be in playbook.applies_to_agents (or the list is empty)
      2. event.topic must match one of applies_to_topics patterns
      3. at least one trigger regex must match event content

    Returns playbooks ranked by specificity (more specific triggers first).
    Cap at 5 playbooks returned to prevent context bloat.
    """
```

Agents inject matching playbooks into their prompt. The `email_pm` prompt
shrinks to the general triage rules; situation-specific procedures move
into playbooks loaded per-event.

### v2 upgrades (not now, but keep retrieval interface stable)
- Semantic retrieval via pgvector
- Agent-selected retrieval via a cheap classifier call

Design the retrieval function so these can drop in without changing
caller code.

---

## The proposal → review → promote flow

Agents **propose** new tools and playbooks. They do **not** silently write
to the shared library. This is a hard rule.

### Proposal mechanism

Agents emit a proposal via a dedicated tool:

```python
propose_skill(
    kind="tool" | "playbook",
    title="...",
    body="...",             # python source or markdown
    rationale="...",        # why this is worth adding
    originating_event_id=...,  # traceability
)
```

`propose_skill` writes a row to a `skill_proposals` table and drops the
body in `skills/proposed/tools/` or `skills/proposed/playbooks/`.

The agent does NOT get to use its own proposal. The proposal must be
reviewed by Sol. If the agent needs the capability *right now*, it
accomplishes the immediate task through existing tools and records the
proposal for future improvement.

### Out-of-band distillation (optional, v2)

A `skills/distiller.py` process runs nightly. Reads Langfuse traces from
the last 24h, asks a stronger model: "what reusable patterns emerged?"
Generates proposals based on observed behavior across agents. Same
review flow — nothing auto-promotes.

### Review CLI

Add `scripts/review_skills.py`:

```
$ uv run python scripts/review_skills.py list
[1] tool    pdf.read_pdf_v2         proposed 2026-05-10 by security_agent
[2] playbook email.blocker_triage   proposed 2026-05-10 by email_pm

$ uv run python scripts/review_skills.py show 1
... opens in $EDITOR ...

$ uv run python scripts/review_skills.py approve 1 --scope shared
Moved skills/proposed/tools/pdf/read_pdf_v2.py → skills/src/skills/tools/pdf/
Updated skills/registry.yaml

$ uv run python scripts/review_skills.py reject 2 --reason "unsafe pattern"
```

### Database table

Add a migration (new file: `resources/migrations/00X_skill_proposals.sql`):

```sql
CREATE TABLE IF NOT EXISTS skill_proposals (
  proposal_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  kind              TEXT NOT NULL CHECK (kind IN ('tool', 'playbook')),
  proposed_id       TEXT NOT NULL,
  proposing_agent   TEXT NOT NULL,
  body_path         TEXT NOT NULL,        -- path under skills/proposed/
  rationale         TEXT,
  originating_event UUID,
  status            TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'approved', 'rejected', 'superseded')),
  reviewed_by       TEXT,
  reviewed_at       TIMESTAMPTZ,
  review_notes      TEXT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ON skill_proposals(status, created_at) WHERE status = 'pending';
```

---

## Integration with existing agents

### Security agent (already exists — do not break)

The security agent was built before the email agent and has priority.
Whatever tools and playbooks it currently uses inline, extract them:

1. Find the security agent's tool list and custom tools (likely in
   `agents/src/agents/security/tools.py` or inline in `agent.py`).
2. For each tool that's genuinely reusable (e.g., URL reputation
   check, attachment scanner, sender verification), move it to
   `skills/src/skills/tools/security/` following the schema above.
3. For each case-specific procedure hardcoded in the system prompt
   (phishing patterns, suspicious attachment handling, credential
   leak response, etc.), extract to a playbook under
   `skills/src/skills/playbooks/security/`.
4. Update the security agent to use `get_tools_for_agent("security_agent")`
   and inject matching playbooks from `get_playbooks_for("security_agent", event)`.
5. **Run the security agent's existing test cases before and after.
   Output must be equivalent or better. Do not regress.**

### Email PM agent (next)

Same process applied to the email agent after security. Extract its
categorization tools and its situation-specific procedures. Starter
playbooks to write (or extract, if the logic already exists):

- `email/appointment_request.md`
- `email/followup_overdue.md`
- `email/task_extraction.md`
- `email/blocker_triage.md`
- `email/sensitive_escalation.md`
- `email/marketing_filter.md`

### Future agents

Any new agent added after this layer exists automatically gets access
to all `scope: shared` tools at the safety class it requests in its
`registry.yaml` entry. For example:

```yaml
# agents/src/agents/berwyn_deal/registry.yaml entry
- name: berwyn_deal
  subscribes: [...]
  tools:
    shared: ["*"]              # all shared tools
    exclude: [security.*]      # opt-out of security-restricted tools
    safety_max: read-write     # no destructive tools
```

---

## The PDF reader: build it as the first reference tool

Build `skills/src/skills/tools/pdf/read_pdf.py` as the canonical example
of a well-formed shared tool. It is also genuinely needed — the security
agent already encounters PDF attachments, and the email agent will too.

Requirements:

- Text extraction via `pdfplumber` (fast, clean for text-based PDFs)
- OCR fallback via `pytesseract` when text extraction returns < 100 chars
- Table extraction via `pdfplumber`'s `extract_tables()`
- **Cross-page table merging**: if a table ends at the bottom of page N
  and another table with the same column count starts at the top of
  page N+1, merge them. Non-trivial, but the whole point. Write a
  helper `_merge_cross_page_tables()`.
- Return a structured dict: `{text, tables, pages, used_ocr, warnings}`
- Safety: reject PDFs over 500 pages with a clear error; reject
  encrypted PDFs with a clear error.
- Tests in `skills/tests/test_read_pdf.py` with at least:
  - A text-only PDF
  - A scanned-only PDF
  - A PDF with a table spanning two pages
  - An encrypted PDF (expect error)

Register it in `skills/registry.yaml` as `scope: shared, safety_class: read-only`.

---

## Concrete task list

Work in this order. Do not skip ahead — each step builds on the previous.

1. **Scaffold `skills/`** — package structure, `pyproject.toml`, workspace
   registration in root `pyproject.toml`, empty `registry.yaml`.
2. **Write `registry.py`** with `get_tools_for_agent()` and
   `get_playbooks_for()` stubs that return empty lists. Wire them into
   the agent runtime so calls exist but return nothing.
3. **Write `playbooks/loader.py`** — parse frontmatter + body from
   Markdown files.
4. **Write `retrieval.py`** with the v1 trigger-match implementation.
5. **Add migration** `00X_skill_proposals.sql`. Apply it.
6. **Implement `propose_skill` tool** in a new `shared/tools/propose.py`
   that any agent can import. Writes to DB + filesystem.
7. **Build the PDF reader** at `skills/src/skills/tools/pdf/read_pdf.py`
   following the spec above. Include tests.
8. **Extract security agent's tools and playbooks.** Do this carefully
   — one at a time, with the security agent's behavior verified after
   each extraction. If a tool's extraction changes behavior, stop and
   ask before continuing.
9. **Build `scripts/review_skills.py`** — list, show, approve, reject.
10. **Extract email agent's playbooks** (if it exists yet). Write the
    five starter playbooks listed above.
11. **Update `DEVELOPMENT.md`** with a new section: "Adding a new tool,"
    "Adding a new playbook," "Reviewing proposals."
12. **Update the PRD** (`docs/prd.docx`) — new section on skills layer.
    You can do this last; it's descriptive, not load-bearing.

---

## Acceptance criteria

Before marking this done, verify all of:

- [ ] `uv sync` succeeds at the workspace root.
- [ ] All existing agents still work. Security agent behavior unchanged
      (verify with its existing tests or manual scenarios).
- [ ] `read_pdf` works on all four test PDFs. Table-spanning test case
      produces a single merged table, not two separate tables.
- [ ] At least one tool is shared between two agents (likely the PDF
      reader used by both security and email agents).
- [ ] Proposing a skill works end-to-end: call `propose_skill`, see it
      in `scripts/review_skills.py list`, approve it, confirm it appears
      in `skills/registry.yaml` and is loaded by the intended agent.
- [ ] The agents no longer build tool lists by hand — they use
      `get_tools_for_agent(name)`.
- [ ] Security agent retains priority: if a `scope: restricted` tool
      exists with `allowed_agents: [security_agent]`, no other agent
      can see or invoke it. Write a test.
- [ ] Draft-only constraint is not compromised anywhere in the new
      layer. `safety_class: destructive` tools require explicit opt-in
      in the agent's `registry.yaml` and are never in the default shared
      set.

---

## Do NOT do any of the following

- **Do not auto-approve proposals.** Ever. Review is human-only.
- **Do not let agents edit tools or playbooks that are already promoted.**
  They propose new versions; old versions stay immutable until
  superseded by review.
- **Do not flatten tools and playbooks into one "skills" concept.**
  They have different review workflows and different lifecycles.
- **Do not add a vector store for playbook retrieval yet.** Trigger-match
  first. Prove the retrieval interface works, then upgrade.
- **Do not give the email agent restricted security tools by default.**
  The security agent may need to inspect things the email agent should
  not see.
- **Do not weaken the draft-only constraint.** No new tool may send on
  any channel unless its safety_class is explicitly `destructive` AND
  the operator has opted the specific agent in.
- **Do not rename or restructure `shared/`, `channels/`, or `agents/`.**
  Add `skills/` alongside them. Don't touch the event bus or adapter
  layers.
- **Do not modify the security agent's prompt or tool surface without
  verifying behavior before and after.** If in doubt, extract one thing
  at a time and test.

---

## Questions to ask before starting

If any of the following are unclear, stop and ask:

1. What's the current shape of the security agent? Where are its tools
   defined, where is its prompt, what does its registry.yaml entry
   look like?
2. Does a Langfuse project exist already, and if so, should skill
   proposals include trace links?
3. Is there an existing PDF-reading mechanism in the security agent
   that should be the starting point for the shared tool?
4. Does Sol want the review CLI as a Python script, or as new `hermes`-style
   CLI subcommands integrated into a top-level `assistant` CLI?

Don't guess. Ask.

---

## Why this matters (keep this in mind as you work)

The whole architectural bet is that the cost of adding agents goes down
over time, not up. This layer is the mechanism for that. Every shared
tool written and reviewed is something no future agent has to build again.
Every playbook captured is domain knowledge that persists beyond the
session that produced it.

The PDF reader is the canonical example on purpose: once that tool exists
and is tested, no future agent — whether it's the security agent checking
a suspicious attachment, the email agent summarizing a contract, or a
future tax agent parsing receipts — ever has to think about PDFs again.
They just call `read_pdf`. That is the property we are building toward
at the system level.

Build with that in mind. If you catch yourself thinking "this is a tool
only this agent will ever need," the default answer is still to put it
in `skills/tools/` with `scope: restricted`. Promoting it later to
`shared` is trivial; extracting it from an agent's private code later is
not. Err on the side of shared architecture from day 1.
