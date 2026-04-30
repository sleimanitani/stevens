-- 008_environment_packages.sql
--
-- Per-agent install inventory. Each row records a successful install: who
-- did it, with what mechanism, where it landed, and whether the structural
-- health check passed.
--
-- See docs/protocols/privileged-execution.md §5.

CREATE TABLE IF NOT EXISTS environment_packages (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- The agent that installed it. Agents read-scope-by-caller.
    caller        TEXT NOT NULL,
    name          TEXT NOT NULL,
    version       TEXT,
    mechanism     TEXT NOT NULL,
    -- For mechanisms that install to a specific path (opt_dir, container);
    -- null for apt (apt's package files are tracked by dpkg, not us).
    location      TEXT,
    sha256        TEXT,
    plan_id       UUID NOT NULL,
    installed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    removed_at    TIMESTAMPTZ,  -- soft delete; preserves audit
    health_status TEXT NOT NULL DEFAULT 'unknown'
                  CHECK (health_status IN ('unknown', 'passed', 'failed', 'rolled_back'))
);

-- Agent-scoped reads (the most common query: "have I installed X?").
CREATE INDEX IF NOT EXISTS environment_packages_active_idx
    ON environment_packages (caller, name)
    WHERE removed_at IS NULL;

-- Operator's global view (`stevens dep list`).
CREATE INDEX IF NOT EXISTS environment_packages_global_idx
    ON environment_packages (name, mechanism)
    WHERE removed_at IS NULL;
