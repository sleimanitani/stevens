"""``demiurge runtime`` CLI — v0.11 step 7.4.

Operator-facing commands for the long-lived ``demiurge-runtime``
daemon (the systemd user unit that supervises every Power + Creature).

Subcommands:

- ``demiurge runtime start [--foreground]`` — start the daemon. By
  default, just prints the systemd command to run (the "right" way
  to start the daemon is via systemctl). With ``--foreground``, runs
  in this shell — useful for debugging.
- ``demiurge runtime stop`` — connect to the daemon's UDS and send a
  shutdown request.
- ``demiurge runtime status`` — query the daemon for live status.
- ``demiurge runtime reload`` — ask the daemon to re-discover plugins.

Communication with a running daemon goes over a UDS at
``$XDG_RUNTIME_DIR/demiurge/runtime.sock`` (or
``~/.local/state/demiurge/runtime.sock`` fallback). Mode 0600 — only
the owning operator can connect.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .runtime.daemon import default_socket_path, send_request


def _print_table(rows: list[dict]) -> None:
    """Tiny inline table renderer for `runtime status`."""
    if not rows:
        print("  (no processes registered)")
        return
    name_w = max(len(r["name"]) for r in rows)
    for r in rows:
        running = "●" if r["is_running"] else "○"
        pid = str(r["pid"]) if r["pid"] is not None else "—"
        print(
            f"  {running} {r['name']:<{name_w}}  "
            f"state={r['desired_state']:<8}  "
            f"pid={pid:<7}  "
            f"restarts={r['restart_count']}"
        )


# ----------------------------- start -----------------------------------


def cmd_runtime_start(args: argparse.Namespace) -> int:
    """Print the systemd start command, or run the daemon in the foreground."""
    if args.foreground:
        # Defer to the daemon's main; runs in this shell.
        from .runtime.daemon import main as daemon_main

        return daemon_main()

    print(
        "To start the runtime daemon as a systemd user service:\n"
        "  systemctl --user start demiurge-runtime\n"
        "\n"
        "To run in the foreground (for debugging):\n"
        "  uv run demiurge runtime start --foreground\n"
        "\n"
        "(`demiurge runtime start` is intentionally not a systemctl\n"
        "wrapper — keep system service control explicit.)"
    )
    return 0


# ----------------------------- stop -----------------------------------


def cmd_runtime_stop(args: argparse.Namespace) -> int:
    socket_path = Path(args.socket) if args.socket else default_socket_path()
    try:
        resp = send_request(
            {"op": "shutdown"}, socket_path=socket_path, timeout=args.timeout
        )
    except (ConnectionRefusedError, FileNotFoundError):
        print(
            f"runtime daemon is not running (no socket at {socket_path})",
            file=sys.stderr,
        )
        return 1
    if not resp.get("ok"):
        print(f"daemon refused: {resp.get('error')}", file=sys.stderr)
        return 1
    print("shutdown requested")
    return 0


# ----------------------------- status -----------------------------------


def cmd_runtime_status(args: argparse.Namespace) -> int:
    socket_path = Path(args.socket) if args.socket else default_socket_path()
    try:
        resp = send_request(
            {"op": "status"}, socket_path=socket_path, timeout=args.timeout
        )
    except (ConnectionRefusedError, FileNotFoundError):
        print(
            f"runtime daemon is not running (no socket at {socket_path})",
            file=sys.stderr,
        )
        return 1

    if not resp.get("ok"):
        print(f"daemon error: {resp.get('error')}", file=sys.stderr)
        return 1

    data = resp.get("data") or {}
    if args.json:
        print(json.dumps(data, indent=2))
        return 0

    print(f"runtime daemon at {data.get('socket_path', '?')}: running")
    print()
    print("supervised processes:")
    _print_table(data.get("processes") or [])
    return 0


# ----------------------------- reload -----------------------------------


def cmd_runtime_reload(args: argparse.Namespace) -> int:
    socket_path = Path(args.socket) if args.socket else default_socket_path()
    try:
        resp = send_request(
            {"op": "reload"}, socket_path=socket_path, timeout=args.timeout
        )
    except (ConnectionRefusedError, FileNotFoundError):
        print(
            f"runtime daemon is not running (no socket at {socket_path})",
            file=sys.stderr,
        )
        return 1
    if not resp.get("ok"):
        print(f"reload failed: {resp.get('error')}", file=sys.stderr)
        return 1
    data = resp.get("data") or {}
    print(
        f"reload ok: {data.get('powers_registered', 0)} power(s), "
        f"{data.get('creatures_registered', 0)} creature(s) registered"
    )
    return 0


# ----------------------------- argparse wiring --------------------------


def add_runtime_parser(top: argparse._SubParsersAction) -> None:
    rt = top.add_parser(
        "runtime",
        help="manage the long-lived runtime daemon (supervisor)",
        description=(
            "The runtime daemon supervises every Power + Creature. "
            "It runs as a systemd user service (`demiurge-runtime.service`). "
            "These subcommands are the operator surface for inspecting + "
            "controlling it; talk to the daemon over its UDS."
        ),
    )
    sub = rt.add_subparsers(dest="subcmd", required=True)

    s_start = sub.add_parser(
        "start", help="hint for starting the daemon (or run in foreground)"
    )
    s_start.add_argument(
        "--foreground",
        action="store_true",
        help="run the daemon in this shell (debug mode)",
    )
    s_start.set_defaults(fn=cmd_runtime_start)

    s_stop = sub.add_parser("stop", help="ask the running daemon to shut down")
    s_stop.add_argument("--socket", help="override UDS path")
    s_stop.add_argument(
        "--timeout", type=float, default=5.0, help="IPC timeout seconds"
    )
    s_stop.set_defaults(fn=cmd_runtime_stop)

    s_status = sub.add_parser("status", help="query daemon status")
    s_status.add_argument("--socket")
    s_status.add_argument("--timeout", type=float, default=5.0)
    s_status.add_argument(
        "--json", action="store_true", help="emit JSON instead of table"
    )
    s_status.set_defaults(fn=cmd_runtime_status)

    s_reload = sub.add_parser(
        "reload", help="ask the daemon to re-discover plugins"
    )
    s_reload.add_argument("--socket")
    s_reload.add_argument("--timeout", type=float, default=5.0)
    s_reload.set_defaults(fn=cmd_runtime_reload)
