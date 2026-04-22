/**
 * WhatsApp adapter.
 *
 * Loads all active WhatsApp accounts from channel_accounts and spins up one
 * Baileys socket per account, each with its own auth state directory.
 *
 * Inbound: socket 'messages.upsert' -> publish whatsapp.message.received.<account_id>
 * Outbound: POST /whatsapp/send { account_id, chat_id, text } -> socket.sendMessage
 *
 * Endpoints:
 *   POST /whatsapp/send   — send a message (local only, not exposed publicly)
 *   GET  /health          — liveness
 *
 * NOTE: This is a skeleton. Full wiring — loading accounts on startup, handling
 * disconnects/reconnects, media, etc. — lands day 3.
 */

import { default as makeWASocket, DisconnectReason, useMultiFileAuthState } from '@whiskeysockets/baileys';
import type { WASocket, proto } from '@whiskeysockets/baileys';
import Fastify from 'fastify';
import pg from 'pg';
import pino from 'pino';
import { randomUUID } from 'node:crypto';
import { join } from 'node:path';
import { mkdirSync } from 'node:fs';

const log = pino({ level: 'info' });
const sockets = new Map<string, WASocket>();

const authDir = process.env.WHATSAPP_AUTH_DIR || './whatsapp-auth';
const dbUrl = process.env.DATABASE_URL;
if (!dbUrl) throw new Error('DATABASE_URL not set');

const pool = new pg.Pool({ connectionString: dbUrl });

/** Fetch all active WhatsApp accounts from the DB. */
async function loadActiveAccounts(): Promise<Array<{ account_id: string; display_name: string }>> {
  const { rows } = await pool.query(
    `SELECT account_id, display_name FROM channel_accounts
     WHERE channel_type = 'whatsapp' AND status = 'active'`
  );
  return rows;
}

/** Publish an event to the bus (events table) and NOTIFY. */
async function publishEvent(topic: string, accountId: string, payload: Record<string, unknown>) {
  const client = await pool.connect();
  try {
    await client.query(
      `INSERT INTO events (event_id, topic, account_id, payload)
       VALUES ($1, $2, $3, $4::jsonb)`,
      [payload.event_id, topic, accountId, JSON.stringify(payload)]
    );
    await client.query('NOTIFY events_new');
  } finally {
    client.release();
  }
}

/** Start a Baileys socket for one account. */
async function startSocket(accountId: string): Promise<WASocket> {
  const accountAuthDir = join(authDir, accountId);
  mkdirSync(accountAuthDir, { recursive: true });

  const { state, saveCreds } = await useMultiFileAuthState(accountAuthDir);
  const sock = makeWASocket({
    auth: state,
    printQRInTerminal: false,  // add_account handles QR; running service should have creds
    logger: log.child({ account: accountId }) as any,
  });

  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', (update) => {
    const { connection, lastDisconnect } = update;
    if (connection === 'close') {
      const shouldReconnect =
        (lastDisconnect?.error as any)?.output?.statusCode !== DisconnectReason.loggedOut;
      log.info({ accountId, shouldReconnect }, 'connection closed');
      if (shouldReconnect) {
        setTimeout(() => startSocket(accountId).then((s) => sockets.set(accountId, s)), 2000);
      } else {
        // Mark account as broken in DB
        pool.query(
          `UPDATE channel_accounts SET status = 'broken' WHERE account_id = $1`,
          [accountId]
        ).catch((e) => log.error({ err: e }, 'failed to mark broken'));
      }
    } else if (connection === 'open') {
      log.info({ accountId }, 'connected');
    }
  });

  sock.ev.on('messages.upsert', async ({ messages, type }) => {
    if (type !== 'notify') return;
    for (const msg of messages) {
      if (msg.key.fromMe) continue;
      if (!msg.message) continue;

      const event = formatMessageEvent(accountId, msg);
      try {
        await publishEvent(`whatsapp.message.received.${accountId}`, accountId, event);
      } catch (e) {
        log.error({ err: e, accountId, msgId: msg.key.id }, 'failed to publish event');
      }
    }
  });

  return sock;
}

/** Convert a Baileys message to our WhatsAppMessageEvent shape. */
function formatMessageEvent(accountId: string, msg: proto.IWebMessageInfo): Record<string, unknown> {
  const chatId = msg.key.remoteJid || '';
  const isGroup = chatId.endsWith('@g.us');
  const text =
    msg.message?.conversation ||
    msg.message?.extendedTextMessage?.text ||
    msg.message?.imageMessage?.caption ||
    msg.message?.videoMessage?.caption ||
    '';

  return {
    event_id: randomUUID(),
    ts: new Date().toISOString(),
    source: 'whatsapp',
    account_id: accountId,
    msg_id: msg.key.id || '',
    chat_id: chatId,
    from_jid: msg.key.participant || chatId,
    from_push_name: msg.pushName || null,
    is_group: isGroup,
    group_id: isGroup ? chatId : null,
    text,
    media_ref: null,  // TODO: handle media
    quoted_msg_id: msg.message?.extendedTextMessage?.contextInfo?.stanzaId || null,
    raw_ref: `whatsapp:msg/${msg.key.id}`,
  };
}

// ---------------------------------------------------------------------------
// HTTP API
// ---------------------------------------------------------------------------
const app = Fastify({ logger: log });

app.get('/health', async () => ({ status: 'ok', accounts: Array.from(sockets.keys()) }));

app.post<{
  Body: { account_id: string; chat_id: string; text: string };
}>('/whatsapp/send', async (req, reply) => {
  const { account_id, chat_id, text } = req.body;
  const sock = sockets.get(account_id);
  if (!sock) {
    return reply.status(404).send({ error: `no active socket for ${account_id}` });
  }
  try {
    await sock.sendMessage(chat_id, { text });
    return { ok: true };
  } catch (e) {
    log.error({ err: e }, 'send failed');
    return reply.status(500).send({ error: String(e) });
  }
});

// ---------------------------------------------------------------------------
// Startup
// ---------------------------------------------------------------------------
async function main() {
  const accounts = await loadActiveAccounts();
  log.info({ count: accounts.length }, 'loaded active whatsapp accounts');

  for (const { account_id } of accounts) {
    const sock = await startSocket(account_id);
    sockets.set(account_id, sock);
  }

  await app.listen({ host: '0.0.0.0', port: 8081 });
  log.info('whatsapp adapter listening on :8081');
}

main().catch((e) => {
  log.error({ err: e }, 'fatal startup error');
  process.exit(1);
});
