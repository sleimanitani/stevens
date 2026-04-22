"""Pending approvals protocol.

Reserved for v0.2 — when agents start requesting human approval for actions,
this model + the pending_approvals table are the protocol. v0.1 agents don't
use this (they only draft/label, no approval needed), but the table exists
so adding it later doesn't require a migration.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


ApprovalStatus = Literal["pending", "approved", "rejected", "expired"]


class PendingApproval(BaseModel):
    approval_id: UUID
    agent_name: str
    action_type: str  # e.g. "send_email", "send_whatsapp"
    context: dict[str, Any] = Field(default_factory=dict)
    status: ApprovalStatus = "pending"
    created_at: datetime
    resolved_at: Optional[datetime] = None
    resolution: dict[str, Any] = Field(default_factory=dict)
