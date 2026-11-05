"""iKuai Router 配置流程。"""
from __future__ import annotations

import re
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_SCAN_INTERVAL
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    IkuaiApiError,
    IkuaiAuthError,
    IkuaiConnectionError,
    async_detect_base_url,
)
from .const import (
    CONF_BASE_URL,
    CONF_CPU_THRESHOLD,
    CONF_DISK_THRESHOLD,
    CONF_ENABLE_CLIENTS,
    CONF_ENABLE_LEGACY,
    CONF_LEGACY_PASSWORD,
    CONF_LEGACY_USERNAME,
    CONF_MEM_THRESHOLD,
    CONF_OFFLINE_GRACE,
    CONF_TOKEN,
    CONF_TRACKERS,
    CONF_TRACKERS_EXTRA,
    CONF_VERIFY_SSL,
    DEFAULT_CPU_THRESHOLD,
    DEFAULT_DISK_THRESHOLD,
    DEFAULT_MEM_THRESHOLD,
    DEFAULT_OFFLINE_GRACE,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LOGGER,
    MIN_SCAN_INTERVAL,
)
from .coordinator import IkuaiCoordinator, normalize_mac
from .legacy import IkuaiLegacyClient

TOKEN_HELP = "路由器后台 → 系统设置 → 爱快云/API → 新建令牌"
MAC_RE = re.compile(r"^[0-9a-f]{2}([:-][0-9a-f]{2}){5}$")


class IkuaiRouterConfigFlow(ConfigFlow, domain=DOMAIN):
    """添加路由器。"""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            token = user_input[CONF_TOKEN].strip()
            verify_ssl = bool(user_input.get(CONF_VERIFY_SSL, False))
            session = async_get_clientsession(self.hass, verify_ssl=verify_ssl)

            try:
                base_url, info, _headers = await async_detect_base_url(
                    session, host, token, verify_ssl
                )
            except IkuaiAuthError:
                errors["base"] = "invalid_auth"
            except IkuaiConnectionError:
                errors["base"] = "cannot_connect"
            except IkuaiApiError:
                errors["base"] = "unsupported_firmware"
            except Exception:  # noqa: BLE001
                LOGGER.exception("iKuai 配置校验异常")
                errors["base"] = "unknown"
            else:
                sysinfo = info.get("sysinfo", {}) if isinstance(info, dict) else {}
                gwid = str(sysinfo.get("gwid") or base_url)
                await self.async_set_unique_id(gwid)
                self._abort_if_unique_id_configured()

                title = sysinfo.get("hostname") or host
                version = (sysinfo.get("verinfo") or {}).get("version")
                if version:
                    title = f"{title} ({version})"

                return self.async_create_entry(
                    title=title,
                    data={
                        CONF_HOST: host,
                        CONF_TOKEN: token,
                        CONF_BASE_URL: base_url,
                        CONF_VERIFY_SSL: verify_ssl,
                        CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default="192.168.2.1"): str,
                    vol.Required(CONF_TOKEN): selector.TextSelector(
                        selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                    ),
                    vol.Optional(CONF_VERIFY_SSL, default=False): bool,
                }
            ),
            errors=errors,
            description_placeholders={"token_help": TOKEN_HELP},
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        errors: dict[str, str] = {}
        assert entry is not None

        if user_input is not None:
            token = user_input[CONF_TOKEN].strip()
            session = async_get_clientsession(self.hass)
            try:
                base_url, _info, _headers = await async_detect_base_url(
                    session, entry.data[CONF_HOST], token
                )
            except IkuaiAuthError:
                errors["base"] = "invalid_auth"
            except (IkuaiConnectionError, IkuaiApiError):
                errors["base"] = "cannot_connect"
            else:
                self.hass.config_entries.async_update_entry(
                    entry,
                    data={**entry.data, CONF_TOKEN: token, CONF_BASE_URL: base_url},
                )
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_TOKEN): selector.TextSelector(
                        selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                    )
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return IkuaiOptionsFlow()


