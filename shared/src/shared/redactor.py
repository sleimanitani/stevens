"""Trace / log redactor — strips obvious secrets before payloads go to
Langfuse, stdout, or anywhere else outside the Security Agent.

Defense in depth: the primary guarantee is that capabilities don't
surface raw secrets in their results (the Security Agent scrubs them
at the boundary). This redactor catches accidents — a prompt that
happens to echo a user's API key, an error message that includes a
token, a tool argument a new capability forgot to mark sensitive.

It is **not** comprehensive. Rely on capability design first.

Rules:

1. Dict keys whose name suggests a secret → value replaced by
   ``"<REDACTED:key-name>"``.
2. String values matching well-known secret patterns (``Bearer <token>``,
   JWT, OAuth access/refresh tokens) → value replaced by ``"<REDACTED>"``.
3. Everything else passes through unchanged. Structure is preserved.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

SENSITIVE_KEY_NAMES = frozenset(
    {
        # Generic
        "password",
        "pass",
        "pw",
        "secret",
        "passphrase",
        "token",
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "id_token",
        "bearer",
        "authorization",
        "auth",
        "cookie",
        "session",
        "session_id",
        "sessionid",
        "csrf",
        "csrf_token",
        "private_key",
        "privatekey",
        "signing_key",
        "x-api-key",
        # Payment / PII
        "card",
        "card_number",
        "cardnumber",
        "pan",
        "cvv",
        "cvc",
        "card_cvc",
        "ssn",
        "iban",
        # OAuth / Google
        "client_secret",
        "client_id",
        "sig",
        "signature",
    }
)

_BEARER_RE = re.compile(r"(?i)bearer\s+[a-z0-9\-_\.]{10,}")
# Looks like a JWT: three base64url segments separated by dots.
_JWT_RE = re.compile(r"\beyJ[a-zA-Z0-9_\-=]+?\.[a-zA-Z0-9_\-=]+?\.[a-zA-Z0-9_\-=]+?\b")
# Long-ish OAuth-style tokens (e.g. Google access tokens start with "ya29.").
_GOOGLE_ACCESS_TOKEN_RE = re.compile(r"\bya29\.[A-Za-z0-9_\-]{20,}\b")
# Generic "looks like a secret": long base64url run with no whitespace.
_HIGH_ENTROPY_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9_\-]{40,}(?![A-Za-z0-9])")

REDACTED = "<REDACTED>"


def _is_sensitive_key(key: str) -> bool:
    if not isinstance(key, str):
        return False
    lowered = key.lower().replace("-", "_")
    if lowered in SENSITIVE_KEY_NAMES:
        return True
    for needle in ("secret", "password", "token", "api_key", "private_key"):
        if needle in lowered:
            return True
    return False


def _redact_string(value: str) -> str:
    redacted = _BEARER_RE.sub(REDACTED, value)
    redacted = _JWT_RE.sub(REDACTED, redacted)
    redacted = _GOOGLE_ACCESS_TOKEN_RE.sub(REDACTED, redacted)
    # High-entropy substitution applied last and only if nothing above matched
    # for this span — avoids double-replacement cosmetics.
    redacted = _HIGH_ENTROPY_RE.sub(REDACTED, redacted)
    return redacted


def redact(value: Any) -> Any:
    """Return a redacted copy of ``value``. Structure preserved."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if _is_sensitive_key(k):
                out[k] = f"<REDACTED:{k}>"
            else:
                out[k] = redact(v)
        return out
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    if isinstance(value, str):
        return _redact_string(value)
    return value


def redact_headers(headers: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    """Redact HTTP header pairs, preserving order + case."""
    out: list[tuple[str, str]] = []
    for name, value in headers:
        if _is_sensitive_key(name):
            out.append((name, f"<REDACTED:{name}>"))
        else:
            out.append((name, _redact_string(value)))
    return out
