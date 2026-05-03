"""``python -m demiurge.runtime`` — start the runtime daemon.

The systemd user unit `demiurge-runtime.service` invokes this. Also
useful for foreground debugging: `uv run python -m demiurge.runtime`.
"""

from __future__ import annotations

from .daemon import main


if __name__ == "__main__":
    raise SystemExit(main())
