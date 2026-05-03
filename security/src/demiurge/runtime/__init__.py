"""Demiurge runtime supervisor — v0.11 step 7.

Reads forged-Creature + forged-Power state and runs each one as a
supervised subprocess (or, for polling-mode powers, as scheduled tick
calls). One supervisor instance manages N plugins — replaces the
hardcoded 6-unit catalog from v0.10's `bootstrap.systemd`.

Submodules:

- ``supervisor`` (7.1) — `Supervisor` class managing subprocess
  lifecycle. Exposes ``add`` / ``start_all`` / ``stop_all`` / ``pause`` /
  ``resume`` / ``status``. Async-driven; each supervised process has its
  own watcher coroutine.
- ``runtime`` (7.2+, coming) — entry-point discovery + power runtime
  integration + Creature runtime integration + the systemd user unit.

The supervisor's design contract is narrow on purpose: it manages
*processes*, full stop. It doesn't know about manifests, blessings,
or forge results. Higher layers (7.2+) translate forged-state into
``SupervisedProcess`` records and hand them to the supervisor.
"""

from .power_runtime import (  # noqa: F401
    PowerRuntime,
    PowerRuntimeError,
    PowerRuntimeRegistration,
)
from .supervisor import (  # noqa: F401
    BackoffPolicy,
    ProcessNotFound,
    ProcessStatus,
    SupervisedProcess,
    Supervisor,
    SupervisorClosed,
)
