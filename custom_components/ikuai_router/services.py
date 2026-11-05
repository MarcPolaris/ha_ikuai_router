"""iKuai Router 集成服务。"""
from __future__ import annotations

import time
import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, LOGGER, MODE_BLACKLIST
from .coordinator import IkuaiCoordinator, normalize_mac

BLOCK_COMMENT = "由 Home Assistant 临时封禁"
BLOCK_TAG_PREFIX = "HA_Block_"

SERVICES: tuple[str, ...] = (
    "get_traffic_ranking",
    "get_protocol_stats",
    "get_offline_history",
    "add_mac_rule",
    "delete_mac_rule",
    "block_client",
    "unblock_client",
    "call_api",
)


def _coordinator(hass: HomeAssistant, device_id: str | None = None) -> IkuaiCoordinator:
    entries = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if getattr(entry, "runtime_data", None) is not None
    ]
    if not entries:
        raise ServiceValidationError("没有已加载的 iKuai Router 实例")

    if device_id:
        device = dr.async_get(hass).async_get(device_id)
        if device:
            for entry in entries:
                if entry.entry_id in device.config_entries:
                    return entry.runtime_data
        raise ServiceValidationError(f"找不到设备 {device_id} 对应的路由器")

    if len(entries) == 1:
        return entries[0].runtime_data
    raise ServiceValidationError("存在多个 iKuai 实例，请通过 device_id 指定目标路由器")


