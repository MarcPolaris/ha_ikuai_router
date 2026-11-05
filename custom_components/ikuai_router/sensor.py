"""iKuai Router 传感器平台。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Final

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfDataRate,
    UnitOfInformation,
    UnitOfTime,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_ENABLE_CLIENTS, CONF_TRACKERS
from .coordinator import IkuaiCoordinator, normalize_mac
from .entity import (
    IkuaiEntity,
    disk_device,
    interfaces_device,
    main_device,
    maintenance_device,
    network_device,
)


@dataclass(frozen=True, kw_only=True)
class IkuaiSensorDescription(SensorEntityDescription):
    """带取值函数的描述符。"""

    value_fn: Callable[[dict[str, Any]], Any] = lambda _: None
    attr_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


def _short_version(value: Any) -> Any:
    if not value:
        return None
    match = re.search(r"\d+(?:\.\d+){1,3}", str(value))
    return match.group(0) if match else str(value)


SYSTEM_SENSORS: Final = (
    IkuaiSensorDescription(
        key="cpu_load",
        translation_key="cpu_load",
        icon="mdi:cpu-64-bit",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: d["system"]["cpu_load"],
        attr_fn=lambda d: {"cores": d["system"]["cpu_cores"], "freq": d["system"]["freq"]},
    ),
    IkuaiSensorDescription(
        key="memory_usage",
        translation_key="memory_usage",
        icon="mdi:memory",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: d["system"]["memory_usage"],
        attr_fn=lambda d: d["system"]["memory_detail"],
    ),
    IkuaiSensorDescription(
        key="uptime",
        translation_key="uptime",
        icon="mdi:clock-outline",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.DAYS,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d["system"]["uptime"],
    ),
    IkuaiSensorDescription(
        key="temperature",
        translation_key="temperature",
        icon="mdi:thermometer",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d["system"]["temperature"],
    ),
    IkuaiSensorDescription(
        key="online_users",
        translation_key="online_users",
        icon="mdi:account-multiple",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="台",
        value_fn=lambda d: d["system"]["online_users"],
        attr_fn=lambda d: d["system"]["online_user_detail"],
    ),
    IkuaiSensorDescription(
        key="connection_count",
        translation_key="connection_count",
        icon="mdi:lan-connect",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="个",
        value_fn=lambda d: d["system"]["connection_count"],
        attr_fn=lambda d: d["system"]["connect_detail"],
    ),
    IkuaiSensorDescription(
        key="upload_speed",
        translation_key="upload_speed",
        icon="mdi:upload-network",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.BYTES_PER_SECOND,
        suggested_unit_of_measurement=UnitOfDataRate.MEGABYTES_PER_SECOND,
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d["system"]["upload"],
        attr_fn=lambda d: {"ipv6_upload_speed_mb": d["system"]["v6_stats"]["upload_speed"]},
    ),
    IkuaiSensorDescription(
        key="download_speed",
        translation_key="download_speed",
        icon="mdi:download-network",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.BYTES_PER_SECOND,
        suggested_unit_of_measurement=UnitOfDataRate.MEGABYTES_PER_SECOND,
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d["system"]["download"],
        attr_fn=lambda d: {"ipv6_download_speed_mb": d["system"]["v6_stats"]["download_speed"]},
    ),
    IkuaiSensorDescription(
        key="total_upload",
        translation_key="total_upload",
        icon="mdi:upload-lock",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
        suggested_display_precision=2,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda d: d["system"]["total_up"],
    ),
    IkuaiSensorDescription(
        key="total_download",
        translation_key="total_download",
        icon="mdi:download-lock",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
        suggested_display_precision=2,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda d: d["system"]["total_down"],
    ),
    IkuaiSensorDescription(
        key="wan_ip",
        translation_key="wan_ip",
        icon="mdi:ip-network-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d["system"]["wan_ip"],
        attr_fn=lambda d: {
            "all_wan_ips": d["system"]["wan_ip_list"],
            "wan_online": f"{d['wan']['online']}/{d['wan']['total']}",
        },
    ),
)

WIRELESS_SENSORS: Final = (
    IkuaiSensorDescription(
        key="ap_online",
        translation_key="ap_online",
        icon="mdi:access-point",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="台",
        value_fn=lambda d: d["system"]["ap_online"],
        attr_fn=lambda d: d["system"]["wireless_detail"],
    ),
)

NETWORK_SENSORS: Final = (
    IkuaiSensorDescription(
        key="dhcp_static",
        translation_key="dhcp_static",
        icon="mdi:ip-network",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="条",
        value_fn=lambda d: d["dhcp"]["total"],
        attr_fn=lambda d: {"bindings": d["dhcp"]["items"][:50]},
    ),
    IkuaiSensorDescription(
        key="traffic_today",
        translation_key="traffic_today",
        icon="mdi:chart-bar",
        native_unit_of_measurement=UnitOfInformation.MEGABYTES,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=1,
        value_fn=lambda d: d["traffic"]["total_mb"],
        attr_fn=lambda d: {
            "clients": d["traffic"]["client_count"],
            "top_talker": d["traffic"]["top_name"],
            "ranking": d["traffic"]["ranking"],
        },
    ),
)

MAINTENANCE_SENSORS: Final = (
    IkuaiSensorDescription(
        key="firmware_version",
        translation_key="firmware_version",
        icon="mdi:chip",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _short_version(d["system"]["verinfo"].get("version"))
        or _short_version(d["system"]["ver_string"]),
        attr_fn=lambda d: dict(d["system"]["verinfo"]),
    ),
    IkuaiSensorDescription(
        key="upgrade_status",
        translation_key="upgrade_status",
        icon="mdi:update",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d["maintenance"]["state"],
        attr_fn=lambda d: d["maintenance"]["detail"],
    ),
    IkuaiSensorDescription(
        key="latest_backup",
        translation_key="latest_backup",
        icon="mdi:database-check",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d["backup"].get("filename"),
        attr_fn=lambda d: {k: v for k, v in d["backup"].items() if k != "filename"},
    ),
)

INTERFACE_SENSORS: Final = (
    IkuaiSensorDescription(
        key="upload",
        translation_key="iface_upload",
        icon="mdi:upload-network",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.BYTES_PER_SECOND,
        suggested_unit_of_measurement=UnitOfDataRate.MEGABYTES_PER_SECOND,
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    IkuaiSensorDescription(
        key="download",
        translation_key="iface_download",
        icon="mdi:download-network",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.BYTES_PER_SECOND,
        suggested_unit_of_measurement=UnitOfDataRate.MEGABYTES_PER_SECOND,
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    IkuaiSensorDescription(
        key="total_up",
        translation_key="iface_total_up",
        icon="mdi:upload",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
        suggested_display_precision=2,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    IkuaiSensorDescription(
        key="total_down",
        translation_key="iface_total_down",
        icon="mdi:download",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
        suggested_display_precision=2,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    IkuaiSensorDescription(
        key="conn",
        translation_key="iface_conn",
        icon="mdi:lan-connect",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="个",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

WAN_V6_SENSORS: Final = (
    IkuaiSensorDescription(
        key="v6_upload",
        translation_key="wan_v6_upload",
        icon="mdi:upload-network",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.BYTES_PER_SECOND,
        suggested_unit_of_measurement=UnitOfDataRate.MEGABYTES_PER_SECOND,
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    IkuaiSensorDescription(
        key="v6_download",
        translation_key="wan_v6_download",
        icon="mdi:download-network",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.BYTES_PER_SECOND,
        suggested_unit_of_measurement=UnitOfDataRate.MEGABYTES_PER_SECOND,
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    IkuaiSensorDescription(
        key="v6_conn",
        translation_key="wan_v6_conn",
        icon="mdi:ip-network",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="个",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

DISK_SENSORS: Final = (
    IkuaiSensorDescription(
        key="total",
        translation_key="disk_total",
        icon="mdi:harddisk",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    IkuaiSensorDescription(
        key="used",
        translation_key="disk_used",
        icon="mdi:database",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    IkuaiSensorDescription(
        key="usage",
        translation_key="disk_usage",
        icon="mdi:chart-arc",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
)

CLIENT_SENSORS: Final = (
    IkuaiSensorDescription(
        key="download",
        translation_key="client_download",
        icon="mdi:download-network",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.BYTES_PER_SECOND,
        suggested_unit_of_measurement=UnitOfDataRate.MEGABYTES_PER_SECOND,
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    IkuaiSensorDescription(
        key="upload",
        translation_key="client_upload",
        icon="mdi:upload-network",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.BYTES_PER_SECOND,
        suggested_unit_of_measurement=UnitOfDataRate.MEGABYTES_PER_SECOND,
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: IkuaiCoordinator = entry.runtime_data
    data = coordinator.data or {}
    entities: list[SensorEntity] = []

    for desc in SYSTEM_SENSORS:
        # 软路由通常没有温度探头，没值就不创建实体，避免长期 unknown
        if desc.key == "temperature" and data.get("system", {}).get("temperature") is None:
            continue
        entities.append(IkuaiSystemSensor(coordinator, desc))

    if coordinator.wifi_supported:
        for desc in WIRELESS_SENSORS:
            entities.append(IkuaiSystemSensor(coordinator, desc))

    for desc in NETWORK_SENSORS:
        entities.append(IkuaiNetworkSensor(coordinator, desc))

    for desc in MAINTENANCE_SENSORS:
        entities.append(IkuaiMaintenanceSensor(coordinator, desc))

    # 接口相关
    interfaces = data.get("interfaces", {})
    wan_names = sorted(n for n, v in interfaces.items() if v.get("is_wan"))
    first_wan = wan_names[0] if wan_names else None
    lan_names = sorted(n for n in interfaces if n.startswith("lan"))
    default_on = {n for n in (first_wan, lan_names[0] if lan_names else None) if n}

    for name, iface in interfaces.items():
        for desc in INTERFACE_SENSORS:
            enabled = name in default_on
            entities.append(IkuaiInterfaceSensor(coordinator, desc, name, enabled))
        if iface.get("is_wan") and iface.get("has_v6"):
            for desc in WAN_V6_SENSORS:
                entities.append(IkuaiInterfaceSensor(coordinator, desc, name, name == first_wan))

    for disk_id, disk in data.get("disks", {}).items():
        for desc in DISK_SENSORS:
            entities.append(IkuaiDiskSensor(coordinator, desc, disk_id, disk.get("model", disk_id)))

    # 每终端速率（可选）
    if entry.options.get(CONF_ENABLE_CLIENTS):
        for mac in entry.options.get(CONF_TRACKERS) or {}:
            mac = normalize_mac(mac)
            if not mac:
                continue
            for desc in CLIENT_SENSORS:
                entities.append(IkuaiClientSensor(coordinator, desc, mac))

    async_add_entities(entities)


class IkuaiSystemSensor(IkuaiEntity, SensorEntity):
    """主设备传感器。"""

    entity_description: IkuaiSensorDescription

    def __init__(self, coordinator: IkuaiCoordinator, desc: IkuaiSensorDescription) -> None:
        super().__init__(coordinator, main_device(coordinator), desc.key)
        self.entity_description = desc

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if not self.entity_description.attr_fn:
            return None
        return self.entity_description.attr_fn(self.coordinator.data)


class IkuaiNetworkSensor(IkuaiEntity, SensorEntity):
    """网络与终端子设备传感器。"""

    entity_description: IkuaiSensorDescription

    def __init__(self, coordinator: IkuaiCoordinator, desc: IkuaiSensorDescription) -> None:
        super().__init__(coordinator, network_device(coordinator), f"net_{desc.key}")
        self.entity_description = desc

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if not self.entity_description.attr_fn:
            return None
        return self.entity_description.attr_fn(self.coordinator.data)


class IkuaiMaintenanceSensor(IkuaiEntity, SensorEntity):
    """系统维护子设备传感器。"""

    entity_description: IkuaiSensorDescription

    def __init__(self, coordinator: IkuaiCoordinator, desc: IkuaiSensorDescription) -> None:
        super().__init__(coordinator, maintenance_device(coordinator), f"maint_{desc.key}")
        self.entity_description = desc

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if not self.entity_description.attr_fn:
            return None
        return self.entity_description.attr_fn(self.coordinator.data)


class IkuaiInterfaceSensor(IkuaiEntity, SensorEntity):
    """按网口生成的传感器。"""

    entity_description: IkuaiSensorDescription

    def __init__(
        self,
        coordinator: IkuaiCoordinator,
        desc: IkuaiSensorDescription,
        iface_name: str,
        enabled_default: bool,
    ) -> None:
        super().__init__(coordinator, interfaces_device(coordinator), f"iface_{iface_name}_{desc.key}")
        self.entity_description = desc
        self._iface_name = iface_name
        self._attr_translation_placeholders = {"iface": iface_name}
        self._attr_entity_registry_enabled_default = enabled_default

    @property
    def _iface(self) -> dict[str, Any]:
        return (self.coordinator.data or {}).get("interfaces", {}).get(self._iface_name, {})

    @property
    def native_value(self) -> Any:
        if self.entity_description.key == "conn":
            return self._iface.get("connect_num")
        return self._iface.get(self.entity_description.key)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        iface = self._iface
        if self.entity_description.key == "upload":
            return {
                "ip": iface.get("ip"),
                "protocol": iface.get("protocol"),
                "connected": iface.get("connected"),
                "dropped_up": iface.get("updropped"),
            }
        if self.entity_description.key == "download":
            return {"dropped_down": iface.get("downdropped")}
        return None


class IkuaiDiskSensor(IkuaiEntity, SensorEntity):
    """磁盘传感器。"""

    entity_description: IkuaiSensorDescription

    def __init__(
        self,
        coordinator: IkuaiCoordinator,
        desc: IkuaiSensorDescription,
        disk_id: str,
        model: str,
    ) -> None:
        super().__init__(
            coordinator, disk_device(coordinator, disk_id, model), f"disk_{disk_id}_{desc.key}"
        )
        self.entity_description = desc
        self._disk_id = disk_id

    @property
    def _disk(self) -> dict[str, Any]:
        return (self.coordinator.data or {}).get("disks", {}).get(self._disk_id, {})

    @property
    def native_value(self) -> Any:
        return self._disk.get(self.entity_description.key)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.key != "usage":
            return None
        disk = self._disk
        return {"partitions": disk.get("partitions", []), "system_disk": disk.get("system")}


class IkuaiClientSensor(IkuaiEntity, SensorEntity):
    """单个终端的实时速率。"""

    _attr_has_entity_name = False
    entity_description: IkuaiSensorDescription

    def __init__(
        self, coordinator: IkuaiCoordinator, desc: IkuaiSensorDescription, mac: str
    ) -> None:
        super().__init__(coordinator, main_device(coordinator), f"client_{mac}_{desc.key}")
        self.entity_description = desc
        self._mac = mac

    @property
    def name(self) -> str | None:
        client = (self.coordinator.data or {}).get("clients", {}).get(self._mac, {})
        label = client.get("name") or self._mac
        suffix = "下载" if self.entity_description.key == "download" else "上传"
        return f"{label} {suffix}"

    @property
    def _client(self) -> dict[str, Any]:
        return (self.coordinator.data or {}).get("clients", {}).get(self._mac, {})

    @property
    def available(self) -> bool:
        return super().available and bool(self._client)

    @property
    def native_value(self) -> Any:
        return self._client.get(self.entity_description.key, 0)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        client = self._client
        return {
            "mac": self._mac,
            "ip": client.get("ip"),
            "vendor": client.get("vendor"),
            "interface": client.get("interface"),
            "online_since": client.get("online_since"),
            "total_up_mb": round(client.get("total_up", 0) / 1024 / 1024, 2),
            "total_down_mb": round(client.get("total_down", 0) / 1024 / 1024, 2),
        }
