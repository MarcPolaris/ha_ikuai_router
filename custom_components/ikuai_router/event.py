"""iKuai Router 事件平台：终端上下线、系统日志、DDNS、无线、消息中心。"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Final

from homeassistant.components.event import EventEntity, EventEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import IkuaiCoordinator
from .entity import IkuaiEntity, main_device

EVENT_KINDS: Final = (
    ("presence", "terminal_presence", "mdi:account-clock"),
    ("system", "system_log", "mdi:math-log"),
    ("ddns", "ddns_log", "mdi:dns"),
    ("wireless", "wireless_log", "mdi:wifi-marker"),
    ("messages", "message_center", "mdi:bell-ring"),
)


@dataclass(frozen=True, kw_only=True)
class IkuaiEventDescription(EventEntityDescription):
    """事件描述。"""

    data_key: str


DESCRIPTIONS: Final = tuple(
    IkuaiEventDescription(
        key=translation_key,
        translation_key=translation_key,
        icon=icon,
        data_key=data_key,
    )
    for data_key, translation_key, icon in EVENT_KINDS
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: IkuaiCoordinator = entry.runtime_data
    async_add_entities(IkuaiEvent(coordinator, desc) for desc in DESCRIPTIONS)


class IkuaiEvent(IkuaiEntity, EventEntity):
    """把协调器增量数据转成 HA 事件。"""

    entity_description: IkuaiEventDescription
    _attr_event_types = ["online", "offline", "message", "ddns", "system", "wireless"]

    def __init__(self, coordinator: IkuaiCoordinator, desc: IkuaiEventDescription) -> None:
        super().__init__(coordinator, main_device(coordinator), f"event_{desc.key}")
        self.entity_description = desc
        self._seen: set[str] = set()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._drain()
        self.async_on_remove(self.coordinator.async_add_listener(self._drain))

    @callback
    def _drain(self) -> None:
        items = (self.coordinator.data or {}).get("events", {}).get(
            self.entity_description.data_key, []
        )
        fired = False
        for item in items:
            if not isinstance(item, dict):
                continue
            key = self._identity(item)
            if key in self._seen:
                continue
            self._seen.add(key)
            self._fire(item)
            fired = True
        if len(self._seen) > 1500:
            self._seen = set(list(self._seen)[-500:])
        if fired:
            self.async_write_ha_state()

    def _identity(self, item: dict[str, Any]) -> str:
        raw = item.get("id")
        if raw is None:
            raw = "|".join(str(item.get(k)) for k in ("type", "title", "detail", "content", "mac"))
        return f"{self.entity_description.data_key}_{raw}"

    def _fire(self, item: dict[str, Any]) -> None:
        kind = self.entity_description.data_key
        if kind == "presence":
            event_type = "offline" if int(item.get("logout_time") or 0) > 0 else "online"
            self._trigger_event(
                event_type,
                {
                    "mac": item.get("mac"),
                    "ip": item.get("ip_addr"),
                    "name": item.get("termname") or item.get("client_model"),
                    "client_type": item.get("client_type"),
                    "vendor": item.get("client_vendor"),
                    "online_time": item.get("online_time"),
                    "timestamp": item.get("date_time"),
                },
            )
        elif kind == "system":
            self._trigger_event(
                "system",
                {
                    "content": item.get("content"),
                    "level": item.get("level"),
                    "module": item.get("module"),
                    "timestamp": item.get("timestamp"),
                },
            )
        elif kind == "ddns":
            self._trigger_event(
                "ddns",
                {
                    "domain": item.get("domain"),
                    "ip": item.get("ip_addr"),
                    "result": item.get("result"),
                    "event": item.get("event"),
                    "timestamp": item.get("timestamp"),
                },
            )
        elif kind == "wireless":
            self._trigger_event(
                "wireless",
                {
                    "mac": item.get("mac"),
                    "name": item.get("termname"),
                    "ssid": item.get("ssid"),
                    "action": item.get("action"),
                    "signal": item.get("signal"),
                    "reason": item.get("errmsg"),
                },
            )
        else:
            self._trigger_event(
                "message",
                {
                    "title": item.get("title"),
                    "detail": item.get("detail"),
                    "type": item.get("type"),
                    "status": item.get("status"),
                    "received_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                },
            )
