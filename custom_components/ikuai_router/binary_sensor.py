"""iKuai Router 二元传感器平台（告警与状态）。"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_CPU_THRESHOLD,
    CONF_ENABLE_CLIENTS,
    CONF_MEM_THRESHOLD,
    CONF_TRACKERS,
    DEFAULT_CPU_THRESHOLD,
    DEFAULT_MEM_THRESHOLD,
)
from .coordinator import IkuaiCoordinator, normalize_mac
from .entity import IkuaiEntity, interfaces_device, main_device


@dataclass(frozen=True, kw_only=True)
class IkuaiBinaryDescription(BinarySensorEntityDescription):
    """带取值函数的二元传感器描述。"""

    is_on_fn: Callable[[dict[str, Any], ConfigEntry], bool] = lambda _d, _e: False
    attr_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


def _threshold(entry: ConfigEntry, key: str, default: int) -> float:
    try:
        return float(entry.options.get(key, default))
    except (TypeError, ValueError):
        return float(default)


ALERT_SENSORS: Final = (
    IkuaiBinaryDescription(
        key="cpu_high",
        translation_key="cpu_high",
        icon="mdi:speedometer",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda d, e: (d["system"]["cpu_load"] or 0)
        >= _threshold(e, CONF_CPU_THRESHOLD, DEFAULT_CPU_THRESHOLD),
        attr_fn=lambda d: {"cpu_load": d["system"]["cpu_load"]},
    ),
    IkuaiBinaryDescription(
        key="memory_high",
        translation_key="memory_high",
        icon="mdi:memory",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda d, e: (d["system"]["memory_usage"] or 0)
        >= _threshold(e, CONF_MEM_THRESHOLD, DEFAULT_MEM_THRESHOLD),
        attr_fn=lambda d: dict(d["system"]["memory_detail"]),
    ),
    IkuaiBinaryDescription(
        key="log_disk_low",
        translation_key="log_disk_low",
        icon="mdi:harddisk-remove",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda d, e: d["system"]["log_disk"].get("use_percent") is not None
        and d["system"]["log_disk"]["use_percent"] >= 90,
        attr_fn=lambda d: dict(d["system"]["log_disk"]),
    ),
    IkuaiBinaryDescription(
        key="firmware_update",
        translation_key="firmware_update",
        icon="mdi:package-up",
        device_class=BinarySensorDeviceClass.UPDATE,
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda d, e: bool(d["maintenance"].get("new_version_available")),
        attr_fn=lambda d: dict(d["maintenance"]["detail"]),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: IkuaiCoordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = []

    for desc in ALERT_SENSORS:
        entities.append(IkuaiAlertSensor(coordinator, desc))

    for iface_name, iface in (coordinator.data or {}).get("interfaces", {}).items():
        if iface.get("is_wan"):
            entities.append(IkuaiWanConnectivitySensor(coordinator, iface_name))

    if entry.options.get(CONF_ENABLE_CLIENTS):
        for mac in entry.options.get(CONF_TRACKERS) or {}:
            mac = normalize_mac(mac)
            if mac:
                entities.append(IkuaiClientOnlineSensor(coordinator, mac))

    async_add_entities(entities)


class IkuaiAlertSensor(IkuaiEntity, BinarySensorEntity):
    """主设备告警类二元传感器。"""

    entity_description: IkuaiBinaryDescription

    def __init__(self, coordinator: IkuaiCoordinator, desc: IkuaiBinaryDescription) -> None:
        super().__init__(coordinator, main_device(coordinator), f"alert_{desc.key}")
        self.entity_description = desc

    @property
    def is_on(self) -> bool:
        return self.entity_description.is_on_fn(self.coordinator.data, self.coordinator.entry)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if not self.entity_description.attr_fn:
            return None
        return self.entity_description.attr_fn(self.coordinator.data)


class IkuaiWanConnectivitySensor(IkuaiEntity, BinarySensorEntity):
    """每条 WAN 线路的连通性。"""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_translation_key = "wan_connectivity"

    def __init__(self, coordinator: IkuaiCoordinator, iface_name: str) -> None:
        super().__init__(coordinator, interfaces_device(coordinator), f"wan_conn_{iface_name}")
        self._iface_name = iface_name
        self._attr_translation_placeholders = {"iface": iface_name}
        self._attr_icon = "mdi:wan"

    @property
    def _iface(self) -> dict[str, Any]:
        return (self.coordinator.data or {}).get("interfaces", {}).get(self._iface_name, {})

    @property
    def is_on(self) -> bool:
        return bool(self._iface.get("connected"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        iface = self._iface
        return {
            "ip": iface.get("ip"),
            "gateway": iface.get("gateway"),
            "protocol": iface.get("protocol"),
            "auto_switch": iface.get("auto_switch"),
            "message": iface.get("errmsg"),
            "uptime_since": iface.get("connect_since"),
        }


class IkuaiClientOnlineSensor(IkuaiEntity, BinarySensorEntity):
    """终端在线状态（带离线宽限）。"""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_has_entity_name = False

    def __init__(self, coordinator: IkuaiCoordinator, mac: str) -> None:
        super().__init__(coordinator, main_device(coordinator), f"client_online_{mac}")
        self._mac = mac

    @property
    def name(self) -> str | None:
        client = (self.coordinator.data or {}).get("clients", {}).get(self._mac, {})
        return f"{client.get('name') or self._mac} 在线"

    @property
    def is_on(self) -> bool:
        return self._mac in (self.coordinator.data or {}).get("clients", {})

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        client = (self.coordinator.data or {}).get("clients", {}).get(self._mac, {})
        return {
            "mac": self._mac,
            "ip": client.get("ip"),
            "vendor": client.get("vendor"),
            "interface": client.get("interface"),
            "buffered": client.get("offline_buffering", False),
        }
