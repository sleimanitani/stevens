"""Operator-facing wizards.

Each wizard drives a multi-step provider-side setup (e.g. Google Cloud
Console for Gmail / Calendar / Pub/Sub) — automating the API-able steps
and walking the operator through the irreducible manual ones with
exact URLs and exact field values.

The general operator-assisted browser-driven equivalent (Charon, v0.7)
will eventually subsume the manual-step parts; until then, wizards live
here as focused per-provider helpers.
"""
