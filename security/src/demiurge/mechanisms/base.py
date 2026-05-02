"""Mechanism base shapes — pure data structures, no execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol


class ValidationError(Exception):
    """Raised when a plan fails mechanism-specific validation."""

    def __init__(self, message: str, *, field_path: Optional[str] = None) -> None:
        super().__init__(message)
        self.field_path = field_path


@dataclass(frozen=True)
class ValidatedPlan:
    """A plan that passed validation. Ready to be turned into an executor."""

    mechanism: str
    plan_body: Dict[str, Any]
    rollback_body: Dict[str, Any]
    rationale: Optional[str] = None


@dataclass(frozen=True)
class Executor:
    """Pure data — argv + env + timeout. The capability handler runs the subprocess."""

    argv: List[str]
    env: Dict[str, str]
    timeout_seconds: int


@dataclass(frozen=True)
class ExecResult:
    """Result of running an Executor. Returned by the privileged-exec capability."""

    exit_code: int
    stdout: bytes
    stderr: bytes
    timed_out: bool = False


@dataclass(frozen=True)
class HealthCheckSpec:
    """Mechanism-defined post-install verification."""

    type: str                          # mechanism-specific (e.g. "dpkg_installed")
    spec: Dict[str, Any] = field(default_factory=dict)


class Mechanism(Protocol):
    name: str

    def validate_plan(self, plan_body: Dict[str, Any], rollback_body: Dict[str, Any]) -> ValidatedPlan: ...
    def build_executor(self, plan: ValidatedPlan) -> Executor: ...
    def health_check_spec(self, plan: ValidatedPlan) -> HealthCheckSpec: ...
    def evaluate_health_check(self, hc: HealthCheckSpec, exec_result: ExecResult, probe_result: Optional[ExecResult]) -> bool:
        """Given the install exec_result and an optional probe (e.g. dpkg-query),
        return True iff the install was structurally successful."""
    def build_health_probe(self, hc: HealthCheckSpec) -> Optional[Executor]:
        """Optional: a separate probe command Enkidu runs after the install
        to verify state. None means the health check is purely on exec_result."""
    def validate_rollback(self, install_plan: ValidatedPlan) -> ValidatedPlan: ...
