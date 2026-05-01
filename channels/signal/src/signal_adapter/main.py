"""Signal adapter entrypoint — FastAPI app + inbound polling loop.

Polls signal-cli-rest-api for new messages, parses each into a
``SignalMessageEvent``, publishes to the bus.

Env vars:
- ``SIGNAL_DAEMON_URL``         daemon base URL (e.g. http://signal-daemon:8080)
- ``SIGNAL_ACCOUNT_ID``         our Stevens account_id (e.g. signal.personal)
- ``SIGNAL_PHONE``              the linked phone number
- ``SIGNAL_POLL_INTERVAL_S``    seconds between receive polls (default 2.0)
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Optional

from fastapi import FastAPI

from shared import bus
from shared.events import SignalMessageEvent

from .client import IncomingMessage, SignalCliClient, SignalCliError


log = logging.getLogger(__name__)


def _to_event(account_id: str, msg: IncomingMessage) -> SignalMessageEvent:
    return SignalMessageEvent(
        account_id=account_id,
        msg_id=msg.msg_id,
        source_phone=msg.source_phone,
        source_uuid=msg.source_uuid,
        source_name=msg.source_name,
        group_id=msg.group_id,
        is_group=msg.is_group,
        text=msg.text,
        attachments=msg.attachments,
        timestamp=msg.timestamp,
        raw_ref=str(uuid.uuid4()),
    )


async def poll_loop(
    *,
    client: SignalCliClient,
    account_id: str,
    phone: str,
    interval_seconds: float = 2.0,
    max_iterations: Optional[int] = None,
    publisher=None,
) -> None:
    """Forever loop polling /v1/receive. Test seam: max_iterations + custom publisher."""
    publish = publisher or bus.publish
    backoff = 1.0
    iterations = 0
    while True:
        try:
            msgs = await client.receive(phone=phone)
            backoff = 1.0
        except SignalCliError as e:
            log.warning("signal receive failed: %s; backing off %.1fs", e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(60.0, backoff * 2)
            iterations += 1
            if max_iterations is not None and iterations >= max_iterations:
                return
            continue

        for msg in msgs:
            event = _to_event(account_id, msg)
            try:
                await publish(event)
            except Exception:  # noqa: BLE001
                log.exception("failed to publish signal event %s", event.msg_id)

        iterations += 1
        if max_iterations is not None and iterations >= max_iterations:
            return
        await asyncio.sleep(interval_seconds)


def make_app() -> FastAPI:
    app = FastAPI(title="signal-adapter")

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    return app


async def _amain() -> None:
    logging.basicConfig(
        level=os.environ.get("STEVENS_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    daemon = os.environ["SIGNAL_DAEMON_URL"]
    account = os.environ["SIGNAL_ACCOUNT_ID"]
    phone = os.environ["SIGNAL_PHONE"]
    interval = float(os.environ.get("SIGNAL_POLL_INTERVAL_S", "2.0"))

    client = SignalCliClient(base_url=daemon)
    log.info("signal-adapter polling %s @ %s every %.1fs", phone, daemon, interval)
    await poll_loop(
        client=client, account_id=account, phone=phone, interval_seconds=interval,
    )


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(_amain())