class IkuaiOptionsFlow(OptionsFlow):
    """集成选项。"""

    async def _resolve_extra_trackers(
        self, coordinator: IkuaiCoordinator | None, raw: str
    ) -> dict[str, str]:
        """把手填的「设备名 / MAC」解析成 ``{mac: 名称}``。

        路由器上没在线的终端（例如关机的手机）没法出现在下拉框里，
        这里允许直接写设备名或 MAC，靠终端上线历史翻译成 MAC。
        """
        tokens = [t for t in re.split(r"[,，;；\s]+", raw or "") if t.strip()]
        if not tokens:
            return {}

        directory: dict[str, str] = {}
        if coordinator is not None:
            directory = await coordinator.api.get_terminal_directory()
            for mac, client in (coordinator.data or {}).get("clients", {}).items():
                name = (client or {}).get("name")
                if name:
                    directory.setdefault(mac, name)
        by_name = {name: mac for mac, name in directory.items()}

        resolved: dict[str, str] = {}
        unknown: list[str] = []
        for token in tokens:
            token = token.strip()
            mac = normalize_mac(token)
            if MAC_RE.match(mac):
                resolved[mac] = directory.get(mac) or ""
                continue
            found = by_name.get(token) or ""
            if not found:
                for name, candidate in by_name.items():
                    if token in name:
                        found = candidate
                        break
            if found:
                resolved[found] = directory.get(found) or token
            else:
                unknown.append(token)
        if unknown:
            raise ValueError("、".join(unknown))
        return resolved

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self.config_entry
        coordinator = getattr(entry, "runtime_data", None)
        errors: dict[str, str] = {}

        online: dict[str, str] = {}
        if coordinator is not None and coordinator.data:
            for mac, client in coordinator.data.get("clients_online", {}).items():
                online[mac] = f"{client.get('name')} ({client.get('ip') or '—'})"

        current_trackers = entry.options.get(CONF_TRACKERS) or entry.data.get(CONF_TRACKERS) or {}
        options_list = [
            selector.SelectOptionDict(value=mac, label=label) for mac, label in online.items()
        ]
        # 保留已配置但当前不在线的终端，避免打开选项时被清空
        for mac, conf in current_trackers.items():
            mac = normalize_mac(mac)
            if mac not in online:
                name = conf.get("name") if isinstance(conf, dict) else None
                options_list.append(
                    selector.SelectOptionDict(value=mac, label=f"{name or mac}（离线）")
                )

        if user_input is not None:
            enable_legacy = bool(user_input.get(CONF_ENABLE_LEGACY))
            username = (user_input.get(CONF_LEGACY_USERNAME) or "").strip()
            password = user_input.get(CONF_LEGACY_PASSWORD) or ""
            if enable_legacy and username and password:
                session = async_get_clientsession(self.hass)
                client = IkuaiLegacyClient(
                    session,
                    entry.data.get(CONF_BASE_URL, ""),
                    username,
                    password,
                    bool(entry.data.get(CONF_VERIFY_SSL, False)),
                )
                if not await client.async_login():
                    errors["base"] = "legacy_auth"
            elif enable_legacy:
                errors["base"] = "legacy_required"

            if not errors:
                selected = [normalize_mac(m) for m in user_input.get(CONF_TRACKERS, [])]
                trackers = {
                    mac: ({"name": current_trackers.get(mac, {}).get("name")} if isinstance(current_trackers.get(mac), dict) else {})
                    for mac in selected
                }
                try:
                    extra = await self._resolve_extra_trackers(
                        coordinator, user_input.get(CONF_TRACKERS_EXTRA, "")
                    )
                except ValueError as err:
                    errors["base"] = "tracker_not_found"
                    LOGGER.warning("以下终端无法识别：%s", err)
                else:
                    for mac, name in extra.items():
                        trackers.setdefault(mac, {"name": name or None})
                    return self.async_create_entry(
                        data={
                            CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                            CONF_TRACKERS: trackers,
                            CONF_ENABLE_CLIENTS: bool(user_input.get(CONF_ENABLE_CLIENTS)),
                            CONF_OFFLINE_GRACE: int(user_input[CONF_OFFLINE_GRACE]),
                            CONF_CPU_THRESHOLD: int(user_input[CONF_CPU_THRESHOLD]),
                            CONF_MEM_THRESHOLD: int(user_input[CONF_MEM_THRESHOLD]),
                            CONF_DISK_THRESHOLD: int(user_input[CONF_DISK_THRESHOLD]),
                            CONF_ENABLE_LEGACY: enable_legacy,
                            CONF_LEGACY_USERNAME: username if enable_legacy else "",
                            CONF_LEGACY_PASSWORD: password if enable_legacy else "",
                        }
                    )

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=entry.options.get(
                        CONF_SCAN_INTERVAL, entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
                    ),
                ): vol.All(int, vol.Range(min=MIN_SCAN_INTERVAL, max=300)),
                vol.Optional(
                    CONF_TRACKERS, default=list(current_trackers.keys())
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=options_list,
                        multiple=True,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(CONF_TRACKERS_EXTRA, default=""): str,
                vol.Optional(
                    CONF_ENABLE_CLIENTS,
                    default=bool(entry.options.get(CONF_ENABLE_CLIENTS, False)),
                ): bool,
                vol.Optional(
                    CONF_OFFLINE_GRACE,
                    default=int(entry.options.get(CONF_OFFLINE_GRACE, DEFAULT_OFFLINE_GRACE)),
                ): vol.All(int, vol.Range(min=0, max=60)),
                vol.Optional(
                    CONF_CPU_THRESHOLD,
                    default=int(entry.options.get(CONF_CPU_THRESHOLD, DEFAULT_CPU_THRESHOLD)),
                ): vol.All(int, vol.Range(min=30, max=100)),
                vol.Optional(
                    CONF_MEM_THRESHOLD,
                    default=int(entry.options.get(CONF_MEM_THRESHOLD, DEFAULT_MEM_THRESHOLD)),
                ): vol.All(int, vol.Range(min=30, max=100)),
                vol.Optional(
                    CONF_DISK_THRESHOLD,
                    default=int(entry.options.get(CONF_DISK_THRESHOLD, DEFAULT_DISK_THRESHOLD)),
                ): vol.All(int, vol.Range(min=50, max=100)),
                vol.Optional(
                    CONF_ENABLE_LEGACY,
                    default=bool(entry.options.get(CONF_ENABLE_LEGACY, False)),
                ): bool,
                vol.Optional(
                    CONF_LEGACY_USERNAME, default=entry.options.get(CONF_LEGACY_USERNAME, "")
                ): str,
                vol.Optional(
                    CONF_LEGACY_PASSWORD, default=entry.options.get(CONF_LEGACY_PASSWORD, "")
                ): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
            description_placeholders={"count": str(len(online))},
        )
