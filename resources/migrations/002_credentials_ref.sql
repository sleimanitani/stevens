-- 002_credentials_ref.sql
-- Add the credentials_ref column to channel_accounts so adapters can hold
-- an opaque reference to a sealed-store secret instead of the raw creds.
--
-- The existing `credentials` JSONB column is kept for transition. Accounts
-- migrated to the sealed store set credentials_ref = '<sealed-store name>'
-- and leave credentials either empty or holding a tombstone marker.
--
-- Run once per deployment:
--   docker compose exec -T postgres psql -U assistant -d assistant \
--     < resources/migrations/002_credentials_ref.sql
--
-- Safe to re-run: every operation uses IF NOT EXISTS / IF EXISTS.

ALTER TABLE channel_accounts
    ADD COLUMN IF NOT EXISTS credentials_ref TEXT;

-- Cheap sanity index — lookups by ref are rare but we sometimes want to
-- confirm a given sealed-store name is used by at most one account.
CREATE UNIQUE INDEX IF NOT EXISTS idx_channel_accounts_credentials_ref
    ON channel_accounts(credentials_ref)
    WHERE credentials_ref IS NOT NULL;

COMMENT ON COLUMN channel_accounts.credentials_ref IS
    'Opaque name of the sealed-store secret holding the real credentials. '
    'When present, credentials JSONB must not be used.';
