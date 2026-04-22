"""Email PM–specific tools.

These tools are specific to the email PM's job: tracking followups and
applying the PM label taxonomy. They live with the agent, not in the shared
tool factory, because they encode this agent's domain.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Literal

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from shared.db import connection


PM_CATEGORIES = ["pm/urgent", "pm/waiting-on-them", "pm/waiting-on-me", "pm/fyi", "pm/done"]


class FollowupInput(BaseModel):
    account_id: str
    thread_id: str
    direction: Literal["waiting_on_them", "waiting_on_me"]
    deadline: str = Field(description="ISO-8601 deadline, e.g. '2026-04-29T17:00:00Z'")
    note: str = Field(description="One-line reminder of what's expected")


class CategorizeInput(BaseModel):
    account_id: str
    thread_id: str
    category: Literal["pm/urgent", "pm/waiting-on-them", "pm/waiting-on-me", "pm/fyi", "pm/done"]


async def _log_followup(account_id: str, thread_id: str, direction: str, deadline: str, note: str) -> str:
    """Insert a followup row."""
    deadline_dt = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
    async with connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO followups (account_id, thread_id, direction, deadline, note)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING followup_id
                """,
                (account_id, thread_id, direction, deadline_dt, note),
            )
            row = await cur.fetchone()
        await conn.commit()
    return json.dumps({"ok": True, "followup_id": str(row[0])})


async def _list_overdue_followups() -> str:
    """Return open followups past their deadline."""
    now = datetime.now(timezone.utc)
    async with connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT followup_id, account_id, thread_id, direction, deadline, note
                FROM followups
                WHERE status = 'open' AND deadline < %s
                ORDER BY deadline ASC
                """,
                (now,),
            )
            rows = await cur.fetchall()
    return json.dumps([
        {
            "followup_id": str(r[0]),
            "account_id": r[1],
            "thread_id": r[2],
            "direction": r[3],
            "deadline": r[4].isoformat(),
            "note": r[5],
        }
        for r in rows
    ])


# StructuredTool needs sync wrappers; bridge to async via asyncio.run
def _sync_log_followup(account_id: str, thread_id: str, direction: str, deadline: str, note: str) -> str:
    return asyncio.run(_log_followup(account_id, thread_id, direction, deadline, note))


def _sync_list_overdue() -> str:
    return asyncio.run(_list_overdue_followups())


def get_email_pm_tools() -> list[BaseTool]:
    return [
        StructuredTool.from_function(
            func=_sync_log_followup,
            name="log_followup",
            description=(
                "Record a followup for a thread. direction='waiting_on_them' means they owe us "
                "a response; 'waiting_on_me' means we owe them. deadline is ISO-8601 in UTC."
            ),
            args_schema=FollowupInput,
        ),
        StructuredTool.from_function(
            func=_sync_list_overdue,
            name="list_overdue_followups",
            description="List all open followups whose deadline has passed.",
            args_schema=BaseModel,  # no args
        ),
    ]
