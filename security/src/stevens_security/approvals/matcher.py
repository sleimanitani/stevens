"""Standing-approval matcher index.

Hot path for every approval-gated capability call. Loads ``standing_approvals``
rows into memory at boot; refreshes on grant/revoke via an explicit signal.

Match semantics:
  - For each standing approval indexed under (capability, caller):
    - Skip if revoked.
    - Skip if expired (timestamp expiry; session expiry is host-managed).
    - For each predicate in ``predicates`` JSON, AND-fold against the call's
      params. Missing predicate fields = "any" for that field.
  - First matching approval wins.

The index is a pure in-memory data structure; no DB hit on the hot path.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from .predicates import match_predicate

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class StandingApproval:
    id: str
    capability: str
    caller: str
    predicates: Dict[str, Any]                 # raw JSON; e.g. {"mechanism": "apt", "source": {"regex": "..."}}
    expires_at: Optional[datetime] = None      # naive timestamp expiry
    expires_session: Optional[str] = None      # session-bound expiry
    granted_at: Optional[datetime] = None
    granted_by: Optional[str] = None
    rationale: Optional[str] = None
    revoked_at: Optional[datetime] = None


@dataclass(frozen=True)
class MatchResult:
    matched: bool
    approval_id: Optional[str] = None
    reason: Optional[str] = None  # populated when matched=False on diagnostic path


class MatcherIndex:
    """In-memory index of active standing approvals.

    Construction is cheap. Reload via ``replace_all(approvals)``. Per-call
    matching scans only the (capability, caller) bucket — typically 0–5
    entries — so even with hundreds of total approvals the hot path stays
    O(1)-ish.
    """

    def __init__(
        self,
        approvals: Optional[List[StandingApproval]] = None,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        current_session: Optional[str] = None,
    ) -> None:
        self._clock = clock
        self._current_session = current_session
        self._buckets: Dict[tuple, List[StandingApproval]] = {}
        if approvals:
            self.replace_all(approvals)

    def replace_all(self, approvals: List[StandingApproval]) -> None:
        """Drop the current cache and rebuild from the given list."""
        new_buckets: Dict[tuple, List[StandingApproval]] = {}
        for sa in approvals:
            new_buckets.setdefault((sa.capability, sa.caller), []).append(sa)
        self._buckets = new_buckets

    def set_session(self, session_id: Optional[str]) -> None:
        self._current_session = session_id

    def __len__(self) -> int:
        return sum(len(b) for b in self._buckets.values())

    def match(
        self,
        *,
        capability: str,
        caller: str,
        params: Dict[str, Any],
    ) -> MatchResult:
        """Match a call against the active standing approvals.

        Returns the first matching approval (no ranking) or no-match. Callers
        treat no-match as "fall through to per-call approval queue."
        """
        bucket = self._buckets.get((capability, caller), ())
        now = self._clock()
        for sa in bucket:
            if sa.revoked_at is not None:
                continue
            if sa.expires_at is not None and sa.expires_at < now:
                continue
            if sa.expires_session is not None and sa.expires_session != self._current_session:
                continue
            if not _all_predicates_match(sa.predicates, params):
                continue
            return MatchResult(matched=True, approval_id=sa.id)
        return MatchResult(matched=False, reason="no standing approval matches")


def _all_predicates_match(predicates: Dict[str, Any], params: Dict[str, Any]) -> bool:
    """AND-fold every predicate in ``predicates`` against ``params``.

    Missing predicate fields are "any" — so an empty ``predicates`` dict
    matches every call. Predicate field names that aren't in ``params``
    are treated as a non-match (the caller asked for a specific value
    and the call didn't provide one).
    """
    if not predicates:
        return True
    # `param_matchers` is a nested matcher dict on call params. The other
    # top-level keys are install-protocol-specific shorthand: mechanism,
    # source, packages. They look up the same-named field in `params`.
    extra_matchers = predicates.get("param_matchers") or {}
    for key, predicate in predicates.items():
        if key == "param_matchers":
            continue
        if key not in params:
            # The approval requires a field the call didn't carry.
            return False
        if not match_predicate(predicate, params[key]):
            return False
    if isinstance(extra_matchers, dict):
        for key, predicate in extra_matchers.items():
            if key not in params:
                return False
            if not match_predicate(predicate, params[key]):
                return False
    return True
