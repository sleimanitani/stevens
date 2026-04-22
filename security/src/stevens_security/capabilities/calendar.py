"""Google Calendar capabilities.

All Calendar operations agents or adapters care about, broker-mediated.
Access tokens never leave the Security Agent's process.
"""

from __future__ import annotations

from typing import Any, Dict

from ..context import CapabilityContext
from ..identity import RegisteredAgent
from ..outbound.calendar import CalendarClient
from .registry import capability


def _cal(ctx: CapabilityContext) -> CalendarClient:
    outbound = ctx.outbound
    if outbound is None:
        raise RuntimeError("no outbound client configured")
    client = getattr(outbound, "calendar", None)
    if not isinstance(client, CalendarClient):
        raise RuntimeError("outbound.calendar is not a CalendarClient")
    return client


@capability("calendar.list_calendars")
async def cal_list_calendars(
    agent: RegisteredAgent, params: Dict[str, Any], context: CapabilityContext
) -> Dict[str, Any]:
    return await _cal(context).list_calendars(params["account_id"])


@capability(
    "calendar.list_events",
    clear_params=[
        "calendar_id",
        "time_min",
        "time_max",
        "max_results",
        "single_events",
        "order_by",
        "page_token",
    ],
)
async def cal_list_events(
    agent: RegisteredAgent, params: Dict[str, Any], context: CapabilityContext
) -> Dict[str, Any]:
    return await _cal(context).list_events(
        params["account_id"],
        params.get("calendar_id", "primary"),
        time_min=params.get("time_min"),
        time_max=params.get("time_max"),
        q=params.get("q"),
        max_results=int(params.get("max_results", 50)),
        single_events=bool(params.get("single_events", True)),
        order_by=params.get("order_by", "startTime"),
        sync_token=params.get("sync_token"),
        page_token=params.get("page_token"),
    )


@capability("calendar.get_event", clear_params=["calendar_id", "event_id"])
async def cal_get_event(
    agent: RegisteredAgent, params: Dict[str, Any], context: CapabilityContext
) -> Dict[str, Any]:
    return await _cal(context).get_event(
        params["account_id"],
        params["calendar_id"],
        params["event_id"],
    )


@capability("calendar.insert_event", clear_params=["calendar_id", "send_updates"])
async def cal_insert_event(
    agent: RegisteredAgent, params: Dict[str, Any], context: CapabilityContext
) -> Dict[str, Any]:
    return await _cal(context).insert_event(
        params["account_id"],
        params.get("calendar_id", "primary"),
        params["event"],
        send_updates=params.get("send_updates", "none"),
    )


@capability(
    "calendar.patch_event",
    clear_params=["calendar_id", "event_id", "send_updates"],
)
async def cal_patch_event(
    agent: RegisteredAgent, params: Dict[str, Any], context: CapabilityContext
) -> Dict[str, Any]:
    return await _cal(context).patch_event(
        params["account_id"],
        params["calendar_id"],
        params["event_id"],
        params["patch"],
        send_updates=params.get("send_updates", "none"),
    )


@capability(
    "calendar.delete_event",
    clear_params=["calendar_id", "event_id", "send_updates"],
)
async def cal_delete_event(
    agent: RegisteredAgent, params: Dict[str, Any], context: CapabilityContext
) -> Dict[str, Any]:
    return await _cal(context).delete_event(
        params["account_id"],
        params["calendar_id"],
        params["event_id"],
        send_updates=params.get("send_updates", "none"),
    )


@capability(
    "calendar.watch_events",
    clear_params=["calendar_id", "channel_id", "webhook_url", "ttl_seconds"],
)
async def cal_watch_events(
    agent: RegisteredAgent, params: Dict[str, Any], context: CapabilityContext
) -> Dict[str, Any]:
    return await _cal(context).watch_events(
        params["account_id"],
        params["calendar_id"],
        params["channel_id"],
        params["webhook_url"],
        channel_token=params.get("channel_token"),
        ttl_seconds=params.get("ttl_seconds"),
    )


@capability("calendar.stop_channel", clear_params=["channel_id", "resource_id"])
async def cal_stop_channel(
    agent: RegisteredAgent, params: Dict[str, Any], context: CapabilityContext
) -> Dict[str, Any]:
    return await _cal(context).stop_channel(
        params["account_id"],
        params["channel_id"],
        params["resource_id"],
    )
