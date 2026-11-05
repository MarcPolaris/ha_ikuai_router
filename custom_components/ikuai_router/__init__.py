"""iKuai Router 集成入口。"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    IkuaiApi,
    IkuaiAuthError,
    IkuaiConnectionError,
    async_detect_base_url,
)
from .const import (
    CONF_BASE_URL,
    CONF_ENABLE_LEGACY,
    CONF_LEGACY_PASSWORD,
    CONF_LEGACY_USERNAME,
    CONF_TOKEN,
    CONF_VERIFY_SSL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LOGGER,
    PLATFORMS,
)
from .coordinator import IkuaiCoordinator
from .legacy import IkuaiLegacyClient
from .services import async_setup_services, async_unload_services

IkuaiConfigEntry = ConfigEntry[IkuaiCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: IkuaiConfigEntry) -> bool:
    """建立 API / 协调器并转发各平台。"""
    host = entry.data[CONF_HOST]
    token = entry.data[CONF_TOKEN]
    verify_ssl = bool(entry.data.get(CONF_VERIFY_SSL, False))
    session = async_get_clientsession(hass, verify_ssl=verify_ssl)

    base_url = entry.data.get(CONF_BASE_URL)
    if not base_url:
        try:
            base_url, _info, _headers = await async_detect_base_url(session, host, token, verify_ssl)
        except IkuaiAuthError as err:
            raise ConfigEntryNotReady(f"令牌无效: {err}") from err
        except IkuaiConnectionError as err:
            raise ConfigEntryNotReady(f"无法连接路由器: {err}") from err

    api = IkuaiApi(session, base_url, token, verify_ssl)

    legacy: IkuaiLegacyClient | None = None
    if entry.options.get(CONF_ENABLE_LEGACY):
        username = entry.options.get(CONF_LEGACY_USERNAME)
        password = entry.options.get(CONF_LEGACY_PASSWORD)
        if username and password:
            legacy = IkuaiLegacyClient(session, base_url, username, password, verify_ssl)
            if not await legacy.async_login():
                LOGGER.warning("iKuai 传统接口登录失败，相关实体将不可用")
                legacy = None

    scan_interval = int(
        entry.options.get(CONF_SCAN_INTERVAL)
        or entry.data.get(CONF_SCAN_INTERVAL)
        or DEFAULT_SCAN_INTERVAL
    )

    coordinator = IkuaiCoordinator(hass, entry, api, legacy, scan_interval)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    # 先落地主设备：子设备需要用 via_device_id 关联（via_device 已弃用）
    device_registry = dr.async_get(hass)
    hub_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, coordinator.gwid)},
        name=entry.title or coordinator.hostname,
        manufacturer="iKuai",
        model="iKuai Router",
        sw_version=coordinator.sw_version,
        hw_version=coordinator.hw_version,
        configuration_url=api.base_url,
    )
    coordinator.hub_device_id = hub_device.id

    await async_setup_services(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: IkuaiConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: IkuaiConfigEntry) -> bool:
    """卸载平台，并在没有其他实例时注销服务。"""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        remaining = [
            other
            for other in hass.config_entries.async_entries(DOMAIN)
            if other.entry_id != entry.entry_id and getattr(other, "runtime_data", None) is not None
        ]
        if not remaining:
            await async_unload_services(hass)
    return unloaded
