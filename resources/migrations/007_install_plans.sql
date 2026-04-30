-- 007_install_plans.sql
--
-- Structured install plans submitted by agents via `system.plan_install`.
-- Validated at submit; executed by `system.execute_privileged`. Every plan
-- carries its inverse rollback; rollback is also a plan.
--
-- See docs/protocols/privileged-execution.md.

CREATE TABLE IF NOT EXISTS install_plans (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    proposing_agent TEXT NOT NULL,
    mechanism       TEXT NOT NULL,
    plan_body       JSONB NOT NULL,
    rollback_body   JSONB NOT NULL,
    rationale       TEXT,
    proposed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL,
    -- Set when execute_privileged actually runs the plan.
    executed_at     TIMESTAMPTZ,
    execution_outcome TEXT  CHECK (execution_outcome IN
                       (NULL, 'ok', 'failed', 'health_failed', 'timed_out', 'rejected')),
    inventory_id    UUID  -- FK to environment_packages once executed
);

CREATE INDEX IF NOT EXISTS install_plans_active_idx
    ON install_plans (proposing_agent, mechanism, proposed_at)
    WHERE executed_at IS NULL;
