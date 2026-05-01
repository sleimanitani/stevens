# Runbook — WhatsApp Cloud API (business numbers)

End state: each WhatsApp Business phone number you onboard publishes inbound message events to the bus, and agents can send replies via the broker.

This is for **business numbers** registered through Meta's WhatsApp Cloud API. For personal numbers, see the (not-yet-built) Baileys adapter.

## Goal

- One or more WhatsApp Cloud accounts onboarded.
- Permanent System User Access Token stored as `wac.<account_id>.access_token` in the sealed store.
- App secret stored as `whatsapp_cloud.app_secret` (shared across accounts under one Meta app).
- Webhook receiver running and registered with Meta.

## Prerequisites

- A Meta Business Manager account.
- A WhatsApp Business Account (WABA) with at least one phone number registered.
- A Meta app linked to the WABA.
- A **System User** with permanent access token (NOT the temporary 24-hour token from the dashboard — that one expires daily).
- A public webhook URL reachable from Meta (Tailscale Funnel / Cloudflare Tunnel — must be HTTPS).
- The `whatsapp-cloud-adapter` service brought up via compose (it serves the webhook on port 8082 by default).

## Manual prep in Meta Business Manager (one-time)

1. **Business Manager** → Business Settings → Users → System Users → Create.
2. Assign that user to your WhatsApp Business Account with full control.
3. Click **Generate New Token** → select the app → check `whatsapp_business_messaging` and `whatsapp_business_management` scopes → generate. Copy the token. **It does not expire.**
4. Get the **App Secret** from your app's basic settings page. This is used to verify webhook payload signatures.
5. Get the **phone-number ID** for each phone (numeric, not the human phone number). It's listed under the WABA's phone numbers page.
6. Pick a **verify token** — any random nonce. You'll use it both in Meta's webhook config and in the onboard step.

## Steps

```bash
# 1. store the shared app secret (one time per Meta app)
echo -n "$YOUR_APP_SECRET" | uv run stevens secrets add whatsapp_cloud.app_secret --from-stdin

# 2. onboard each phone (the access token comes via stdin so it doesn't
#    appear in shell history or `ps`)
echo -n "$YOUR_PERMANENT_ACCESS_TOKEN" | \
  uv run stevens onboard whatsapp_cloud --app-secret-stdin -- \
    --id wac.business1 --name "Work WhatsApp" \
    --phone-number-id 999888777 \
    --display-phone-number "+1-555-1234" \
    --access-token-stdin \
    --verify-token "your-random-nonce-string"

# 3. configure the Meta webhook to point at your public URL
#    Meta dashboard → app → WhatsApp → Configuration → Webhook
#    Callback URL: https://stevens.example.ts.net/whatsapp/webhook
#    Verify token: <the same nonce you used above>
#    Subscribe to: messages, message_status (others optional)
```

## Verify

```bash
uv run stevens secrets list
# expected: whatsapp_cloud.app_secret + wac.business1.access_token + wac.business1.phone_number_id

# Send a test WhatsApp message TO your business number from another
# phone. Within seconds the webhook receives it, the adapter publishes
# whatsapp.message.received.wac.business1 to the bus.
uv run stevens audit tail -f
```

## Multi-account

One container per phone is the simplest — set the right env vars when starting each `whatsapp-cloud-adapter` instance. Multi-phone in a single adapter is possible (account_id propagates) but you have one inbound webhook URL per Meta app, so the routing happens at the adapter level based on the message payload's `phone_number_id`.

## Common issues

- **"24-hour conversation window."** WhatsApp Cloud restricts unsolicited outbound: you can only send freeform text to a number that has messaged you within the last 24 hours. For older outbound, you must use a pre-approved **template** message. The adapter's `whatsapp.send_template` capability handles this.
- **"Webhook signature mismatch."** Your `whatsapp_cloud.app_secret` doesn't match the app you actually got the access token from. They must come from the same Meta app.
- **"Verify token mismatch on webhook setup."** The string you typed in Meta's dashboard doesn't match `--verify-token`. Re-check both.
- **"Token revoked unexpectedly."** Your System User got disabled, or the token was a 24-hour temporary one (NOT a System User token). Generate a new System User token with `whatsapp_business_messaging` scope and rotate via `stevens secrets rotate`.
