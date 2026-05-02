"""``stevens reset`` — wipe local Stevens state for fresh-install testing.

What gets wiped:
- Sealed store directory (``$STEVENS_SECURITY_SECRETS`` or default)
- Audit logs (``$STEVENS_SECURITY_AUDIT_DIR`` or default)
- Agent keys + env profiles (``~/.config/stevens/agents/``)
- Janus persistent browser profile (``~/.config/stevens/janus-profile/``)
- OS keyring passphrase entry (``stevens passphrase forget``)
- All Stevens-owned Postgres tables (TRUNCATE; schema preserved):
    channel_accounts, skill_proposals, standing_approvals,
    approval_requests, install_plans, environment_packages,
    events, subscription_cursors, followups, approvals
- PDF corpus cache (``./.pdf-corpus/``)

What is **not** touched:
- The git repo / source tree
- Your venv (``./.venv/``)
- gcloud auth (separate operator concern)
- Postgres itself (we truncate; we don't drop the database)
- Downloaded ``client_secret*.json`` files in ~/Downloads/ (those are yours)
- Migrations (the schema stays)
- Any data outside ``$STEVENS_SECURITY_*`` paths and ``~/.config/stevens/``

Default mode is **dry-run**: prints the wipe plan, takes no action.
Pass ``--yes`` to actually execute (with one final confirmation), or
``--force`` to skip confirmation entirely.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, List, Optional


log = logging.getLogger(__name__)


# Stevens-owned tables to TRUNCATE (RESTART IDENTITY CASCADE for safety).
_STEVENS_TABLES = (
    "approval_requests",
    "standing_approvals",
    "environment_packages",
    "install_plans",
    "skill_proposals",
    "followups",
    "approvals",
    "subscription_cursors",
    "events",
    "channel_accounts",
)


@dataclass
class ResetPlan:
    """What ``stevens reset`` would wipe. Pure data; printable."""

    sealed_store_dir: Optional[Path] = None
    audit_dir: Optional[Path] = None
    agents_config_dir: Optional[Path] = None
    janus_profile_dir: Optional[Path] = None
    keyring_entry: bool = False
    pdf_corpus_dir: Optional[Path] = None
    postgres_tables: List[str] = field(default_factory=list)

    def render(self) -> str:
        lines = ["stevens reset — would wipe:"]
        if self.sealed_store_dir is not None:
            exists = self.sealed_store_dir.exists()
            lines.append(f"  - sealed store dir: {self.sealed_store_dir} {'[exists]' if exists else '[absent]'}")
        if self.audit_dir is not None:
            exists = self.audit_dir.exists()
            lines.append(f"  - audit log dir:    {self.audit_dir} {'[exists]' if exists else '[absent]'}")
        if self.agents_config_dir is not None:
            exists = self.agents_config_dir.exists()
            lines.append(f"  - agent profiles:   {self.agents_config_dir} {'[exists]' if exists else '[absent]'}")
        if self.janus_profile_dir is not None:
            exists = self.janus_profile_dir.exists()
            lines.append(f"  - Janus profile:    {self.janus_profile_dir} {'[exists]' if exists else '[absent]'}")
        if self.keyring_entry:
            lines.append("  - OS keyring entry: stevens-security/vault (if present)")
        if self.pdf_corpus_dir is not None:
            exists = self.pdf_corpus_dir.exists()
            lines.append(f"  - PDF corpus cache: {self.pdf_corpus_dir} {'[exists]' if exists else '[absent]'}")
        if self.postgres_tables:
            lines.append(f"  - Postgres tables (TRUNCATE; schema preserved):")
            for t in self.postgres_tables:
                lines.append(f"      - {t}")
        lines.append("")
        lines.append("NOT touched: repo source, venv, gcloud auth, ~/Downloads/, your Google account.")
        return "\n".join(lines)


def _config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "stevens"


def _default_sealed_store() -> Path:
    return Path(os.environ.get("STEVENS_SECURITY_SECRETS", "/var/lib/stevens/secrets"))


def _default_audit_dir() -> Path:
    return Path(os.environ.get("STEVENS_SECURITY_AUDIT_DIR", "/var/lib/stevens/audit"))


def _default_pdf_corpus_dir() -> Path:
    return Path.cwd() / ".pdf-corpus"


def build_plan(
    *,
    keep_sealed: bool = False,
    keep_audit: bool = False,
    keep_agents: bool = False,
    keep_janus_profile: bool = False,
    keep_keyring: bool = False,
    keep_postgres: bool = False,
    keep_pdf_corpus: bool = False,
) -> ResetPlan:
    plan = ResetPlan()
    if not keep_sealed:
        plan.sealed_store_dir = _default_sealed_store()
    if not keep_audit:
        plan.audit_dir = _default_audit_dir()
    if not keep_agents:
        plan.agents_config_dir = _config_dir() / "agents"
    if not keep_janus_profile:
        plan.janus_profile_dir = _config_dir() / "janus-profile"
    if not keep_keyring:
        plan.keyring_entry = True
    if not keep_pdf_corpus:
        plan.pdf_corpus_dir = _default_pdf_corpus_dir()
    if not keep_postgres:
        plan.postgres_tables = list(_STEVENS_TABLES)
    return plan


def _rm_dir(path: Path) -> str:
    if not path.exists():
        return f"  · skipped (absent): {path}"
    shutil.rmtree(path)
    return f"  ✓ wiped: {path}"


def _clear_keyring() -> str:
    try:
        from . import keyring_passphrase

        keyring_passphrase.clear()
        return "  ✓ cleared keyring entry stevens-security/vault"
    except Exception as e:  # noqa: BLE001
        return f"  · keyring not available or already empty ({e})"


async def _truncate_postgres_tables(tables: List[str]) -> str:
    """TRUNCATE each Stevens-owned table. Schema stays.

    Tables that don't exist (e.g. fresh DB before migrations) get skipped
    with a note rather than an error.
    """
    from shared.db import connection

    truncated: List[str] = []
    skipped: List[str] = []
    async with connection() as conn:
        async with conn.cursor() as cur:
            for tbl in tables:
                try:
                    await cur.execute(f"TRUNCATE TABLE {tbl} RESTART IDENTITY CASCADE")
                    truncated.append(tbl)
                except Exception as e:  # noqa: BLE001
                    skipped.append(f"{tbl} ({e})")
        await conn.commit()
    parts = []
    if truncated:
        parts.append(f"  ✓ TRUNCATE'd {len(truncated)} table(s): {', '.join(truncated)}")
    if skipped:
        parts.append(f"  · skipped (missing or in-use): {', '.join(skipped)}")
    if not parts:
        parts.append("  · no Postgres tables to wipe")
    return "\n".join(parts)


async def execute_plan(plan: ResetPlan) -> List[str]:
    """Execute the wipe plan. Returns a list of human-readable result lines."""
    out: List[str] = []
    if plan.sealed_store_dir is not None:
        out.append(_rm_dir(plan.sealed_store_dir))
    if plan.audit_dir is not None:
        out.append(_rm_dir(plan.audit_dir))
    if plan.agents_config_dir is not None:
        out.append(_rm_dir(plan.agents_config_dir))
    if plan.janus_profile_dir is not None:
        out.append(_rm_dir(plan.janus_profile_dir))
    if plan.keyring_entry:
        out.append(_clear_keyring())
    if plan.pdf_corpus_dir is not None:
        out.append(_rm_dir(plan.pdf_corpus_dir))
    if plan.postgres_tables:
        # Postgres step is async + may fail if no DATABASE_URL.
        try:
            line = await _truncate_postgres_tables(plan.postgres_tables)
            out.append(line)
        except Exception as e:  # noqa: BLE001
            out.append(
                f"  · Postgres wipe skipped: {e} "
                "(set DATABASE_URL and bring up the DB first)"
            )
    return out


def post_wipe_next_steps() -> str:
    """The 'now what?' message printed after a real wipe — drives the
    fresh-install experience Sol wants to test."""
    return "\n".join([
        "",
        "fresh install — to start over from scratch:",
        "  1. uv run stevens secrets init",
        "  2. uv run stevens passphrase remember     # opt-in: silent unlocks",
        "  3. uv run stevens wizard google --project-id stevens-personal",
        "     (or whatever channel you're onboarding first)",
        "  4. uv run stevens janus run google_oauth_client --project-id stevens-personal",
        "  5. uv run stevens onboard gmail --client-json ~/Downloads/client_secret_*.json -- --id gmail.personal --name 'Sol personal'",
        "  6. uv run stevens agent provision email_pm --preset email_pm",
        "  7. uv run stevens agent run email_pm",
        "",
    ])
