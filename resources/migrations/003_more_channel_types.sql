-- 003_more_channel_types.sql
-- Loosen channel_accounts.channel_type to allow the new Python channels
-- we're shipping alongside the Gmail + Baileys-WhatsApp originals:
--
--   whatsapp_cloud — Meta/Facebook Cloud API (for business numbers; see
--                    channels/whatsapp-cloud/)
--
-- Keeps existing rows and the original CHECK intact; drops and re-adds
-- with the expanded whitelist.

ALTER TABLE channel_accounts
    DROP CONSTRAINT IF EXISTS channel_accounts_channel_type_check;

ALTER TABLE channel_accounts
    ADD CONSTRAINT channel_accounts_channel_type_check
    CHECK (channel_type IN ('gmail', 'whatsapp', 'whatsapp_cloud', 'calendar'));
