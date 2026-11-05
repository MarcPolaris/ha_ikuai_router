"""iKuai Router 选择器平台：全局 MAC 访问控制模式。"""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import MAC_ACL_MODES, MAC_ACL_MODES_REVERSE
from .coordinator import IkuaiCoordinator
from .entity import IkuaiEntity, security_device

OPTION_LABELS = {"blacklist": "黑名单", "whitelist": "白名单"}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: IkuaiCoordinator = entry.runtime_data
    async_add_entities([IkuaiAclModeSelect(coordinator)])


class IkuaiAclModeSelect(IkuaiEntity, SelectEntity):
    """切换黑/白名单模式。"""

    _attr_translation_key = "acl_mode"
    _attr_icon = "mdi:shield-account"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: IkuaiCoordinator) -> None:
        super().__init__(coordinator, security_device(coordinator), "acl_mode")
        self._attr_options = [
            OPTION_LABELS[value] for value in MAC_ACL_MODES.values()
        ]

    @property
    def current_option(self) -> str | None:
        mode = (self.coordinator.data or {}).get("security", {}).get("mode", 0)
        return OPTION_LABELS.get(MAC_ACL_MODES.get(mode, "blacklist"))

    async def async_select_option(self, option: str) -> None:
        reverse = {label: value for value, label in OPTION_LABELS.items()}
        code = MAC_ACL_MODES_REVERSE.get(reverse.get(option, ""), 0)
        await self.coordinator.api.set_mac_mode(code)
        await self.coordinator.async_request_refresh()