async def async_setup_services(hass: HomeAssistant) -> None:
    """注册服务（全局只注册一次）。"""
    if hass.services.has_service(DOMAIN, "call_api"):
        return

    async def get_traffic_ranking(call: ServiceCall) -> ServiceResponse:
        coordinator = _coordinator(hass, call.data.get("device_id"))
        res = await coordinator.api.get_traffic_summary()
        devices = [
            {
                "name": item.get("comment") or item.get("username") or item.get("mac"),
                "mac": normalize_mac(item.get("mac")),
                "ip": item.get("ip_addr"),
                "total_mb": round(int(item.get("sum_total") or 0) / 1024 / 1024, 2),
                "up_mb": round(int(item.get("sum_total_up") or 0) / 1024 / 1024, 2),
                "down_mb": round(int(item.get("sum_total_down") or 0) / 1024 / 1024, 2),
            }
            for item in res.get("terminal", [])
            if isinstance(item, dict)
        ]
        devices.sort(key=lambda x: x["total_mb"], reverse=True)
        limit = int(call.data.get("limit") or 100)
        return {
            "total_mb": round(int(res.get("terminal_total_flow") or 0) / 1024 / 1024, 2),
            "client_count": res.get("terminal_total"),
            "devices": devices[:limit],
        }

    async def get_protocol_stats(call: ServiceCall) -> ServiceResponse:
        coordinator = _coordinator(hass, call.data.get("device_id"))
        mac = normalize_mac(call.data["mac"])
        ip = call.data.get("ip") or (coordinator.client_by_mac(mac) or {}).get("ip")
        if not ip:
            raise ServiceValidationError("无法确定该终端的 IP，请手动传入 ip")
        res = await coordinator.api.get_protocol_stats(mac, ip)
        protocols = [
            {
                "name": item.get("proto_name"),
                "total_mb": round(int(item.get("total") or 0) / 1024 / 1024, 2),
            }
            for item in res.get("data", [])
            if isinstance(item, dict) and int(item.get("total") or 0) > 0
        ]
        protocols.sort(key=lambda x: x["total_mb"], reverse=True)
        return {"mac": mac, "ip": ip, "protocols": protocols}

    async def get_offline_history(call: ServiceCall) -> ServiceResponse:
        coordinator = _coordinator(hass, call.data.get("device_id"))
        res = await coordinator.api.get_offline_clients(call.data.get("limit", 20))
        history = []
        for item in res.get("offline_data", []):
            if not isinstance(item, dict):
                continue
            history.append(
                {
                    "name": item.get("termname") or item.get("client_model") or item.get("mac"),
                    "mac": normalize_mac(item.get("mac")),
                    "ip": item.get("ip_addr"),
                    "logout_time": item.get("logout_time"),
                    "total_mb": round(
                        (int(item.get("total_up") or 0) + int(item.get("total_down") or 0))
                        / 1024
                        / 1024,
                        2,
                    ),
                    "vendor": item.get("client_vendor"),
                }
            )
        return {"history": history}

    async def add_mac_rule(call: ServiceCall) -> None:
        coordinator = _coordinator(hass, call.data.get("device_id"))
        mac = normalize_mac(call.data["mac"])
        payload = {
            "mac": mac,
            "enabled": "yes",
            "tagname": call.data.get("tagname") or f"HA_{mac[-5:]}",
            "comment": call.data.get("comment") or "由 Home Assistant 添加",
            "expires": call.data.get("expires", 0),
            "strategy": "day",
            "cycle_time": "all",
            "time": "00:00-23:59",
        }
        await coordinator.api.add_mac_rule(payload)
        await coordinator.async_request_refresh()

    async def delete_mac_rule(call: ServiceCall) -> None:
        coordinator = _coordinator(hass, call.data.get("device_id"))
        await coordinator.api.delete_mac_rule(int(call.data["rule_id"]))
        await coordinator.async_request_refresh()

    async def block_client(call: ServiceCall) -> None:
        coordinator = _coordinator(hass, call.data.get("device_id"))
        security = (coordinator.data or {}).get("security", {})
        if security.get("mode") != MODE_BLACKLIST:
            raise ServiceValidationError(
                "当前路由器为白名单模式，临时封禁会在白名单下变成放行。"
                "请先在「安全中心 - 全局访问控制模式」切换到黑名单。"
            )

        mac = normalize_mac(call.data["mac"])
        minutes = int(call.data.get("minutes", 0))
        expires = int(time.time()) + minutes * 60 if minutes > 0 else 0
        client = coordinator.client_by_mac(mac)
        await coordinator.api.add_mac_rule(
            {
                "mac": mac,
                "enabled": "yes",
                "tagname": f"{BLOCK_TAG_PREFIX}{mac[-5:].upper()}",
                "comment": BLOCK_COMMENT,
                "expires": expires,
                "strategy": "day",
                "cycle_time": "all",
                "time": "00:00-23:59",
            }
        )
        LOGGER.info("已临时封禁 %s（%s 分钟）", client.get("name", mac), minutes or "永久")
        await coordinator.async_request_refresh()

    async def unblock_client(call: ServiceCall) -> None:
        coordinator = _coordinator(hass, call.data.get("device_id"))
        mac = normalize_mac(call.data["mac"])
        rules = (coordinator.data or {}).get("security", {}).get("rules", {})
        removed = 0
        for rule_id, rule in rules.items():
            if normalize_mac(rule.get("mac")) != mac:
                continue
            if not str(rule.get("tagname") or "").startswith(BLOCK_TAG_PREFIX):
                continue
            await coordinator.api.delete_mac_rule(int(rule_id))
            removed += 1
        if not removed:
            raise ServiceValidationError(f"{mac} 没有由本集成添加的封禁规则")
        await coordinator.async_request_refresh()

    async def call_api(call: ServiceCall) -> ServiceResponse:
        coordinator = _coordinator(hass, call.data.get("device_id"))
        return await coordinator.api.async_call(
            call.data.get("method", "GET"),
            call.data["path"],
            params=call.data.get("params"),
            timeout=float(call.data.get("timeout", 15)),
            raw=bool(call.data.get("return_raw", False)),
        )

    device_field = {vol.Optional("device_id"): cv.string}

    hass.services.async_register(
        DOMAIN,
        "get_traffic_ranking",
        get_traffic_ranking,
        schema=vol.Schema(
            {**device_field, vol.Optional("limit", default=100): vol.All(int, vol.Range(min=1, max=500))}
        ),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        "get_protocol_stats",
        get_protocol_stats,
        schema=vol.Schema(
            {**device_field, vol.Required("mac"): cv.string, vol.Optional("ip"): cv.string}
        ),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        "get_offline_history",
        get_offline_history,
        schema=vol.Schema(
            {**device_field, vol.Optional("limit", default=20): vol.All(int, vol.Range(min=1, max=200))}
        ),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        "add_mac_rule",
        add_mac_rule,
        schema=vol.Schema(
            {
                **device_field,
                vol.Required("mac"): cv.string,
                vol.Optional("tagname"): cv.string,
                vol.Optional("comment"): cv.string,
                vol.Optional("expires", default=0): vol.All(int, vol.Range(min=0)),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        "delete_mac_rule",
        delete_mac_rule,
        schema=vol.Schema({**device_field, vol.Required("rule_id"): cv.positive_int}),
    )
    hass.services.async_register(
        DOMAIN,
        "block_client",
        block_client,
        schema=vol.Schema(
            {
                **device_field,
                vol.Required("mac"): cv.string,
                vol.Optional("minutes", default=0): vol.All(int, vol.Range(min=0, max=10080)),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        "unblock_client",
        unblock_client,
        schema=vol.Schema({**device_field, vol.Required("mac"): cv.string}),
    )
    hass.services.async_register(
        DOMAIN,
        "call_api",
        call_api,
        schema=vol.Schema(
            {
                **device_field,
                vol.Required("path"): cv.string,
                vol.Optional("method", default="GET"): vol.In(
                    ["GET", "POST", "PUT", "PATCH", "DELETE"]
                ),
                vol.Optional("params"): vol.Any(None, cv.string, dict, list),
                vol.Optional("timeout", default=15): vol.All(vol.Coerce(float), vol.Range(min=1, max=120)),
                vol.Optional("return_raw", default=False): cv.boolean,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )


async def async_unload_services(hass: HomeAssistant) -> None:
    for service in SERVICES:
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)
