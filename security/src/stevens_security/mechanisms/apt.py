"""apt mechanism — Debian/Ubuntu package installs.

Plan body shape::

    mechanism: apt
    operation: install               # install | remove | purge
    packages: [tesseract-ocr, ...]
    source:
      repo: deb.debian.org/debian
      suite: bookworm
      component: main
    flags: [--no-install-recommends]   # subset of FLAGS_ALLOW

The structural health check is ``dpkg-query --status`` reporting "install ok
installed" for each package after install, or "deinstall ok"/"unknown ok" for
remove/purge. apt's own exit code alone isn't trusted — it's been known to
return 0 on partial failures.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from . import register
from .base import (
    Executor,
    HealthCheckSpec,
    Mechanism,
    ValidatedPlan,
    ValidationError,
)


_PACKAGE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9.+-]*$")
_VALID_OPERATIONS = {"install", "remove", "purge"}
_FLAGS_ALLOW = frozenset(
    [
        "--no-install-recommends",
        "--no-upgrade",
        "-y",
        "--yes",
    ]
)
_FLAGS_FORBID = frozenset(
    [
        "--force-yes",
        "--allow-unauthenticated",
        "--allow-downgrades",
    ]
)
_SOURCE_ALLOW = [
    re.compile(r"^deb\.debian\.org(/.*)?$"),
    re.compile(r"^archive\.ubuntu\.com(/.*)?$"),
    re.compile(r"^security\.ubuntu\.com(/.*)?$"),
    re.compile(r"^security\.debian\.org(/.*)?$"),
]
_VALID_SUITES = frozenset(
    [
        "bookworm", "bullseye", "trixie",          # debian
        "jammy", "noble", "focal",                  # ubuntu
        "stable", "stable-security",
    ]
)
_VALID_COMPONENTS = frozenset(["main", "universe", "multiverse", "contrib", "non-free", "non-free-firmware"])

_INVERSE_OP = {"install": "purge", "purge": "install", "remove": "install"}


class _AptMechanism:
    name = "apt"

    def validate_plan(
        self, plan_body: Dict[str, Any], rollback_body: Dict[str, Any]
    ) -> ValidatedPlan:
        self._validate_body(plan_body, ctx="plan")
        # Check inverse-op BEFORE validating rollback body — an install-shaped
        # rollback would fail body validation for missing source, but the real
        # problem is that install can't BE a rollback.
        rb_op = rollback_body.get("operation") if isinstance(rollback_body, dict) else None
        if rb_op != _INVERSE_OP.get(plan_body["operation"]):
            raise ValidationError(
                f"rollback operation must be the inverse of install: "
                f"{plan_body['operation']} → expected {_INVERSE_OP.get(plan_body['operation'])}, "
                f"got {rb_op!r}",
                field_path="rollback.operation",
            )
        self._validate_body(rollback_body, ctx="rollback")
        plan_pkgs = set(plan_body["packages"])
        rollback_pkgs = set(rollback_body["packages"])
        if not rollback_pkgs.issubset(plan_pkgs):
            raise ValidationError(
                f"rollback packages must be a subset of plan packages: "
                f"unexpected {sorted(rollback_pkgs - plan_pkgs)}",
                field_path="rollback.packages",
            )
        return ValidatedPlan(
            mechanism="apt",
            plan_body=dict(plan_body),
            rollback_body=dict(rollback_body),
        )

    def _validate_body(self, body: Dict[str, Any], *, ctx: str) -> None:
        if not isinstance(body, dict):
            raise ValidationError(f"{ctx} must be a mapping")
        op = body.get("operation")
        if op not in _VALID_OPERATIONS:
            raise ValidationError(
                f"{ctx}.operation must be one of {sorted(_VALID_OPERATIONS)}; got {op!r}",
                field_path=f"{ctx}.operation",
            )
        packages = body.get("packages")
        if not isinstance(packages, list) or not packages:
            raise ValidationError(f"{ctx}.packages must be a non-empty list")
        for p in packages:
            if not isinstance(p, str) or not _PACKAGE_NAME_RE.match(p):
                raise ValidationError(
                    f"invalid {ctx} package name {p!r}",
                    field_path=f"{ctx}.packages",
                )
        # source allowed for install; rollback typically inherits implicitly.
        if op == "install":
            source = body.get("source") or {}
            if not isinstance(source, dict):
                raise ValidationError(f"{ctx}.source must be a mapping")
            repo = source.get("repo")
            if not isinstance(repo, str) or not any(p.match(repo) for p in _SOURCE_ALLOW):
                raise ValidationError(
                    f"{ctx}.source.repo {repo!r} not on allow-list",
                    field_path=f"{ctx}.source.repo",
                )
            suite = source.get("suite")
            if suite not in _VALID_SUITES:
                raise ValidationError(
                    f"{ctx}.source.suite {suite!r} not on allow-list",
                    field_path=f"{ctx}.source.suite",
                )
            component = source.get("component")
            if component not in _VALID_COMPONENTS:
                raise ValidationError(
                    f"{ctx}.source.component {component!r} not on allow-list",
                    field_path=f"{ctx}.source.component",
                )
        # flags allow/forbid lists
        flags = body.get("flags") or []
        if not isinstance(flags, list):
            raise ValidationError(f"{ctx}.flags must be a list")
        for f in flags:
            if not isinstance(f, str):
                raise ValidationError(f"{ctx}.flags entries must be strings")
            if f in _FLAGS_FORBID:
                raise ValidationError(
                    f"forbidden flag {f!r} in {ctx}.flags",
                    field_path=f"{ctx}.flags",
                )
            if f not in _FLAGS_ALLOW:
                raise ValidationError(
                    f"unrecognized flag {f!r} in {ctx}.flags (not on allow-list)",
                    field_path=f"{ctx}.flags",
                )

    def build_executor(self, plan: ValidatedPlan) -> Executor:
        body = plan.plan_body
        op = body["operation"]
        # apt-get is the scriptable front-end. -y for non-interactive.
        # No shell; argv-only.
        flags = list(body.get("flags") or [])
        if "-y" not in flags and "--yes" not in flags:
            flags.insert(0, "-y")
        argv = ["apt-get", op, *flags, *body["packages"]]
        env = {
            "DEBIAN_FRONTEND": "noninteractive",
            "LC_ALL": "C",
            "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        }
        return Executor(argv=argv, env=env, timeout_seconds=300)

    def health_check_spec(self, plan: ValidatedPlan) -> HealthCheckSpec:
        op = plan.plan_body["operation"]
        return HealthCheckSpec(
            type="dpkg_installed" if op == "install" else "dpkg_removed",
            spec={"packages": list(plan.plan_body["packages"])},
        )

    def build_health_probe(self, hc: HealthCheckSpec) -> Optional[Executor]:
        # We use dpkg-query to probe each package independently; for v0.3
        # we run one combined call.
        packages = hc.spec.get("packages") or []
        if not packages:
            return None
        # dpkg-query --show --showformat='${Status}\n' pkg1 pkg2 …
        argv = [
            "dpkg-query", "--show",
            "--showformat=${Package} ${Status}\\n",
            *packages,
        ]
        return Executor(argv=argv, env={"LC_ALL": "C"}, timeout_seconds=15)

    def evaluate_health_check(
        self,
        hc: HealthCheckSpec,
        exec_result,
        probe_result,
    ) -> bool:
        # Install / remove must have exited 0 OR the probe must show the
        # expected end-state. (apt sometimes returns nonzero on lock-held
        # transient failures that nonetheless succeed; the probe is the
        # source of truth.)
        if probe_result is None or probe_result.exit_code not in (0, 1):
            # exit 1 from dpkg-query is "not installed" — meaningful for remove.
            return False
        text = probe_result.stdout.decode("utf-8", errors="replace")
        packages = hc.spec.get("packages") or []
        # Map package name → status string.
        statuses: Dict[str, str] = {}
        for line in text.splitlines():
            parts = line.split(maxsplit=1)
            if len(parts) == 2:
                statuses[parts[0]] = parts[1]
        for p in packages:
            status = statuses.get(p, "")
            if hc.type == "dpkg_installed":
                if status != "install ok installed":
                    return False
            elif hc.type == "dpkg_removed":
                # After remove: "deinstall ok config-files"; after purge: "unknown ok not-installed"
                # or the package isn't listed at all.
                if status and not (
                    status.startswith("deinstall ok") or status.startswith("unknown ok")
                ):
                    return False
            else:
                return False
        return True

    def validate_rollback(self, install_plan: ValidatedPlan) -> ValidatedPlan:
        # Re-validate the rollback half as if it were a fresh plan submission;
        # paired with a NULL rollback (rollbacks don't rollback further).
        synthetic_rollback_body = dict(install_plan.rollback_body)
        # The rollback's own "rollback" — if anything went wrong cleaning up,
        # we'd attempt a re-install. Symmetric.
        synthetic_inverse = {
            "operation": _INVERSE_OP[synthetic_rollback_body["operation"]],
            "packages": list(install_plan.plan_body["packages"]),
            "flags": list(install_plan.plan_body.get("flags") or []),
        }
        if synthetic_inverse["operation"] == "install":
            synthetic_inverse["source"] = dict(install_plan.plan_body["source"])
        return self.validate_plan(synthetic_rollback_body, synthetic_inverse)


_apt = _AptMechanism()
register("apt", _apt)
