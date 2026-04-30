-- 006_approval_requests.sql
--
-- Per-call approval queue. When a `requires_approval: true` capability call
-- has no covering standing approval, Enkidu writes a row here, returns
-- BLOCKED to the caller, and waits for Sol's decision via
-- `stevens approval approve <id>` / `reject <id>`.
--
-- See docs/protocols/approvals.md.

CREATE TABLE IF NOT EXISTS approval_requests (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    capability      TEXT NOT NULL,
    caller          TEXT NOT NULL,
    -- Human-readable summary, displayed by `stevens approval list`.
    params_summary  TEXT NOT NULL,
    -- The original signed envelope, replayable verbatim on approve.
    -- Stored as JSONB so we can index and inspect; replay reconstructs the
    -- bytes exactly (canonical encoding is deterministic).
    full_envelope   JSONB NOT NULL,
    -- Rationale supplied by the calling agent at request time.
    rationale       TEXT,

    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'approved', 'rejected', 'expired', 'failed')),
    decided_at      TIMESTAMPTZ,
    decided_by      TEXT,
    decision_notes  TEXT,
    -- If Sol promoted this per-call to a standing approval, link.
    promoted_standing_id UUID,

    -- For traceability, record the trace_id of the original BLOCKED audit line
    -- so we can reconstruct the per-call's full life from the audit log.
    blocked_trace_id UUID,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS approval_requests_pending_idx
    ON approval_requests (status, created_at)
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS approval_requests_caller_idx
    ON approval_requests (caller, status, created_at);
