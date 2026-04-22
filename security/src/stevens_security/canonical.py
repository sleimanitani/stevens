"""Deterministic (canonical) msgpack encoding for signing.

The Security Agent RPC signs requests over a *canonical* encoding of the
request envelope — not the over-the-wire frame. This lets callers in
different languages produce byte-identical input to the signer as long as
they follow the same rules. Rules:

1. The top-level object must be a ``dict``. Dicts at any depth have their
   keys sorted lexicographically (bytes-wise on the UTF-8 encoding of the
   string key). Non-string dict keys are rejected.
2. The allowed value types are ``dict``, ``list``, ``str``, ``bytes``,
   ``int``, ``bool``, and ``None``. **Floats are rejected** — their cross-
   language serialization is too footgun-prone for something load-bearing.
   Msgpack ext types are rejected for the same reason.
3. Strings are UTF-8 bytes; raw bytes are the msgpack ``bin`` type (never
   ``str``).
4. Integers use msgpack's compact form (positive fixint, int 8/16/32/64 as
   needed) — this is what ``msgpack-python`` does by default; other
   languages' libraries must match.
5. Lists preserve input order.

This module does not enforce the presence of specific envelope fields —
that's the identity layer's job. It only guarantees byte-identical output
for equivalent inputs.
"""

from __future__ import annotations

from typing import Any

import msgpack


class CanonicalEncodingError(Exception):
    """Raised when an input contains a value that is not allowed in the canonical form."""


_ALLOWED_SCALARS = (str, bytes, int, bool, type(None))


def _normalize(obj: Any) -> Any:
    if isinstance(obj, bool):
        # bool is a subclass of int — check first.
        return obj
    if isinstance(obj, dict):
        result = {}
        for k in sorted(obj.keys(), key=_key_sort_key):
            if not isinstance(k, str):
                raise CanonicalEncodingError(
                    f"dict key must be str, got {type(k).__name__}"
                )
            result[k] = _normalize(obj[k])
        return result
    if isinstance(obj, list):
        return [_normalize(item) for item in obj]
    if isinstance(obj, tuple):
        # Accept tuples as lists for ergonomics, but normalize to list so wire
        # output matches either way.
        return [_normalize(item) for item in obj]
    if isinstance(obj, float):
        raise CanonicalEncodingError("float is not allowed in canonical encoding")
    if isinstance(obj, _ALLOWED_SCALARS):
        return obj
    raise CanonicalEncodingError(
        f"value of type {type(obj).__name__} is not allowed in canonical encoding"
    )


def _key_sort_key(k: Any) -> bytes:
    if not isinstance(k, str):
        raise CanonicalEncodingError(
            f"dict key must be str, got {type(k).__name__}"
        )
    return k.encode("utf-8")


def canonical_encode(obj: Any) -> bytes:
    """Return the deterministic msgpack encoding of ``obj`` suitable for signing.

    Raises :class:`CanonicalEncodingError` if ``obj`` contains a float, a
    non-string dict key, or any other disallowed type.
    """
    normalized = _normalize(obj)
    return msgpack.packb(normalized, use_bin_type=True, strict_types=True)
