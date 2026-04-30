"""Pure plan-construction logic — no I/O.

Given (package_name, env_snapshot), produces an apt install plan body and
its paired rollback. Validation happens broker-side via
``system.plan_install``; this is the agent's "what to ask for" logic.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


_DEFAULT_SOURCES = {
    "ubuntu": {"repo": "archive.ubuntu.com/ubuntu", "component": "main"},
    "debian": {"repo": "deb.debian.org/debian", "component": "main"},
}

_SUITE_BY_OS = {
    ("ubuntu", "24.04"): "noble",
    ("ubuntu", "22.04"): "jammy",
    ("ubuntu", "20.04"): "focal",
    ("debian", "12"): "bookworm",
    ("debian", "11"): "bullseye",
}


class PlanBuildError(Exception):
    """Raised when the host environment is incompatible with the request."""


def build_apt_plan(
    *, package: str, env_snapshot: Dict[str, Any]
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Build (plan_body, rollback_body) for an apt install.

    ``env_snapshot`` is the response from ``system.read_environment`` covering
    at minimum ``os_release.id`` and ``os_release.version_id``.
    """
    osr = (env_snapshot or {}).get("os_release") or {}
    os_id = osr.get("id")
    version = osr.get("version_id")
    if os_id not in _DEFAULT_SOURCES:
        raise PlanBuildError(
            f"unsupported OS {os_id!r} for apt mechanism; expected ubuntu/debian"
        )
    suite = _SUITE_BY_OS.get((os_id, version))
    if suite is None:
        raise PlanBuildError(
            f"unsupported OS version {os_id} {version!r} for apt mechanism"
        )
    repo_info = _DEFAULT_SOURCES[os_id]
    plan_body = {
        "operation": "install",
        "packages": [package],
        "source": {
            "repo": repo_info["repo"],
            "suite": suite,
            "component": repo_info["component"],
        },
        "flags": ["--no-install-recommends"],
    }
    rollback_body = {
        "operation": "purge",
        "packages": [package],
        "flags": [],
    }
    return plan_body, rollback_body
