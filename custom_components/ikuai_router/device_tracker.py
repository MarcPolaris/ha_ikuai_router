"""iKuai Router 设备追踪平台。"""
from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker import ScannerEntity, SourceType
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_TRACKERS, LOGGER
from .coordinator import IkuaiCoordinator, normalize_mac
from .entity import IkuaiEntity, main_device


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: IkuaiCoordinator = entry.runtime_data
    added: set[str] = set()

    @callback
    def _sync() -> None:
        trackers = entry.options.get(CONF_TRACKERS) or entry.data.get(CONF_TRACKERS) or {}
        valid = {f"{coordinator.gwid}_track_{normalize_mac(mac)}" for mac in trackers}

        new_entities = []
        for mac_raw, conf in trackers.items():
            mac = normalize_mac(mac_raw)
            if not mac:
                continue
            uid = f"{coordinator.gwid}_track_{mac}"
            if uid in added:
                continue
            name = conf.get("name") if isinstance(conf, dict) else None
            new_entities.append(IkuaiTracker(coordinator, mac, name, uid))
            added.add(uid)
        if new_entities:
            async_add_entities(new_entities, True)

        registry = er.async_get(hass)
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
            if entity.domain != "device_tracker":
                continue
            if entity.unique_id not in valid:
                LOGGER.debug("移除已取消追踪的实体 %s", entity.entity_id)
                registry.async_remove(entity.entity_id)
                added.discard(entity.unique_id)

    _sync()
    entry.async_on_unload(coordinator.async_add_listener(_sync))


class IkuaiTracker(IkuaiEntity, ScannerEntity):
    """终端在线追踪（路由器来源）。

    继承 ScannerEntity：由 ``is_connected`` 自动推导「在家 / 离家」，
    并支持在实体设置里给终端挂一个关联区域（例如把它归到"二楼"）。
    """

    _attr_has_entity_name = False
    _attr_source_type = SourceType.ROUTER
    _attr_entity_registry_enabled_default = True

    def __init__(
        self, coordinator: IkuaiCoordinator, mac: str, name: str | None, uid: str
    ) -> None:
        super().__init__(coordinator, main_device(coordinator), f"track_{mac}")
        self._attr_unique_id = uid
        self._mac = mac
        self._custom_name = name

    @property
    def unique_id(self) -> str | None:
        # ScannerEntity 默认用 mac_address 当 unique_id，这里显式沿用基类的稳定 ID
        return self._attr_unique_id

    @property
    def _client(self) -> dict[str, Any]:
        return (self.coordinator.data or {}).get("clients", {}).get(self._mac, {})

    @property
    def name(self) -> str | None:
        return self._custom_name or self._client.get("name") or self._mac

    @property
    def is_connected(self) -> bool:
        return bool(self._client)

    @property
    def mac_address(self) -> str | None:
        return self._mac

    @property
    def ip_address(self) -> str | None:
        return self._client.get("ip")

    @property
    def hostname(self) -> str | None:
        client = self._client
        return client.get("termname") or client.get("hostname") or None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        client = self._client
        if not client:
            return {"mac": self._mac, "configured": self._custom_name is not None}
        return {
            "mac": self._mac,
            "ip": client.get("ip"),
            "vendor": client.get("vendor"),
            "client_type": client.get("client_type"),
            "interface": client.get("interface"),
            "ssid": client.get("ssid") or None,
            "signal_dbm": client.get("signal") or None,
            "ap_mac": client.get("apmac") or None,
            "online_since": client.get("online_since"),
            "total_up_mb": round(client.get("total_up", 0) / 1024 / 1024, 2),
            "total_down_mb": round(client.get("total_down", 0) / 1024 / 1024, 2),
            "buffered": client.get("offline_buffering", False),
        }
