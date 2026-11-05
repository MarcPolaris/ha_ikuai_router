"""iKuai Router 诊断信息导出。"""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_LEGACY_PASSWORD, CONF_TOKEN, DOMAIN
from .coordinator import IkuaiCoordinator


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """导出诊断数据（敏感字段脱敏）。"""
    coordinator: IkuaiCoordinator = entry.runtime_data

    def scrub(data: dict[str, Any]) -> dict[str, Any]:
        result = dict(data)
        if CONF_TOKEN in result:
            result[CONF_TOKEN] = "***"
        if CONF_LEGACY_PASSWORD in result:
            result[CONF_LEGACY_PASSWORD] = "***"
        return result

    data = coordinator.data or {}
    return {
        "entry": {
            "data": scrub(dict(entry.data)),
            "options": scrub(dict(entry.options)),
        },
        "router": {
            "base_url": coordinator.api.base_url,
            "gwid": coordinator.gwid,
            "hostname": coordinator.hostname,
            "firmware": coordinator.sw_version,
            "arch": coordinator.hw_version,
            "wifi_supported": coordinator.wifi_supported,
            "legacy_enabled": coordinator.legacy is not None,
            "legacy_available": bool(coordinator.legacy and coordinator.legacy.available),
        },
        "summary": {
            "system": data.get("system"),
            "wan": data.get("wan"),
            "interfaces": data.get("interfaces"),
            "dhcp_total": data.get("dhcp", {}).get("total"),
            "mac_rules": list(data.get("security", {}).get("rules", {})),
            "clients_online": len(data.get("clients_online", {})),
            "disks": data.get("disks"),
            "maintenance": data.get("maintenance"),
            "legacy": data.get("legacy"),
        },
        "domain": DOMAIN,
    }
