"""Shared skills layer.

Two distinct first-class concepts that share a directory:

- **Tools** are Python functions agents call. Code-reviewed, imported.
- **Playbooks** are Markdown procedural knowledge loaded into prompts at
  runtime. Content-reviewed, retrieved per-event.

These are NOT the same thing. Different review workflows, different
storage, different retrieval, different failure modes. See
``CLAUDE_skills_layer.md`` for the canonical spec.
"""

__version__ = "0.1.0"
