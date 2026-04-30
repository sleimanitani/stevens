"""Code-resident tools agents can call.

Each tool lives at ``skills/src/skills/tools/<category>/<name>.py`` with a
standard shape (TOOL_METADATA dict + pydantic input schema + pure-function
implementation + ``build_tool() -> StructuredTool``). See
``CLAUDE_skills_layer.md`` for the full schema.
"""
