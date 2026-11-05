"""iKuai Router 按钮平台。"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import IkuaiPermissionError
from .const import LOGGER
from .coordinator import IkuaiCoordinator
from .entity import IkuaiEntity, main_device, maintenance_device


@dataclass(frozen=True, kw_only=True)
class IkuaiButtonDescription(ButtonEntityDescription):
    """按钮描述。"""

    action: str
    needs_legacy: bool = False
    notify: str = ""


BUTTONS: Final = (
    IkuaiButtonDescription(
        key="reboot",
        translation_key="reboot",
        icon="mdi:restart",
        device_class=ButtonDeviceClass.RESTART,
        entity_category=EntityCategory.CONFIG,
        action="reboot",
        notify="已提交重启请求，路由器将在约 1 分钟后重启。",
    ),
    IkuaiButtonDescription(
        key="reconnect_wan",
        translation_key="reconnect_wan",
        icon="mdi:wan",
        entity_category=EntityCategory.CONFIG,
        action="reconnect_wan",
        needs_legacy=True,
        notify="已向 WAN1 发起 PPPoE 重拨。",
    ),
    IkuaiButtonDescription(
        key="check_upgrade",
        translation_key="check_upgrade",
        icon="mdi:cloud-search",
        action="check_upgrade",
        notify="已开始检测固件更新。",
    ),
    IkuaiButtonDescription(
        key="backup",
        translation_key="backup",
        icon="mdi:database-export",
        action="backup",
        notify="已开始备份配置。",
    ),
    IkuaiButtonDescription(
        key="start_upgrade",
        translation_key="start_upgrade",
        icon="mdi:cloud-download",
        entity_category=EntityCategory.CONFIG,
        action="start_upgrade",
        notify="已开始固件升级，请勿断电。",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: IkuaiCoordinator = entry.runtime_data
    entities = []
    for desc in BUTTONS:
        if desc.needs_legacy and coordinator.legacy is None:
            continue
        entities.append(IkuaiButton(coordinator, desc))
    async_add_entities(entities)


class IkuaiButton(IkuaiEntity, ButtonEntity):
    """执行一次性动作。"""

    entity_description: IkuaiButtonDescription

    def __init__(self, coordinator: IkuaiCoordinator, desc: IkuaiButtonDescription) -> None:
        device = main_device(coordinator) if desc.key == "reboot" else maintenance_device(coordinator)
        super().__init__(coordinator, device, f"btn_{desc.key}")
        self.entity_description = desc

    async def async_press(self) -> None:
        action = self.entity_description.action
        api = self.coordinator.api
        try:
            if action == "reboot":
                await self._reboot()
            elif action == "reconnect_wan":
                if self.coordinator.legacy:
                    await self.coordinator.legacy.reconnect_wan(1)
            elif action == "check_upgrade":
                await api.check_upgrade()
            elif action == "backup":
                await api.trigger_backup()
            elif action == "start_upgrade":
                await api.start_upgrade()
        except IkuaiPermissionError as err:
            # 令牌是有效的，只是路由器那边没给这个账号开权限，别当成"令牌失效"吓人
            LOGGER.warning("iKuai 按钮 %s 被路由器拒绝（权限不足）: %s", action, err)
            if action == "check_upgrade":
                # 芯片不给触发云端检测，就把路由器已知的固件信息刷一遍，
                # "固件状态 / 最新版本" 传感器照样能反映结果
                await self.coordinator.async_request_refresh()
                return
            if action in ("backup", "start_upgrade"):
                self.hass.async_create_task(self._delayed_refresh())
            raise HomeAssistantError(
                "该操作被路由器拒绝：当前令牌没有这项权限。"
                "请到路由器「系统设置 → 账户设置」给对应账号放开权限。"
            ) from err
        except Exception as err:  # noqa: BLE001
            LOGGER.error("iKuai 按钮 %s 执行失败: %s", action, err)
            raise HomeAssistantError(f"操作失败：{err}") from err

        if self.entity_description.notify:
            await self._notify(self.entity_description.notify)

        if action in ("check_upgrade", "backup", "start_upgrade"):
            self.hass.async_create_task(self._delayed_refresh())

    async def _notify(self, message: str) -> None:
        """尽量弹一个持久通知；部分环境未启用该功能时静默跳过。"""
        try:
            await self.hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": "iKuai Router",
                    "message": message,
                    "notification_id": f"ikuai_router_{self.entity_description.action}",
                },
                blocking=False,
            )
        except Exception:  # noqa: BLE001
            LOGGER.debug("持久通知不可用，已跳过: %s", message)

    async def _reboot(self) -> None:
        """用一次性重启计划实现"立刻重启"（OpenAPI 无直接重启接口）。"""
        api = self.coordinator.api
        now = datetime.now(ZoneInfo("Asia/Shanghai")) + timedelta(minutes=1)
        await api.add_reboot_schedule(
            {
                "enabled": "yes",
                "event": "reboot",
                "strategy": "one",
                "cycle_time": now.strftime("%Y-%m-%d"),
                "time": now.strftime("%H:%M"),
                "tagname": "HA_Reboot",
                "comment": "由 Home Assistant 触发的重启",
            }
        )

    async def _delayed_refresh(self) -> None:
        await asyncio.sleep(3)
        await self.coordinator.async_request_refresh()
