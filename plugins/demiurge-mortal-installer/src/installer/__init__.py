"""Installer agent — packaged as ``demiurge-mortal-installer`` (v0.11).

Narrow, deterministic Mortal. Subscribes to ``system.dep.requested.*``
events. Per request:

  1. Reads narrow host facts via ``system.read_environment``.
  2. Builds a structured install plan (pure logic).
  3. Submits the plan via ``system.plan_install`` → plan_id.
  4. Calls ``system.execute_privileged(plan_id)`` (approval-gated).
  5. Publishes the outcome event.

No LLM. No broad tool list. No imports of other agents. See
``docs/architecture/agent-isolation.md`` for the rules this enshrines.
"""

from __future__ import annotations


def manifest():
    """Entry-point target for ``demiurge.mortals``."""
    from shared.plugins.discovery import load_manifest_for_package

    return load_manifest_for_package("installer")
