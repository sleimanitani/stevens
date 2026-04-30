# Protocol — Approvals

> **Status:** Draft v1 — design ahead of implementation (delivered in v0.3-installer-and-approvals).
> **Audience:** Enkidu's policy evaluator implementer; future agents adding approval-gated capabilities; Sol as operator.
> **Charter ref:** STEVENS.md §3 (Security architecture), §3.13 (Approval gates).

The **Approvals primitive** is how Enkidu lets Sol gate capabilities that need explicit human authorization without making him say yes to every individual call. It's a system-level mechanism, not specific to the installer — every future approval-gated capability (payments, credential rotation, autonomous-send for the email/whatsapp agents, etc.) goes through this same primitive.

---

## 1. Two layers

| Layer | Lifetime | Granularity | Used when |
|---|---|---|---|
| **Per-call approval** | one-shot | exact call (capability + caller + params) | first time, unusual, high-risk |
| **Standing approval** | hours / days / sessions / forever | a *class* of calls matching predicates | repeated, well-understood patterns |

A call to an approval-gated capability is gated as:

```
1. Match against active standing approvals (in-memory, O(1)-ish).
   ├─ matches → execute immediately, audit-log with the standing-approval id
   └─ no match → enqueue per-call approval, block the call until decided
2. Per-call: Sol decides.
   ├─ approved → execute, audit-log with the per-call approval id
   ├─ approved + promote to standing → execute + grant a standing approval (CLI offers durations + condition tightening)
   └─ rejected → fail the call with a structured error, audit-log
```

Standing approvals reduce toil. Per-call approvals stay around for first-time / unusual / surprising actions.

---

## 2. Standing approvals — orthogonal predicates

A standing approval is a small set of **independent, optional predicates**. Missing predicates mean "any" for that field. A call matches an approval iff every *specified* predicate matches.

```
standing_approval:
  capability:   <literal>           # always required
  caller:       <literal>           # always required
  expires_at:   <timestamp | null = forever | "session:<id>">
  granted_at:   <timestamp>
  granted_by:   <operator name>
  rationale:    <free text>

  # Predicates — all optional
  mechanism:    <literal>           | absent = any
  source:       <regex>             | absent = any
  packages:     <glob | set>        | absent = any
  param_matchers: { key: matcher, … } | absent = no extra constraints
```

### Examples

```yaml
# "Trust apt installs from any signed source for routine deps, 30 days."
- capability:   system.execute_privileged
  caller:       installer
  mechanism:    apt
  expires_at:   2026-05-30T...
  rationale:    "routine system dep installs"

# "Trust pip installs of pdfplumber/pillow with pinned hashes, forever."
- capability:   system.execute_privileged
  caller:       installer
  mechanism:    pip
  packages:     [pdfplumber, pillow]
  param_matchers:
    sha256: { in: [<hash1>, <hash2>] }
  rationale:    "vetted python deps for pdf reader"

# "Trust deb.debian.org as a source regardless of mechanism, 90 days."
- capability:   system.execute_privileged
  caller:       installer
  source:       "^deb\\.debian\\.org/.*"
  expires_at:   2026-07-29T...
  rationale:    "trust the distro maintainers for source classification"

# "Trust this exact caller to call gmail.create_draft on Sol's accounts forever
#  (this is the equivalent of a normal allow rule — included to show that
#  approvals can subsume policy when desired)."
- capability:   gmail.create_draft
  caller:       email_pm
  param_matchers:
    account_id: { glob: "gmail.*" }
  expires_at:   null
  rationale:    "email_pm drafts only, never sends; covered by draft-only constraint"
```

### Predicate matcher types

| Type | YAML form | Semantics |
|---|---|---|
| literal | `mechanism: apt` | exact string match |
| glob | `account_id: { glob: "gmail.*" }` | `fnmatch.fnmatchcase` |
| regex | `source: { regex: "^deb\\..*$" }` | full re.search |
| set | `packages: { in: [a, b, c] }` | membership |
| range | `count: { ge: 0, le: 100 }` | numeric bounds |
| sha256-set | `sha256: { in: [<hash>, <hash>] }` | constant-time comparison |

The primitive matchers are deliberately small. New ones added only when a real call needs them.

---

## 3. Per-call approvals

When no standing approval covers a call:

