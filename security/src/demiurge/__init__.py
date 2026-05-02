"""Demiurge Security Agent (Enkidu) — sole broker for all secrets and sensitive operations.

See STEVENS.md §3 and plans/v0.1-sec.md for the charter and current milestone.
This package is the only component that reads the sealed secret store at rest
or holds decrypted secret material in memory. All other components interact
through the RPC surface exposed by ``demiurge.server``.
"""

__version__ = "0.1.0-sec.step1"
