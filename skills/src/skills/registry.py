"""Skills registry — single index of approved tools and playbooks.

Loaded once at process start (or on demand) from
``skills/registry.yaml``. Agents call ``get_tools_for_agent(name)`` and
``get_playbooks_for(agent_name, event)``; they never construct tool lists
or scan playbook directories on their own.

Schema (see also ``skills/registry.yaml``)::

    tools:
      - id: pdf.read_pdf
        path: skills/src/skills/tools/pdf/read_pdf.py
        scope: shared              # shared | restricted
        allowed_agents: []         # required iff scope=restricted
        safety_class: read-only    # read-only | read-write | destructive
        version: 1.0.0

    playbooks:
      - id: email/appointment_request
        path: skills/src/skills/playbooks/email/appointment_request.md
        applies_to_topics: ["email.received.*"]
        applies_to_agents: ["email_pm"]
"""

from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import yaml

from .playbooks.loader import Playbook, load_playbook


class RegistryError(Exception):
    """Raised on malformed registry.yaml."""


_VALID_SCOPES = {"shared", "restricted"}
_VALID_SAFETY = {"read-only", "read-write", "destructive"}
_SAFETY_RANK = {"read-only": 0, "read-write": 1, "destructive": 2}


@dataclass(frozen=True)
class ToolEntry:
    id: str
    path: Path
    scope: str
    allowed_agents: List[str]
    safety_class: str
    version: str


@dataclass(frozen=True)
class PlaybookEntry:
    id: str
    path: Path
    applies_to_topics: List[str]
    applies_to_agents: List[str]


@dataclass(frozen=True)
class Registry:
    tools: List[ToolEntry] = field(default_factory=list)
    playbooks: List[PlaybookEntry] = field(default_factory=list)
    root: Optional[Path] = None  # repo root, used to resolve relative paths


# --- loading ---


def _resolve(path_str: str, root: Path) -> Path:
    p = Path(path_str)
    if not p.is_absolute():
        p = root / p
    return p


def _parse_tool(entry: Dict[str, Any], root: Path, ctx: str) -> ToolEntry:
    for k in ("id", "path", "scope", "safety_class"):
        if k not in entry:
            raise RegistryError(f"{ctx}: tool entry missing {k!r}: {entry!r}")
    scope = entry["scope"]
    if scope not in _VALID_SCOPES:
        raise RegistryError(f"{ctx}: tool scope must be one of {sorted(_VALID_SCOPES)}, got {scope!r}")
    safety = entry["safety_class"]
    if safety not in _VALID_SAFETY:
        raise RegistryError(f"{ctx}: tool safety_class must be one of {sorted(_VALID_SAFETY)}, got {safety!r}")
    allowed = entry.get("allowed_agents") or []
    if not isinstance(allowed, list) or not all(isinstance(x, str) for x in allowed):
        raise RegistryError(f"{ctx}: allowed_agents must be a list of strings")
    if scope == "restricted" and not allowed:
        raise RegistryError(f"{ctx}: restricted tool {entry['id']!r} needs allowed_agents")
    return ToolEntry(
        id=str(entry["id"]),
        path=_resolve(entry["path"], root),
        scope=scope,
        allowed_agents=list(allowed),
        safety_class=safety,
        version=str(entry.get("version", "0.0.0")),
    )


def _parse_playbook(entry: Dict[str, Any], root: Path, ctx: str) -> PlaybookEntry:
    for k in ("id", "path"):
        if k not in entry:
            raise RegistryError(f"{ctx}: playbook entry missing {k!r}: {entry!r}")
    topics = entry.get("applies_to_topics") or []
    agents = entry.get("applies_to_agents") or []
    if not isinstance(topics, list) or not all(isinstance(x, str) for x in topics):
        raise RegistryError(f"{ctx}: applies_to_topics must be a list of strings")
    if not isinstance(agents, list) or not all(isinstance(x, str) for x in agents):
        raise RegistryError(f"{ctx}: applies_to_agents must be a list of strings")
    return PlaybookEntry(
        id=str(entry["id"]),
        path=_resolve(entry["path"], root),
        applies_to_topics=list(topics),
        applies_to_agents=list(agents),
    )


