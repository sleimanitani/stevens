"""Shared tools — modules that any agent can import.

These are utilities that don't belong to a single agent and don't fit
under ``skills/`` (because they're system plumbing, not agent-callable
LangChain tools). The first one is ``propose_skill``, which agents use
to nominate new tools/playbooks for Sol's review.
"""
