-- 009_signal_channel_type.sql
--
-- Expand channel_accounts.channel_type to allow 'signal' (v0.5).
-- Following channels (slack, discord, telegram, imessage) get added in
-- their own migrations as they ship.

ALTER TABLE channel_accounts
    DROP CONSTRAINT IF EXISTS channel_accounts_channel_type_check;

ALTER TABLE channel_accounts
    ADD CONSTRAINT channel_accounts_channel_type_check
    CHECK (channel_type IN ('gmail', 'whatsapp', 'whatsapp_cloud', 'calendar', 'signal'));
