"""Re-export of the canonical encoder from ``shared.canonical``.

Historical callers inside the Security Agent import from
``stevens_security.canonical``; the implementation moved to
``shared.canonical`` in step 8 so the agent-side client
(``shared.security_client``) can share the exact same bytes without the
risk of two implementations drifting.
"""

from shared.canonical import CanonicalEncodingError, canonical_encode  # noqa: F401
