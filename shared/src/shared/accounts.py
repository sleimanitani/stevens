"""Channel account model and lookup.

One row per (channel, real-world account) pair. The account_id is the stable
slug used everywhere — in topics, in tool calls, in logs.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


ChannelType = Literal["gmail", "whatsapp", "calendar"]
AccountStatus = Literal["active", "paused", "broken"]


class ChannelAccount(BaseModel):
    account_id: str
    channel_type: ChannelType
    display_name: str
    credentials: dict[str, Any]
    status: AccountStatus = "active"
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


async def get_account(conn, account_id: str) -> Optional[ChannelAccount]:
    """Fetch a single account by id."""
    row = await conn.fetchrow(
        "SELECT * FROM channel_accounts WHERE account_id = $1",
        account_id,
    )
    if not row:
        return None
    return ChannelAccount.model_validate(dict(row))


async def list_accounts(
    conn,
    channel_type: Optional[ChannelType] = None,
    status: Optional[AccountStatus] = "active",
) -> list[ChannelAccount]:
    """List accounts, optionally filtered by channel and status."""
    clauses = []
    params: list[Any] = []
    if channel_type:
        params.append(channel_type)
        clauses.append(f"channel_type = ${len(params)}")
    if status:
        params.append(status)
        clauses.append(f"status = ${len(params)}")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = await conn.fetch(f"SELECT * FROM channel_accounts {where}", *params)
    return [ChannelAccount.model_validate(dict(r)) for r in rows]
