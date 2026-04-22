-- 001_init.sql
-- Initial schema for the personal assistant system (v0.1)
--
-- Run once, from the repo root:
--   docker compose exec -T postgres psql -U assistant -d assistant < resources/migrations/001_init.sql
--
-- Safe to re-run: every CREATE uses IF NOT EXISTS.

CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- for gen_random_uuid()

-- ---------------------------------------------------------------------------
-- channel_accounts: one row per real-world account
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS channel_accounts (
    account_id    TEXT PRIMARY KEY,
    channel_type  TEXT NOT NULL CHECK (channel_type IN ('gmail', 'whatsapp', 'calendar')),
    display_name  TEXT NOT NULL,
    credentials   JSONB NOT NULL,
    status        TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused', 'broken')),
    metadata      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_channel_accounts_type_status
    ON channel_accounts(channel_type, status);

-- ---------------------------------------------------------------------------
-- events: append-only bus log
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS events (
    event_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    topic         TEXT NOT NULL,
    account_id    TEXT REFERENCES channel_accounts(account_id),
    payload       JSONB NOT NULL,
    published_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Primary query pattern: "give me events matching this topic pattern since my cursor"
CREATE INDEX IF NOT EXISTS idx_events_published_at ON events(published_at);
CREATE INDEX IF NOT EXISTS idx_events_topic_published ON events(topic, published_at);

-- ---------------------------------------------------------------------------
-- subscription_cursors: each subscriber's position in the event log
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS subscription_cursors (
    subscriber_id       TEXT NOT NULL,
    topic_pattern       TEXT NOT NULL,
    last_event_id       UUID,
    last_published_at   TIMESTAMPTZ,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (subscriber_id, topic_pattern)
);

-- ---------------------------------------------------------------------------
-- followups: what's waiting on whom, when
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS followups (
    followup_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id    TEXT NOT NULL REFERENCES channel_accounts(account_id),
    thread_id     TEXT NOT NULL,
    direction     TEXT NOT NULL CHECK (direction IN ('waiting_on_them', 'waiting_on_me')),
    deadline      TIMESTAMPTZ NOT NULL,
    note          TEXT,
    status        TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved', 'cancelled')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at   TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_followups_status_deadline
    ON followups(status, deadline)
    WHERE status = 'open';

CREATE INDEX IF NOT EXISTS idx_followups_thread
    ON followups(account_id, thread_id);

-- ---------------------------------------------------------------------------
-- pending_approvals: reserved for v0.2
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pending_approvals (
    approval_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_name    TEXT NOT NULL,
    action_type   TEXT NOT NULL,
    context       JSONB NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'expired')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at   TIMESTAMPTZ,
    resolution    JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_pending_approvals_status
    ON pending_approvals(status, created_at)
    WHERE status = 'pending';

-- ---------------------------------------------------------------------------
-- updated_at triggers
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_channel_accounts_updated_at ON channel_accounts;
CREATE TRIGGER trg_channel_accounts_updated_at
    BEFORE UPDATE ON channel_accounts
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
