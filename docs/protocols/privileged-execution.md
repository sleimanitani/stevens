# Protocol — Privileged Execution

> **Status:** Draft v1 — design ahead of implementation (delivered in v0.3-installer-and-approvals).
> **Audience:** the installer agent's implementer; future agents that need privileged actions; Enkidu's mechanism implementers.
> **Charter ref:** DEMIURGE.md §3 (Security architecture).

This document specifies the **plan → approve → execute → record** protocol for privileged actions. The first user is the installer agent (system package installs); the protocol generalizes to future privileged actions (mounting volumes, spawning long-lived subprocesses, running migrations, etc.).

The pattern: **agents propose plans (data); Enkidu validates and executes them (privilege).**

---

## 1. Roles

| Role | Has | Doesn't have |
|---|---|---|
| **Agent** (e.g. installer) | reads of host state, plan-building logic, its own scoped DB rows | sudo, network egress, sealed store, other agents' rows |
| **Enkidu** | sudo, the sealed store, plan grammar validators, audit log, approval gate | LLM reasoning, agent-specific domain knowledge, broad bus subscriptions |
| **Operator** (Sol) | all of the above via `demiurge` CLI | n/a |

The agent **never executes privileged commands itself**. It hands a plan to Enkidu and Enkidu does the work.

---

## 2. Capability surface

Three capabilities make up the protocol. Two are non-privileged (no approval gate) and one is privileged.

### 2.1 `system.read_environment` — non-privileged

Read-only host introspection. Returns a structured snapshot the agent can use to plan.

```
request:
  fields:                    # subset to read; minimizes data flow
    - os_release
    - package_manager
    - dpkg_status
        package: tesseract-ocr      # narrow query, not "list all packages"
    - opt_dirs
        path_pattern: /opt/demiurge/*

response:
  os_release:
    id: ubuntu
    version_id: "22.04"
    arch: x86_64
  package_manager:
    primary: apt
    available: [apt, dpkg, pip]
  dpkg_status:
    tesseract-ocr:
      installed: false
      version: null
  opt_dirs:
    /opt/demiurge/venvs: { exists: false }
```

Approval-gated: **no**. This is read-only, low-risk; agents can call it freely. It's still a capability (not a skill) because some of the reads (e.g. dpkg state) require Enkidu's host privilege to be cleanly observable.

### 2.2 `system.plan_install` — non-privileged

The agent submits a structured plan; Enkidu validates and returns a plan id.

```
request:
  mechanism: apt
  packages: [tesseract-ocr]
  source:
    repo: "deb.debian.org/debian"
    suite: bookworm
    component: main
  health_check:                       # structural success criterion
    type: dpkg_installed
    package: tesseract-ocr
  rollback:
    mechanism: apt
    operation: purge
    packages: [tesseract-ocr]
  rationale: "OCR fallback for skills.tools.pdf.read_pdf"

response:
  plan_id: <UUID>
  plan_validated: true
  validation_notes: []
  expires_at: <30 minutes from now>
```

Approval-gated: **no**. Validation is host-safe (no execution); Enkidu does the actual privileged work in the next step.

Validation rules (mechanism-specific; see §3):
- Allowed mechanisms: only those Enkidu has shipped (in v0.3, just `apt`).
- Command grammar: no shell metachars, no wildcards in package names, no flags outside the mechanism's allow-list.
- Source allow-list: per-mechanism (apt's source must be a known repo; later, pip's source must be PyPI; etc.).
- Health-check shape: structurally enforceable (file exists, dpkg reports installed, sha256 matches).
- Rollback: every plan ships with its inverse, validated to the same standard.

If any rule fails, returns `plan_validated: false` with structured reasons; no plan id issued.

### 2.3 `system.execute_privileged` — approval-gated

```
request:
  plan_id: <UUID>
  rationale: "tesseract needed for read_pdf OCR fallback"   # required at policy level

response (success):
  outcome: ok
  approval_via: standing/<sa_id>             # or per_call/<req_id>
  exit_code: 0
  stdout_hash: <sha256>                       # hashed for audit
  stderr_hash: <sha256>
  duration_ms: 4123
  health_check_result: passed
  inventory_id: <UUID>                       # row id in environment_packages

response (rejected):
  outcome: deny
  error_code: REJECTED
  approval_request: <req_id>
  decision_notes: "..."

response (blocked):
  outcome: blocked
  error_code: BLOCKED
  approval_request: <req_id>
  message: "approval pending; poll or wait for the result topic"
```

Approval-gated: **yes** (`requires_approval: true` in `capabilities.yaml`).

Behavior:
1. Look up plan by id. If not found / expired → fail.
2. Re-validate plan (defense in depth).
3. Check approval (see `approvals.md`):
   - Standing match → execute, audit with `approval_via: standing/<sa_id>`.
   - No standing → enqueue per-call, return `BLOCKED`.
4. Execute under Enkidu's privileged subprocess (see §4).
5. Run health check from the plan's `health_check` field.
6. On health-check pass → write inventory row, audit `ok`, return.
7. On health-check fail → run rollback, audit `health_check_failed`, return error.