1. Enkidu enqueues a row in `approval_requests` (status=pending) and returns a `BLOCKED` response carrying the request id and a human-readable summary.
2. The caller (agent) treats `BLOCKED` as a retryable wait — it MAY poll, OR more typically, the agent's caller (the operator's CLI, e.g. `stevens dep ensure tesseract-ocr`) blocks on the request id.
3. Sol runs `stevens approval list`, sees the pending request with full call detail (capability, caller, params, summary, rationale provided by the agent).
4. Sol approves (`stevens approval approve <id>`) or rejects (`stevens approval reject <id> --reason "…"`).
5. On approve, Enkidu re-runs the original call from the queued envelope, executes it, audits, returns the result via the original waiting client (or via a result-publication topic the caller subscribed to).
6. On reject, Enkidu records the rejection, audits, and returns a structured `DENIED` to the caller.

### Promotion at approval time

The `approve` CLI offers, when the operator confirms:

```
$ stevens approval approve 7f3...
   call: system.execute_privileged caller=installer
   plan: apt install tesseract-ocr (source: deb.debian.org bookworm main, sha256: …)
   approve? [y/N] y

   promote to a standing approval? [N/session/30d/forever] 30d

   tighten predicates? (current: capability + caller + mechanism=apt + source=^deb\.debian\..*$)
     [enter to keep, or list packages to scope to]: <enter>

   rationale [optional]: routine system dep installs
   → standing approval granted: id=a1b2…, expires 2026-05-30
   → original call executed, exit 0, took 4.2s
```

Promotion is an opt-in — `[N/...]` defaults to no.

---

## 4. Lifecycle

### 4.1 Grant

Two paths:

- **Direct grant**: `stevens approval standing grant --capability X --caller Y [--mechanism …] [--source …] [--packages …] [--param k=v] [--duration N]`. Used when Sol knows ahead of time that a class of action is acceptable.
- **Promoted from a per-call**: see §3.

Either path writes a row to `standing_approvals` and signals Enkidu to refresh its in-memory cache.

### 4.2 Match (the hot path)

In Enkidu, every call to a `requires_approval: true` capability:

```
fn check_approval(call) -> ApprovalDecision:
    # in-memory list, indexed by (capability, caller)
    candidates = standing_index.get((call.capability, call.caller), [])
    for sa in candidates:
        if sa.expires_at and sa.expires_at < now():  continue
        if sa.revoked_at:  continue
        if not all(predicate.matches(call.params) for predicate in sa.predicates):
            continue
        return Approved(via=sa.id)
    return RequiresPerCall
```

This runs O(k) where k is the number of standing approvals matching `(capability, caller)`, typically 0–5. **No DB hit on the hot path.** The index loads at boot and refreshes on grant/revoke.

### 4.3 Expire

`expires_at` is checked at match time. Expired approvals stay in the DB (audit trail) but no longer match.

Special values:
- `null` — never expires (until revoked).
- `<timestamp>` — expires at that wall-clock UTC time.
- `"session:<session_id>"` — expires when Enkidu's current `session_id` changes (i.e., on next Enkidu restart). The session_id is generated at boot and held in memory only.

### 4.4 Revoke

`stevens approval standing revoke <id>` writes `revoked_at = now()` to the DB **and signals Enkidu to drop the entry from its in-memory index**. The signal is a SIGHUP to Enkidu's process (or, if running locally, a small "refresh" UDS message on a side channel — implementation detail).

If Enkidu is not running, revoke just writes the DB; on next Enkidu boot, the revoked row isn't loaded.

---

## 5. Storage — `standing_approvals` and `approval_requests`

```sql
-- migration 005_standing_approvals.sql
CREATE TABLE standing_approvals (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    capability      TEXT NOT NULL,
    caller          TEXT NOT NULL,
    predicates      JSONB NOT NULL DEFAULT '{}'::jsonb,
    expires_at      TIMESTAMPTZ,                 -- null = forever
    expires_session TEXT,                         -- non-null = session-bound
    granted_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    granted_by      TEXT NOT NULL,
    rationale       TEXT,
    revoked_at      TIMESTAMPTZ,
    revoked_by      TEXT
);

CREATE INDEX standing_approvals_active_idx
    ON standing_approvals (capability, caller)
    WHERE revoked_at IS NULL;

-- migration 006_approval_requests.sql
CREATE TABLE approval_requests (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    capability      TEXT NOT NULL,
    caller          TEXT NOT NULL,
    params_summary  TEXT NOT NULL,
    full_envelope   JSONB NOT NULL,              -- the original signed request, replayable
    rationale       TEXT,                          -- supplied by the agent at request time
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'approved', 'rejected', 'expired', 'failed')),
    decided_at      TIMESTAMPTZ,
    decided_by      TEXT,
    decision_notes  TEXT,
    promoted_standing_id UUID,                   -- FK to standing_approvals if promoted
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX approval_requests_pending_idx
    ON approval_requests (status, created_at) WHERE status = 'pending';
```

