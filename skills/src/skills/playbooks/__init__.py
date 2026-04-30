"""Markdown playbooks — situation-specific procedural knowledge.

YAML frontmatter follows the agentskills.io standard
(``name/description/version/author/license`` at top level), with our
extensions (``applies_to_topics``, ``applies_to_agents``, ``triggers``,
``status``, ``supersedes``) under the ``metadata`` key.

Loader lives in ``loader.py``; retrieval (trigger-match in v1) lives in
``../retrieval.py``.
"""
