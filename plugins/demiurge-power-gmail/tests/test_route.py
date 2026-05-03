"""Gmail → ChannelRoute mapping."""

from __future__ import annotations

from gmail_adapter.route import route_for_inbound
from shared.events import EmailReceivedEvent


def _event(account="gmail.personal", thread="thread-abc", from_="alice@example.com") -> EmailReceivedEvent:
    return EmailReceivedEvent(
        account_id=account, message_id="msg-1", thread_id=thread,
        **{"from": from_}, raw_ref="r",
    )


def test_route_basic():
    r = route_for_inbound(_event())
    assert r.channel_type == "gmail"
    assert r.account_id == "gmail.personal"
    assert r.target_id == "thread-abc"
    assert r.thread_id == "thread-abc"
    assert r.peer_id == "alice@example.com"


def test_route_preserves_thread_per_account():
    r = route_for_inbound(_event(account="gmail.work", thread="thread-99"))
    assert r.account_id == "gmail.work"
    assert r.target_id == "thread-99"
