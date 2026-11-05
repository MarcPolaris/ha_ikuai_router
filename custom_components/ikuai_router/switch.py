"""iKuai Router 开关平台：MAC 规则、ARP 绑定校验、智能流控。"""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import LOGGER
from .coordinator import IkuaiCoordinator
from .entity import IkuaiEntity, security_device


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: IkuaiCoordinator = entry.runtime_data
    added: set[str] = set()

    # -------------------------------------------------- 传统接口开关（可选）
    if coordinator.legacy is not None:
        async_add_entities(
            [
                IkuaiLegacySwitch(coordinator, "arp_filter"),
                IkuaiLegacySwitch(coordinator, "stream_control"),
            ]
        )

    # -------------------------------------------------- MAC 规则动态开关
    @callback
    def _sync_rules() -> None:
        rules = (coordinator.data or {}).get("security", {}).get("rules", {})
        valid = {f"{coordinator.gwid}_macrule_{rid}" for rid in rules}

        new_entities = []
        for rule_id in rules:
            uid = f"{coordinator.gwid}_macrule_{rule_id}"
            if uid not in added:
                new_entities.append(IkuaiMacRuleSwitch(coordinator, rule_id, uid))
                added.add(uid)
        if new_entities:
            async_add_entities(new_entities)

        registry = er.async_get(hass)
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
            if entity.domain != "switch" or "_macrule_" not in (entity.unique_id or ""):
                continue
            if entity.unique_id not in valid:
                LOGGER.debug("移除已删除的 MAC 规则实体 %s", entity.entity_id)
                registry.async_remove(entity.entity_id)
                added.discard(entity.unique_id)

    _sync_rules()
    entry.async_on_unload(coordinator.async_add_listener(_sync_rules))


class IkuaiMacRuleSwitch(IkuaiEntity, SwitchEntity):
    """一条 MAC 访问控制规则的启用开关。"""

    _attr_translation_key = "mac_rule"

    def __init__(self, coordinator: IkuaiCoordinator, rule_id: str, uid: str) -> None:
        super().__init__(coordinator, security_device(coordinator), f"macrule_{rule_id}")
        self._attr_unique_id = uid
        self._rule_id = str(rule_id)

    @property
    def _rule(self) -> dict[str, Any]:
        return (self.coordinator.data or {}).get("security", {}).get("rules", {}).get(self._rule_id, {})

    @property
    def name(self) -> str | None:
        rule = self._rule
        return rule.get("tagname") or rule.get("comment") or f"MAC 规则 {self._rule_id}"

    @property
    def is_on(self) -> bool:
        return self._rule.get("enabled") == "yes"

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.api.update_mac_rule(int(self._rule_id), {"enabled": "yes"})
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.api.update_mac_rule(int(self._rule_id), {"enabled": "no"})
        await self.coordinator.async_request_refresh()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        rule = self._rule
        if not rule:
            return {}
        schedule = []
        for item in (rule.get("time") or {}).get("custom", []) or []:
            if item.get("type") == "weekly":
                schedule.append(f"每周 {item.get('weekdays')}")
            else:
                schedule.append(f"{item.get('start_time')}-{item.get('end_time')}")
        return {
            "mac": rule.get("mac"),
            "terminal": rule.get("termname"),
            "comment": rule.get("comment"),
            "schedule": "; ".join(schedule) or "全天",
            "expires": "永久" if not rule.get("expires") else rule.get("expires"),
            "rule_id": self._rule_id,
            "mode": (self.coordinator.data or {}).get("security", {}).get("mode_label"),
        }


class IkuaiLegacySwitch(IkuaiEntity, SwitchEntity):
    """需要账号密码的传统接口开关。"""

    _attr_device_class = SwitchDeviceClass.SWITCH

    _META = {
        "arp_filter": {"icon": "mdi:account-lock", "name": "非绑定 MAC 禁止上网"},
        "stream_control": {"icon": "mdi:network-outline", "name": "智能流控"},
    }

    def __init__(self, coordinator: IkuaiCoordinator, key: str) -> None:
        super().__init__(coordinator, security_device(coordinator), f"legacy_{key}")
        self._key = key
        self._attr_has_entity_name = False
        self._attr_name = self._META[key]["name"]
        self._attr_icon = self._META[key]["icon"]

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.legacy is not None
            and self.coordinator.legacy.available
        )

    @property
    def is_on(self) -> bool | None:
        return (self.coordinator.data or {}).get("legacy", {}).get(self._key)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)

    async def _set(self, value: bool) -> None:
        legacy = self.coordinator.legacy
        if legacy is None:
            return
        if self._key == "arp_filter":
            await legacy.set_arp_filter(value)
            state = await legacy.get_arp_options()
            new_value = state.get("arp_filter") == 1
        else:
            await legacy.set_stream_control(value)
            state = await legacy.get_stream_control()
            new_value = state.get("stream_ctl_mode") == 1

        if isinstance(self.coordinator.data, dict):
            self.coordinator.data.setdefault("legacy", {})[self._key] = new_value
        self.async_write_ha_state()
