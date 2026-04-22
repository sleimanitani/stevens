# Protocol — Security Agent RPC v1

Stable contract between Stevens components (agents, adapters) and the
Security Agent broker.

This document is the **source of truth** for cross-language interop. If the
code and this document disagree, the document wins until we explicitly
version the protocol.

- **Version:** v1
- **Status:** in development alongside `plans/v0.1-sec.md`. Stabilizes when
  step 8 ships (`shared.security_client`).
- **Authoritative parser reference:** `security/src/stevens_security/` —
  `framing.py`, `canonical.py`, `identity.py`.

---

## Transport

- Unix domain socket.
- Default server path: `/run/stevens/security.sock`.
- Server socket mode: `0o660`. Group assigned per-caller at deploy time
  (step 7 of the Build Plan).
- No TCP. No network listener of any kind.

## Framing

Every RPC message — request or response — is a single framed msgpack
document:

```
[uint32 big-endian payload length][msgpack payload]
```

- Length prefix: 4 bytes, big-endian, unsigned.
- Maximum payload: **1 MiB** (`1 << 20` bytes). Both sides MUST reject
  oversized frames, both at encode time and at decode time based on the
  declared length (before reading the payload).
- One request per connection in v1. The server closes the connection after
  writing the response. Callers MUST NOT reuse connections.

## Request envelope

```msgpack
{
  "v":          1,                          # int, protocol version
  "caller":     "<agent_name>",             # str, matches an entry in agents.yaml
  "nonce":      "<base64(32 random bytes)>",# str, unique per request within TTL
  "ts":         <unix_seconds>,             # int, sender's current wall clock
  "capability": "<namespace.name>",         # str, e.g. "gmail.send_draft"
  "params":     { ... },                    # map, capability-specific
  "sig":        "<base64(ed25519 sig)>"     # str, 64 bytes signed, base64 encoded
}
```

All fields are required. Extra fields are currently ignored but MAY be
rejected by future server versions — don't add unknown fields.

## Response envelope

On success:

```msgpack
{ "ok": true, "result": { ... }, "trace_id": "<uuid>" }
```

On error:

```msgpack
{ "ok": false, "error_code": "<AUTH|DENY|NOTFOUND|RATE|INTERNAL>",
  "message": "<human>", "trace_id": "<uuid>" }
```

`trace_id` is the Security Agent's correlation id for the audit log. Every
request — successful or not — produces exactly one audit line keyed by this
id.

### Error codes

| Code       | Meaning                                                              |
|------------|----------------------------------------------------------------------|
| `AUTH`     | Envelope malformed, signature bad, timestamp stale, nonce replayed, caller unknown. |
| `DENY`     | Policy refused the request.                                          |
| `NOTFOUND` | No capability registered under that name.                            |
| `RATE`     | Caller exceeded a rate/budget limit for this capability.             |
| `INTERNAL` | Server-side bug or transport-level malformation. Retry MAY succeed.  |

## Canonical encoding (for signature scope)

The signature in `sig` is an Ed25519 signature over the **canonical msgpack
encoding** of the following scope map:

```python
{"v": req["v"], "caller": req["caller"], "nonce": req["nonce"],
 "ts": req["ts"], "capability": req["capability"], "params": req["params"]}
```

Canonical encoding rules:

1. Top-level value is a map.
2. Every map at every depth has its keys sorted by the **byte-wise
   lexicographic order of their UTF-8 encoding**, before packing. Dict keys
   MUST be strings. Non-string dict keys are a canonical encoding error.
3. Allowed value types: map, list (or any sequence that serializes as
   msgpack array), string, bytes, int, bool, null. **Floats are forbidden**
   — their cross-language representations are too footgun-prone for a
   load-bearing signature. Msgpack ext types are forbidden.
4. Strings are encoded as msgpack `str` (UTF-8); raw bytes are encoded as
   msgpack `bin`. `use_bin_type=True` in msgpack-python terms.
5. Integers use the compact form msgpack libraries produce by default
   (positive fixint, int 8 / int 16 / int 32 / int 64 as minimally fits).
   All mainstream msgpack libraries do this; if yours has a "compact" or
   "smallest" mode, it's probably the default.
6. Lists preserve input order.

The canonical encoding is used **only** for signing. The over-the-wire
frame is a normal (non-canonical) msgpack encoding — order of keys does
not matter on the wire.

## Replay and freshness

- **Timestamp:** `ts` MUST be within ±60 seconds of server wall clock.
  Outside that window = `AUTH` reject.
- **Nonce:** 32 random bytes, base64-encoded. The server keeps a bounded
  recent-nonce cache (100k entries, 5 min TTL). A nonce that appears
  twice within TTL = `AUTH` reject.

## Authentication flow (server-side)

1. Frame decode. Malformed frame → close connection.
2. Envelope shape check. Missing/wrong-type field → `AUTH`.
3. Protocol version check. Unknown `v` → `AUTH`.
4. Timestamp window check → `AUTH` on fail.
5. Lookup `caller` in registry. Unknown → `AUTH`.
6. Decode `sig` (base64). Bad encoding → `AUTH`.
7. Canonicalize the signature scope. Any non-encodable value → `AUTH`.
8. Ed25519 verify. Bad signature → `AUTH`.
9. Check + record nonce. Replay → `AUTH`.
10. Dispatch to policy + capability.

The nonce is recorded **only after** signature verification succeeds, so
unauthenticated clients can't poison the cache.

## Generating + registering keys

Each agent generates its own Ed25519 keypair on first boot and persists
the private key to its local state volume. The public key is registered
with the Security Agent via:

```
stevens agent register <name>
```

Sol confirms the registration once per agent. Private keys never leave
the host and are never embedded in a container image. See STEVENS.md §3.5.

## Stability and versioning

- The v1 wire shape is stable within v0.x of Stevens.
- A v2 will bump the `v` field and MAY change any rule above. v1 and v2
  will coexist by having the server accept both `v` values during a
  transition.
- Changes that are NOT breaking (new capability names, new `params`
  fields within a capability) don't require a version bump but SHOULD be
  announced in the build plan for the milestone introducing them.