def load_registry(
    yaml_path: Optional[Path] = None, *, root: Optional[Path] = None
) -> Registry:
    """Load the skills registry. Missing file → empty (fail-safe)."""
    yaml_path = yaml_path or _default_registry_path()
    root = root or yaml_path.parent.parent  # skills/registry.yaml → repo root is .. (skills/)
    if not yaml_path.exists():
        return Registry(root=root)
    try:
        data = yaml.safe_load(yaml_path.read_text()) or {}
    except yaml.YAMLError as e:
        raise RegistryError(f"{yaml_path}: invalid yaml: {e}") from e
    if not isinstance(data, dict):
        raise RegistryError(f"{yaml_path}: top-level must be a mapping")
    raw_tools = data.get("tools") or []
    raw_pbs = data.get("playbooks") or []
    if not isinstance(raw_tools, list) or not isinstance(raw_pbs, list):
        raise RegistryError(f"{yaml_path}: 'tools' and 'playbooks' must be lists")
    tools = [_parse_tool(e, root, str(yaml_path)) for e in raw_tools]
    pbs = [_parse_playbook(e, root, str(yaml_path)) for e in raw_pbs]
    return Registry(tools=tools, playbooks=pbs, root=root)


def _default_registry_path() -> Path:
    env = os.environ.get("DEMIURGE_SKILLS_REGISTRY")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "registry.yaml"


# --- query API ---


def _agent_can_use_tool(
    *, tool: ToolEntry, agent_name: str,
    excludes: Sequence[str] = (), safety_max: Optional[str] = None,
) -> bool:
    """Apply scope + exclude + safety_max gates."""
    # Scope gate.
    if tool.scope == "restricted" and agent_name not in tool.allowed_agents:
        return False
    # Per-agent exclude pattern (glob style).
    import fnmatch

    if any(fnmatch.fnmatchcase(tool.id, pat) for pat in excludes):
        return False
    # Safety ceiling.
    if safety_max is not None:
        if _SAFETY_RANK[tool.safety_class] > _SAFETY_RANK[safety_max]:
            return False
    return True


def get_tools_for_agent(
    agent_name: str,
    *,
    excludes: Sequence[str] = (),
    safety_max: Optional[str] = None,
    registry: Optional[Registry] = None,
) -> List[Any]:
    """Return the LangChain BaseTool list this agent should see.

    ``excludes`` is a list of glob patterns matched against tool id; e.g.
    ``["security.*"]`` opts out of all tools under the security category.
    ``safety_max`` caps the safety class (e.g. ``"read-write"`` excludes
    destructive tools).

    Tools are dynamically imported from the path declared in the
    registry. Each tool module must expose ``build_tool() -> BaseTool``.
    """
    reg = registry or load_registry()
    out: List[Any] = []
    for tool in reg.tools:
        if not _agent_can_use_tool(
            tool=tool, agent_name=agent_name,
            excludes=excludes, safety_max=safety_max,
        ):
            continue
        out.append(_load_tool(tool))
    return out


def _load_tool(entry: ToolEntry) -> Any:
    """Import a tool module and call its ``build_tool()``."""
    spec = importlib.util.spec_from_file_location(
        f"skills_tool_{entry.id.replace('.', '_')}", entry.path
    )
    if spec is None or spec.loader is None:
        raise RegistryError(f"cannot import tool at {entry.path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "build_tool"):
        raise RegistryError(
            f"tool module {entry.path} must expose `build_tool() -> BaseTool`"
        )
    return module.build_tool()


def get_playbooks_for(
    agent_name: str,
    event: Any,
    *,
    max_playbooks: int = 3,
    registry: Optional[Registry] = None,
) -> List[Playbook]:
    """Return playbooks matching this agent + event. Trigger-match (v1).

    Delegates to ``skills.retrieval.get_playbooks_for``.
    """
    from . import retrieval

    return retrieval.get_playbooks_for(
        agent_name, event, max_playbooks=max_playbooks, registry=registry,
    )