`predicates` is JSONB so the matcher schema can extend without further migrations.

---

## 6. CLI surface

```
stevens approval list                                   # pending per-call requests
stevens approval show <id>                              # full detail of one request
stevens approval approve <id> [--standing-for <dur>] [--tighten ...]
stevens approval reject <id> --reason "..."

stevens approval standing list [--include-expired]      # active standing approvals
stevens approval standing show <id>
stevens approval standing grant
    --capability <c> --caller <c>
    [--mechanism <m>] [--source <re>] [--packages a,b]
    [--param k=v ...]
    [--duration <30d|session|forever>]
    [--rationale "..."]
stevens approval standing revoke <id>
```

All of these talk directly to Postgres + Enkidu's refresh signal — no separate approval-management capability. The CLI authenticates with the sealed-store passphrase (operator identity), the same way `secrets` and `agent provision` do.

---

## 7. Audit semantics

Every approval-gated call produces an audit line with one of:

- `outcome: ok, approval_via: standing/<sa_id>` — silent execution under standing approval.
- `outcome: ok, approval_via: per_call/<req_id>` — executed after Sol's explicit approval.
- `outcome: blocked, approval_request: <req_id>` — blocked, awaiting decision.
- `outcome: deny, approval_request: <req_id>, error_code: REJECTED` — rejected by Sol.
- `outcome: deny, error_code: NO_POLICY` — capability not allowed for this caller (no approval flow even started).

The `approval_via` field links the audit log to the approval that authorized the call, so post-hoc reconstruction is unambiguous.

---

## 8. Capability declaration — opting in

A capability is approval-gated by setting `requires_approval: true` in `security/policy/capabilities.yaml`:

```yaml
agents:
  - name: installer
    allow:
      - capability: system.read_environment
        # not approval-gated; always allowed
      - capability: system.execute_privileged
        requires_approval: true
        rationale_required: true     # agent must supply a rationale string with each call
```

`rationale_required: true` is a per-capability flag that forces the calling agent to include a `rationale` field in its request. The rationale becomes part of the per-call approval request shown to Sol. (Standing approvals don't use the call-time rationale — they have their own grant-time rationale.)

---

## 9. Future capabilities that will use this primitive

Not in scope for v0.3, but worth listing so the design stays general:

- **`payment.charge`** — required when the v0.2+ payment flow lands. Approval gate is "Sol confirms each charge above $X" and "standing approve recurring vendors."
- **`credentials.rotate`** — when Enkidu rotates an OAuth token before expiry, gated to prevent silent rotation if the old one is still working.
- **`gmail.send`** — there is no `gmail.send` capability today (draft-only). When/if we add it, it'll be approval-gated.
- **`whatsapp.send_text` for personal numbers** — once Baileys lands, sends to non-replied-to contacts may be approval-gated.

The point: standing approvals scale to all of these without code changes. The matcher schema is the load-bearing thing.

---

## 10. Anti-patterns to refuse

- **Auto-promoting per-call approvals to standing.** Never. Promotion is always opt-in by the operator at approve-time.
- **Capability-handle / token bypass.** The agent doesn't get a "you're approved" token it can present to bypass Enkidu. Every call goes through Enkidu; the approval check is fast but it always happens.
- **Approving "all of capability X" without a caller.** Approvals are always (capability, caller, …) — there's no global "allow this capability for any caller" because that would defeat the per-agent isolation principle.
- **Hard-coding standing approvals in YAML.** Standing approvals live in the DB, not in `capabilities.yaml`. Mixing them would mean re-deploying to revoke. The DB is queryable, signal-able, audit-friendly — it's the right home.
- **"Just trust this agent forever."** No agent gets blanket trust. Standing approvals are scoped (predicate-bounded) and revocable. "Forever" means "until revoked," not "no further oversight."

---

## 11. References

- `docs/architecture/agent-isolation.md` — why we need approvals at all.
- `docs/protocols/privileged-execution.md` — the most prominent first user of this primitive.
- `docs/protocols/security-agent.md` — wire protocol for talking to Enkidu.
- STEVENS.md §3.13 — charter pointer to this doc.
