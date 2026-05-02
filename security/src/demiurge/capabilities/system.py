"""``system.*`` capabilities — the privileged-execution protocol.

Four capabilities, of which only ``system.execute_privileged`` is
approval-gated:

- ``system.read_environment`` — host introspection (os_release, dpkg
  status). Read-only, not approval-gated.
- ``system.plan_install`` — agent submits a plan; Enkidu validates via
  the mechanism's validator and stores the plan_id. No execution.
- ``system.execute_privileged`` — execute a previously-validated plan.
  Approval-gated. Runs the executor, then a structural health probe.
  On health-pass, writes inventory; on fail, runs rollback.
- ``system.write_inventory`` — append-only inventory write, caller-bound.

See ``docs/protocols/privileged-execution.md``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from ..context import CapabilityContext
from ..mechanisms import get as get_mechanism
from ..mechanisms.base import ExecResult, ValidationError
from ..system_runtime import (
    InventoryRow,
    StoredPlan,
    SystemRuntime,
    make_inventory_id,
    make_plan_id,
)
from .registry import default_registry

log = logging.getLogger(__name__)


def _runtime(context: CapabilityContext) -> SystemRuntime:
    rt = context.extra.get("system") if isinstance(context.extra, dict) else None
    if not isinstance(rt, SystemRuntime):
        raise RuntimeError(
            "system.* capability invoked but no SystemRuntime configured "
            "in CapabilityContext.extra['system']"
        )
    return rt


# --- system.read_environment ---


@default_registry.capability(
    "system.read_environment",
    clear_params=["fields"],   # the structural query, not the result
)
async def read_environment(agent, params, context):
    """Narrow host introspection. Caller declares which fields to read.

    Supported field names (a subset for v0.3):
    - ``os_release``: contents of /etc/os-release as a dict.
    - ``dpkg_status``: takes ``package: <name>``; returns
      {installed: bool, version: str | null, raw_status: str}.
    - ``which``: takes ``binary: <name>``; returns whether it's on PATH.
    """
    fields = params.get("fields") or []
    if not isinstance(fields, list):
        raise RuntimeError("fields must be a list")
    rt = _runtime(context)
    result: Dict[str, Any] = {}
    for entry in fields:
        if not isinstance(entry, dict):
            raise RuntimeError(f"each field entry must be a dict, got {type(entry).__name__}")
        name = entry.get("name")
        if name == "os_release":
            result["os_release"] = await _read_os_release(rt)
        elif name == "dpkg_status":
            pkg = entry.get("package")
            if not isinstance(pkg, str):
                raise RuntimeError("dpkg_status requires 'package' string")
            result.setdefault("dpkg_status", {})[pkg] = await _query_dpkg(rt, pkg)
        elif name == "which":
            binary = entry.get("binary")
            if not isinstance(binary, str):
                raise RuntimeError("which requires 'binary' string")
            result.setdefault("which", {})[binary] = await _which(rt, binary)
        else:
            raise RuntimeError(f"unknown read_environment field: {name!r}")
    return result


async def _read_os_release(rt: SystemRuntime) -> Dict[str, str]:
    from ..mechanisms.base import Executor

    exe = Executor(argv=["cat", "/etc/os-release"], env={"LC_ALL": "C"}, timeout_seconds=5)
    res = await rt.run_subprocess(exe)
    if res.exit_code != 0:
        return {}
    out: Dict[str, str] = {}
    for line in res.stdout.decode("utf-8", errors="replace").splitlines():
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip().lower()] = v.strip().strip('"')
    return out


async def _query_dpkg(rt: SystemRuntime, package: str) -> Dict[str, Any]:
    from ..mechanisms.base import Executor

    exe = Executor(
        argv=["dpkg-query", "--show", "--showformat=${Status}|${Version}", package],
        env={"LC_ALL": "C"},
        timeout_seconds=5,
    )
    res = await rt.run_subprocess(exe)
    if res.exit_code != 0:
        return {"installed": False, "version": None, "raw_status": ""}
    text = res.stdout.decode("utf-8", errors="replace").strip()
    if "|" not in text:
        return {"installed": False, "version": None, "raw_status": text}
    status, version = text.split("|", 1)
    return {
        "installed": status == "install ok installed",
        "version": version or None,
        "raw_status": status,
    }


async def _which(rt: SystemRuntime, binary: str) -> Dict[str, Any]:
    from ..mechanisms.base import Executor

    exe = Executor(argv=["which", binary], env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin"}, timeout_seconds=5)
    res = await rt.run_subprocess(exe)
    return {
        "found": res.exit_code == 0,
        "path": res.stdout.decode("utf-8", errors="replace").strip() or None,
    }


# --- system.plan_install ---


@default_registry.capability(
    "system.plan_install",
    clear_params=["mechanism", "rationale"],
)
async def plan_install(agent, params, context):
    mechanism_name = params.get("mechanism")
    if not isinstance(mechanism_name, str):
        raise RuntimeError("'mechanism' is required")
    plan_body = params.get("plan_body")
    rollback_body = params.get("rollback_body")
    if not isinstance(plan_body, dict) or not isinstance(rollback_body, dict):
        raise RuntimeError("'plan_body' and 'rollback_body' are required dicts")

    mechanism = get_mechanism(mechanism_name)
    try:
        validated = mechanism.validate_plan(plan_body, rollback_body)
    except ValidationError as e:
        return {"validated": False, "errors": [str(e)], "field_path": e.field_path}

    rt = _runtime(context)
    now = context.clock()
    plan_id = make_plan_id()
    stored = StoredPlan(
        id=plan_id,
        proposing_agent=agent.name,
        mechanism=mechanism_name,
        plan_body=validated.plan_body,
        rollback_body=validated.rollback_body,
        rationale=params.get("rationale") if isinstance(params.get("rationale"), str) else None,
        proposed_at=now,
        expires_at=now + timedelta(seconds=rt.plan_ttl_seconds),
    )
    await rt.plan_store.insert(stored)
    return {
        "validated": True,
        "plan_id": plan_id,
        "expires_at": stored.expires_at.isoformat(),
    }


# --- system.execute_privileged ---


@default_registry.capability(
    "system.execute_privileged",
    clear_params=["plan_id", "mechanism", "rationale", "operation", "packages"],
)
async def execute_privileged(agent, params, context):
    plan_id = params.get("plan_id")
    if not isinstance(plan_id, str):
        raise RuntimeError("'plan_id' is required")
    rt = _runtime(context)
    stored = await rt.plan_store.get(plan_id)
    if stored is None:
        return {"outcome": "failed", "error": "plan not found", "plan_id": plan_id}
    now = context.clock()
    if stored.expires_at < now:
        await rt.plan_store.mark_executed(plan_id, "failed", None)
        return {"outcome": "failed", "error": "plan expired"}
    if stored.proposing_agent != agent.name:
        return {
            "outcome": "rejected",
            "error": "plan was proposed by a different agent",
        }

    mechanism = get_mechanism(stored.mechanism)
    # Re-validate (defense in depth).
    try:
        validated = mechanism.validate_plan(stored.plan_body, stored.rollback_body)
    except ValidationError as e:
        await rt.plan_store.mark_executed(plan_id, "failed", None)
        return {"outcome": "failed", "error": f"plan revalidation failed: {e}"}

    executor = mechanism.build_executor(validated)
    exec_result: ExecResult = await rt.run_subprocess(executor)
    log.info(
        "execute_privileged: plan=%s exit=%s timed_out=%s",
        plan_id, exec_result.exit_code, exec_result.timed_out,
    )

    hc = mechanism.health_check_spec(validated)
    probe = mechanism.build_health_probe(hc)
    probe_result: Optional[ExecResult] = None
    if probe is not None:
        probe_result = await rt.run_subprocess(probe)
    health_ok = mechanism.evaluate_health_check(hc, exec_result, probe_result)

    inventory_id: Optional[str] = None
    if health_ok:
        # Record installs only — for remove/purge we leave the soft-delete to
        # the rollback path, which writes mark_removed on its own inventory row.
        op = stored.plan_body.get("operation")
        if op == "install":
            inv_row = InventoryRow(
                id=make_inventory_id(),
                caller=agent.name,
                name=",".join(stored.plan_body.get("packages") or [])[:200],
                mechanism=stored.mechanism,
                plan_id=plan_id,
                installed_at=now,
                health_status="passed",
            )
            inventory_id = await rt.inventory.append(inv_row)
        else:
            # For remove/purge, find the prior install row and mark removed.
            packages = stored.plan_body.get("packages") or []
            existing = await rt.inventory.list_for(agent.name)
            for row in existing:
                if any(p in row.name.split(",") for p in packages):
                    await rt.inventory.mark_removed(row.id)
        await rt.plan_store.mark_executed(plan_id, "ok", inventory_id)
        return {
            "outcome": "ok",
            "exit_code": exec_result.exit_code,
            "timed_out": exec_result.timed_out,
            "health_check_result": "passed",
            "inventory_id": inventory_id,
        }

    # Health failed — automatic rollback.
    log.warning("health check failed for plan=%s; running rollback", plan_id)
    rollback_validated = mechanism.validate_rollback(validated)
    rb_executor = mechanism.build_executor(rollback_validated)
    rb_result = await rt.run_subprocess(rb_executor)
    await rt.plan_store.mark_executed(
        plan_id, "health_failed" if not exec_result.timed_out else "timed_out", None,
    )
    return {
        "outcome": "health_failed" if not exec_result.timed_out else "timed_out",
        "exit_code": exec_result.exit_code,
        "rollback_exit_code": rb_result.exit_code,
        "health_check_result": "failed",
    }


# --- system.write_inventory ---


@default_registry.capability(
    "system.write_inventory",
    clear_params=["name", "mechanism"],
)
async def write_inventory(agent, params, context):
    """Direct inventory write — used when an agent records state for an action
    that didn't go through execute_privileged (e.g. discovering a pre-existing
    install). Caller is bound from the verified agent name; agents can't forge
    rows for other callers."""
    rt = _runtime(context)
    name = params.get("name")
    mechanism = params.get("mechanism")
    if not isinstance(name, str) or not isinstance(mechanism, str):
        raise RuntimeError("'name' and 'mechanism' are required strings")
    row = InventoryRow(
        id=make_inventory_id(),
        caller=agent.name,
        name=name,
        mechanism=mechanism,
        plan_id=params.get("plan_id") or "external",
        version=params.get("version") if isinstance(params.get("version"), str) else None,
        location=params.get("location") if isinstance(params.get("location"), str) else None,
        sha256=params.get("sha256") if isinstance(params.get("sha256"), str) else None,
        health_status=params.get("health_status", "unknown"),
    )
    inventory_id = await rt.inventory.append(row)
    return {"inventory_id": inventory_id}