### 2.4 `system.write_inventory` — non-privileged but integrity-protected

Append-only writes to the agent's scoped `environment_packages` rows. Called automatically by Enkidu at the end of `execute_privileged`; agents typically don't call it directly.

Exposed for cases where an agent needs to record state for an action that didn't go through `execute_privileged` (e.g. recording an existing-on-host install discovered via `read_environment`). The agent supplies the row, Enkidu sets `caller` from the verified caller name and stamps `recorded_at`.

---

## 3. Mechanisms

A **mechanism** is a strategy for installing or operating on system state. v0.3 ships one (`apt`); the abstraction supports more.

Each mechanism declares:

- **Plan grammar** — what fields are required, allowed flag values, source-format validator.
- **Validator** — pure function: `(plan) -> (validated_plan, validation_errors)`.
- **Executor** — the actual subprocess invocation, run by Enkidu.
- **Health-check evaluator** — given the plan's `health_check` and the post-exec state, return passed/failed.
- **Rollback validator** — confirms the rollback is the inverse of the install.

### 3.1 `apt` mechanism (v0.3)

```yaml
plan_grammar:
  mechanism: apt
  packages: [<list of debian package names>]   # /^[a-z0-9][a-z0-9.+-]*$/ regex per name
  source:
    repo: <known apt repo URL pattern>
    suite: <bookworm|jammy|focal|...>           # explicit allow-list
    component: <main|universe|...>
  flags_allow_list:
    - --no-install-recommends
    - -y
  flags_forbid_list:
    - --force-yes
    - --allow-unauthenticated
  health_check:
    type: dpkg_installed
    package: <package name from plan.packages>
  rollback:
    mechanism: apt
    operation: <remove | purge>
    packages: <subset of plan.packages>

executor:
  cmdline: ["apt-get", "install", "-y", "--no-install-recommends", *plan.packages]
  env: {DEBIAN_FRONTEND: noninteractive}
  timeout_seconds: 300

health_check_evaluator:
  type: dpkg_installed:
    cmd: ["dpkg-query", "--show", "--showformat=${Status}", <package>]
    expect: "install ok installed"
```

Out of scope for v0.3 (mechanism schema supports them, implementations land later):
- `pip`: pip-with-isolation into a per-tool venv at `/opt/demiurge/venvs/<tool>/`. Plans require sha256 hash pins.
- `git`: clone+checkout to `/opt/demiurge/repos/<name>/<commit>/`. Plans require pinned commit SHA + remote allow-list.
- `opt_dir`: download a binary to `/opt/<name>/<version>/`, sha256-verified, symlinked into `/opt/demiurge/bin/`.
- `container`: pull a container image by digest, register a service.

When a new mechanism lands, its grammar/validator/executor/health-check are added under `security/src/demiurge/mechanisms/<mech>.py`. The capability surface (`plan_install`, `execute_privileged`) doesn't change.

---

## 4. Execution under Enkidu

When Enkidu runs `system.execute_privileged`:

- Spawns a subprocess as `root` (Enkidu has CAP_SYS_ADMIN-equivalent inside its container; the container runs with the sudoers entries needed for the mechanism's executor).
- Forwards only the plan's executor cmdline and env; no shell interpolation, no shell at all (`subprocess.Popen` with `shell=False`).
- Captures stdout / stderr to memory (capped at 1 MiB; truncates with a marker).
- Hashes both, stores hashes in the audit line + raw streams in a per-trace log under `/var/lib/demiurge/exec_logs/<trace_id>.log` (mode 0o600, rotated weekly).
- Times out at the mechanism's `timeout_seconds`; on timeout, kills and runs rollback.
- Returns the exit code, hashes, and a structured execution record.

The executor itself is **stateless** — Enkidu's process state across calls is just the cache of standing approvals + the plan-id index, both of which are durable in Postgres.

---

## 5. Inventory

The `environment_packages` table records what's been installed and by whom.

```sql
-- migration 007_environment_packages.sql
CREATE TABLE environment_packages (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    caller        TEXT NOT NULL,                 -- which agent installed it
    name          TEXT NOT NULL,
    version       TEXT,
    mechanism     TEXT NOT NULL,
    location      TEXT,                          -- /opt/.../bin/X for opt_dir; null for apt
    sha256        TEXT,                          -- if applicable for the mechanism
    plan_id       UUID NOT NULL,                 -- traceability to the plan that installed it
    installed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    removed_at    TIMESTAMPTZ,                   -- soft delete; preserves audit
    health_status TEXT NOT NULL DEFAULT 'unknown'
                  CHECK (health_status IN ('unknown', 'passed', 'failed', 'rolled_back'))
);

CREATE INDEX environment_packages_active_idx
    ON environment_packages (caller, name)
    WHERE removed_at IS NULL;

CREATE INDEX environment_packages_global_idx
    ON environment_packages (name, mechanism)
    WHERE removed_at IS NULL;
```

### 5.1 Per-agent scoping vs operator view

- **Agents read only their own rows.** A skill `query_my_installs(name?)` (in-agent) executes `SELECT … WHERE caller = $env.DEMIURGE_CALLER_NAME` directly. The agent literally can't construct a query that returns other callers' rows because its DB role only grants `SELECT … WHERE caller = current_caller`.
- **Sol queries the global view via `demiurge dep list`.** This goes directly to Postgres with the operator's full-read role; no agent mediates.

`environment_packages_global_idx` exists to make the operator's queries fast. `environment_packages_active_idx` exists to make agent-scoped reads fast.

### 5.2 What's recorded vs what's not

Recorded: name, version, mechanism, location, sha256, plan_id, install date, health.

**Not recorded:** the full stdout/stderr (those go to the per-trace log file), the plan body (referenced by id; the plan is in `install_plans` table), or any sensitive parameter values from the plan (those, if any, would be hashed per the audit semantics in `approvals.md`).

---

## 6. Rollback

Every approved plan has a paired rollback plan validated at install-plan time. `demiurge dep remove <name>` triggers:

1. Find the matching active inventory row.
2. Look up its install plan; pull the rollback section.
3. Re-validate the rollback (its own `mechanism + executor`).
4. Submit it as a new `system.execute_privileged` call (which triggers the same approval gate — though typically the standing approval that authorized the install also authorizes the corresponding rollback, so this is silent).
5. Execute, run a "is it really gone?" check, mark inventory row `removed_at = now(), health_status = 'rolled_back'`.

The same protocol applies to **automatic rollbacks** triggered by failed health checks.

---

## 7. Plans table

```sql
-- migration 008_install_plans.sql
CREATE TABLE install_plans (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    proposing_agent TEXT NOT NULL,
    mechanism     TEXT NOT NULL,
    plan_body     JSONB NOT NULL,
    rollback_body JSONB NOT NULL,
    rationale     TEXT,
    proposed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at    TIMESTAMPTZ NOT NULL,           -- 30 minutes from proposed_at
    executed_at   TIMESTAMPTZ,                    -- null until execute_privileged runs
    execution_outcome TEXT,                       -- ok | failed | health_failed | timed_out
    inventory_id  UUID                            -- FK once executed
);
```

Plans expire to bound the staleness window. An agent that wants to retry an expired plan must `plan_install` again (cheap; pure validation).

---

## 8. End-to-end example: tesseract install

Concrete walkthrough showing every actor.

```
1. Operator:     demiurge dep ensure tesseract-ocr
                 → publishes event {topic: system.dep.requested.tesseract-ocr}

2. Installer:    handle(event)
                 → calls system.read_environment(...) [non-privileged]
                 ← {os: ubuntu 22.04, dpkg.tesseract-ocr.installed: false}
                 → builds plan: {mechanism: apt, packages: [tesseract-ocr], source: …, health_check: …, rollback: …}
                 → calls system.plan_install(plan) [non-privileged]
                 ← {plan_id: a1b2..., validated: true}
                 → calls system.execute_privileged(plan_id, rationale=…)

3. Enkidu:       receives execute_privileged
                 → check_approval(call) → matches standing approval
                                            (mechanism=apt, source=^deb\.debian\..*$, expires in 25 days)
                 → loads plan a1b2...
                 → spawns: apt-get install -y --no-install-recommends tesseract-ocr
                 ← exit 0, stdout_hash=<sha>
                 → runs health check: dpkg-query --show ... → "install ok installed" → passed
                 → writes environment_packages row {caller: installer, name: tesseract-ocr, ..., health_status: passed}
                 → audit log line: {outcome: ok, approval_via: standing/<sa_id>, capability: system.execute_privileged, ...}
                 ← {outcome: ok, exit_code: 0, inventory_id: ...}

4. Installer:    publishes event {topic: system.dep.installed.tesseract-ocr}
                 → done

5. Operator:     demiurge dep list
                 → reads environment_packages directly
                 ← shows tesseract-ocr installed by installer at <date>, health passed

6. Operator:     pytest skills/tests/test_read_pdf.py::test_scanned_pdf_uses_ocr
                 ← passes (was previously skipped)
```

---

## 9. What's deliberately not in the protocol

- **No "exec arbitrary command" capability.** Every privileged action goes through a validated mechanism. New mechanisms require code in Enkidu, reviewed and committed.
- **No streaming output.** Output is captured to the per-trace log file; agents get a hash, not the stream. If an agent really needs the output (e.g. for failure-diagnosis prompts), it can request a tail-of-trace via a separate audit-tail capability — not yet built.
- **No partial plans.** A plan is a complete artifact (install + rollback + health-check). No "amend a plan in-flight." If something needs to change, propose a new plan.
- **No "trust this binary because I downloaded it from this URL."** Source allow-lists and sha256 pins, every time. URLs alone are not provenance.

---

## 10. References

- `docs/architecture/agent-isolation.md` — why the agent doesn't have sudo.
- `docs/protocols/approvals.md` — how `system.execute_privileged` is gated.
- `docs/protocols/security-agent.md` — wire protocol for the capabilities above.
- `plans/v0.3-installer-and-approvals.md` — the milestone that builds this.
- DEMIURGE.md §3.13 — charter pointer.
