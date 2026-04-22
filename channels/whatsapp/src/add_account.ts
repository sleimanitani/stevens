/**
 * WhatsApp add-account CLI.
 *
 * Usage:
 *   npm run add-account -- --id wa.us --name "US number"
 *
 * Creates a Baileys auth state dir for this account, starts a socket that
 * prints a QR code to the terminal, waits for scan, and on successful
 * connection writes a row to channel_accounts.
 *
 * Run once per number. Re-running for an existing account re-pairs it.
 */

import { default as makeWASocket, useMultiFileAuthState } from '@whiskeysockets/baileys';
import pg from 'pg';
import qrcode from 'qrcode-terminal';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { parseArgs } from 'node:util';

const { values } = parseArgs({
  options: {
    id: { type: 'string' },
    name: { type: 'string' },
  },
});

if (!values.id || !values.name) {
  console.error('Usage: npm run add-account -- --id wa.us --name "US number"');
  process.exit(1);
}

const accountId = values.id;
const displayName = values.name;

if (!accountId.startsWith('wa.')) {
  console.error('account_id must start with "wa."');
  process.exit(1);
}

const authDir = process.env.WHATSAPP_AUTH_DIR || './whatsapp-auth';
const accountAuthDir = join(authDir, accountId);
mkdirSync(accountAuthDir, { recursive: true });

const dbUrl = process.env.DATABASE_URL;
if (!dbUrl) {
  console.error('DATABASE_URL not set');
  process.exit(1);
}

const pool = new pg.Pool({ connectionString: dbUrl });

async function main() {
  const { state, saveCreds } = await useMultiFileAuthState(accountAuthDir);
  const sock = makeWASocket({ auth: state, printQRInTerminal: false });

  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', async (update) => {
    const { connection, qr } = update;

    if (qr) {
      console.log('\n--- Scan this QR in WhatsApp > Linked Devices > Link a Device ---\n');
      qrcode.generate(qr, { small: true });
    }

    if (connection === 'open') {
      console.log(`\nConnected as ${accountId}`);
      // Baileys stores creds on disk; DB row just records existence + metadata.
      await pool.query(
        `INSERT INTO channel_accounts (account_id, channel_type, display_name, credentials, status, metadata)
         VALUES ($1, 'whatsapp', $2, $3::jsonb, 'active', $4::jsonb)
         ON CONFLICT (account_id) DO UPDATE
         SET display_name = EXCLUDED.display_name, status = 'active'`,
        [
          accountId,
          displayName,
          JSON.stringify({ auth_dir: accountAuthDir }),  // creds are on disk; pointer here
          JSON.stringify({ jid: sock.user?.id || null }),
        ]
      );
      console.log(`Stored account ${accountId} in database.`);
      await pool.end();
      process.exit(0);
    }
  });
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
