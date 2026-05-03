"""Thin async HTTP client around signal-cli-rest-api.

Exposes only the endpoints the adapter needs:
- ``health()``           — daemon liveness probe
- ``send_text()``        — POST /v2/send
- ``receive()``          — GET /v1/receive/{phone} (polling)
- ``qr_link()``          — GET /v1/qrcodelink/{device_name}?number={phone}

Daemon: https://github.com/bbernhard/signal-cli-rest-api
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx


class SignalCliError(Exception):
    """Raised on signal-cli-rest-api transport / response errors."""


@dataclass(frozen=True)
class IncomingMessage:
    msg_id: str                     # we synthesize from timestamp
    source_phone: Optional[str]
    source_uuid: Optional[str]
    source_name: Optional[str]
    group_id: Optional[str]
    is_group: bool
    text: str
    timestamp: int
    attachments: list = field(default_factory=list)


def _parse_envelope(raw: Any) -> Optional[IncomingMessage]:
    """Parse one envelope dict into an IncomingMessage. None if it's not a real message."""
    if not isinstance(raw, dict):
        return None
    env = raw.get("envelope") if "envelope" in raw else raw
    if not isinstance(env, dict):
        return None
    data = env.get("dataMessage")
    if not isinstance(data, dict):
        return None
    text = data.get("message") or ""
    if not isinstance(text, str) or not text.strip():
        return None
    group_info = data.get("groupInfo") or {}
    group_id = group_info.get("groupId") if isinstance(group_info, dict) else None
    timestamp = int(data.get("timestamp") or env.get("timestamp") or 0)
    return IncomingMessage(
        msg_id=str(timestamp),
        source_phone=env.get("source"),
        source_uuid=env.get("sourceUuid"),
        source_name=env.get("sourceName"),
        group_id=group_id if isinstance(group_id, str) else None,
        is_group=bool(group_id),
        text=text,
        timestamp=timestamp,
        attachments=list(data.get("attachments") or []),
    )


class SignalCliClient:
    def __init__(
        self,
        *,
        base_url: str,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._transport = transport
        self._timeout = timeout_seconds

    async def _request(
        self, method: str, path: str, **kwargs,
    ) -> httpx.Response:
        async with httpx.AsyncClient(
            transport=self._transport, timeout=self._timeout,
        ) as client:
            try:
                resp = await client.request(method, f"{self._base}{path}", **kwargs)
            except httpx.HTTPError as e:
                raise SignalCliError(f"signal-cli transport error: {e}") from e
        if resp.status_code >= 500:
            raise SignalCliError(f"signal-cli {resp.status_code}: {resp.text[:200]}")
        if resp.status_code >= 400:
            raise SignalCliError(f"signal-cli {resp.status_code}: {resp.text[:200]}")
        return resp

    async def health(self) -> Dict[str, Any]:
        resp = await self._request("GET", "/v1/about")
        try:
            return resp.json()
        except Exception as e:  # noqa: BLE001
            raise SignalCliError(f"malformed health response: {e}") from e

    async def send_text(
        self, *, from_phone: str, to: str, body: str,
    ) -> Dict[str, Any]:
        """POST /v2/send. ``to`` is a phone number (DM) or a group id."""
        recipients = [to]
        payload = {
            "number": from_phone,
            "recipients": recipients,
            "message": body,
        }
        resp = await self._request("POST", "/v2/send", json=payload)
        try:
            return resp.json()
        except Exception:  # noqa: BLE001
            return {}

    async def receive(self, *, phone: str) -> List[IncomingMessage]:
        """GET /v1/receive/{phone}. Returns parsed dataMessages only."""
        resp = await self._request("GET", f"/v1/receive/{phone}")
        try:
            envelopes = resp.json()
        except Exception as e:  # noqa: BLE001
            raise SignalCliError(f"malformed receive payload: {e}") from e
        if not isinstance(envelopes, list):
            return []
        out: List[IncomingMessage] = []
        for env in envelopes:
            msg = _parse_envelope(env)
            if msg is not None:
                out.append(msg)
        return out

    async def qr_link(
        self, *, phone: str, device_name: str = "Stevens",
    ) -> bytes:
        """GET /v1/qrcodelink/{device_name}?number={phone}. Returns PNG bytes."""
        resp = await self._request(
            "GET", f"/v1/qrcodelink/{device_name}",
            params={"number": phone},
        )
        return resp.content
