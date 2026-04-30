-- 005_standing_approvals.sql
--
-- Standing approvals — Sol pre-authorizes a CLASS of capability calls
-- matching orthogonal predicates (mechanism, source, packages, custom
-- params), bounded by lifetime and revocable on demand.
--
-- See docs/protocols/approvals.md for the full design.

CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- gen_random_uuid()

CREATE TABLE IF NOT EXISTS standing_approvals (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    capability      TEXT NOT NULL,
    caller          TEXT NOT NULL,
    -- Predicates: orthogonal, optional. Missing key => "any" for that field.
    -- Shape: {"mechanism": "apt", "source": {"regex": "^deb\\.debian\\..*"},
    --         "packages": {"in": ["a","b"]}, "param_matchers": {...}}
    predicates      JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- Lifetime: at most one of expires_at / expires_session non-null.
    -- Both null => never expires (still revocable).
    expires_at      TIMESTAMPTZ,
    expires_session TEXT,

    granted_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    granted_by      TEXT NOT NULL,
    rationale       TEXT,

    revoked_at      TIMESTAMPTZ,
    revoked_by      TEXT,

    -- If this standing approval was created by promoting a per-call request,
    -- back-link to that approval_requests row for audit reconstruction.
    promoted_from_request UUID
);

-- Hot path: matcher loads (capability, caller) candidates.
CREATE INDEX IF NOT EXISTS standing_approvals_active_idx
    ON standing_approvals (capability, caller)
    WHERE revoked_at IS NULL;

CREATE INDEX IF NOT EXISTS standing_approvals_expiry_idx
    ON standing_approvals (expires_at)
    WHERE revoked_at IS NULL AND expires_at IS NOT NULL;
