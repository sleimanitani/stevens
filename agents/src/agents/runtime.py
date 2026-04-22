"""Agent runtime.

Reads registry.yaml, starts one subscription task per (agent, subscription)
pair, owns crash isolation.

v0.1: single Python process, all agents as async tasks.
v0.2: same interface, but agents can run in their own processes if needed.

Usage:
    uv run python -m agents.runtime
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
import signal
from pathlib import Path
from typing import Any, Protocol

import yaml

from shared import bus
from shared.db import close_pool
from shared.events import BaseEvent


log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


class AgentModule(Protocol):
    """Every agent module must expose a `handle(event)` async function."""

    async def handle(self, event: BaseEvent, config: dict[str, Any]) -> None: ...


def load_registry() -> dict:
    """Load registry.yaml from this package's directory."""
    registry_path = Path(__file__).parent / "registry.yaml"
    with registry_path.open() as f:
        return yaml.safe_load(f)


def load_agent_module(name: str):
    """Dynamically import agents.<n>.agent."""
    return importlib.import_module(f"agents.{name}.agent")


async def _run_subscription(
    agent_name: str,
    pattern: str,
    agent_config: dict[str, Any],
    stop_event: asyncio.Event,
) -> None:
    """Run one (agent, pattern) subscription. Isolated: crashes logged, task restarted."""
    agent = load_agent_module(agent_name)

    async def handler(event: BaseEvent) -> None:
        # Enforce account scoping at the runtime level, not inside the agent.
        allowed = agent_config.get("accounts", "all")
        if allowed != "all" and event.account_id not in allowed:
            log.debug(
                "agent=%s skipping event account=%s (not in allowed=%s)",
                agent_name, event.account_id, allowed,
            )
            return

        try:
            await agent.handle(event, agent_config)
        except Exception:
            log.exception("agent=%s handler error event_id=%s", agent_name, event.event_id)

    # Subscriber id includes pattern so two subscriptions by the same agent
    # have independent cursors.
    subscriber_id = f"{agent_name}::{pattern}"

    while not stop_event.is_set():
        try:
            await bus.subscribe(subscriber_id, pattern, handler, stop_event)
            break  # stop_event was set
        except Exception:
            log.exception("agent=%s pattern=%s subscription crashed, restarting in 5s",
                          agent_name, pattern)
            await asyncio.sleep(5)


async def main() -> None:
    registry = load_registry()
    stop_event = asyncio.Event()

    # Signal handlers for graceful shutdown
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    tasks: list[asyncio.Task] = []

    for agent_cfg in registry.get("agents", []):
        if not agent_cfg.get("enabled", True):
            log.info("skipping disabled agent: %s", agent_cfg["name"])
            continue

        name = agent_cfg["name"]
        log.info("starting agent: %s", name)

        for pattern in agent_cfg.get("subscribes", []):
            task = asyncio.create_task(
                _run_subscription(name, pattern, agent_cfg, stop_event),
                name=f"sub:{name}:{pattern}",
            )
            tasks.append(task)

        # TODO: schedule handling (cron) lands in v0.1.1 — not in first day's work.

    log.info("runtime started with %d subscription tasks", len(tasks))

    await stop_event.wait()
    log.info("shutdown signal received, stopping...")

    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await close_pool()
    log.info("shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
