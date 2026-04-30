"""Shared outbound HTTP / search clients.

These live in ``shared`` (not ``security``) because both Enkidu and
Arachne use them — the URL validator and the search-backend Protocol are
not security primitives, just plumbing. Enkidu is the only process that
actually makes outbound calls; Arachne calls Enkidu's network capabilities
which use these clients.
"""
