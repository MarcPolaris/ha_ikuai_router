"""实体基类与设备信息。"""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import IkuaiCoordinator


class IkuaiEntity(CoordinatorEntity[IkuaiCoordinator]):
    """所有实体的公共基类。"""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: IkuaiCoordinator,
        device_info: DeviceInfo,
        unique_key: str,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.gwid}_{unique_key}"
        self._attr_device_info = device_info


def main_device(coordinator: IkuaiCoordinator) -> DeviceInfo:
    title = coordinator.entry.title or coordinator.hostname
    return DeviceInfo(
        identifiers={(DOMAIN, coordinator.gwid)},
        name=title,
        manufacturer="iKuai",
        model="iKuai Router",
        sw_version=coordinator.sw_version,
        hw_version=coordinator.hw_version,
        configuration_url=coordinator.api.base_url,
    )


def interfaces_device(coordinator: IkuaiCoordinator) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, f"{coordinator.gwid}_interfaces")},
        name="网络接口",
        manufacturer="iKuai",
        model="Interface Monitor",
        via_device_id=coordinator.hub_device_id,
    )


def security_device(coordinator: IkuaiCoordinator) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, f"{coordinator.gwid}_security")},
        name="安全中心",
        manufacturer="iKuai",
        model="Access Control",
        via_device_id=coordinator.hub_device_id,
    )


def maintenance_device(coordinator: IkuaiCoordinator) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, f"{coordinator.gwid}_maintenance")},
        name="系统维护",
        manufacturer="iKuai",
        model="Maintenance",
        via_device_id=coordinator.hub_device_id,
    )


def network_device(coordinator: IkuaiCoordinator) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, f"{coordinator.gwid}_network")},
        name="网络与终端",
        manufacturer="iKuai",
        model="Network Services",
        via_device_id=coordinator.hub_device_id,
    )


def disk_device(coordinator: IkuaiCoordinator, disk_id: str, model: str) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, f"{coordinator.gwid}_disk_{disk_id}")},
        name=f"存储 {model}",
        manufacturer="iKuai",
        model=model,
        via_device_id=coordinator.hub_device_id,
    )
