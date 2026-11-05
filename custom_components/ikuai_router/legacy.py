"""iKuai 传统接口（/Action/call）客户端。

用途：OpenAPI v4 没有覆盖到的少量控制项——ARP 绑定校验、智能流控开关、
WAN 口重连。这部分必须用账号密码登录换取 ``sess_key``，令牌认证在这里无效，
因此仅在用户于集成选项里显式开启时才启用。
"""
from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

from .const import LOGGER

REQUEST_TIMEOUT = 12


class IkuaiLegacyError(Exception):
    """传统接口异常。"""


class IkuaiLegacyAuthError(IkuaiLegacyError):
    """账号密码错误。"""


class IkuaiLegacyClient:
    """基于 sess_key Cookie 的传统接口客户端。"""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        username: str,
        password: str,
        verify_ssl: bool = False,
    ) -> None:
        self._session = session
        self._base = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._verify_ssl = verify_ssl
        self._sess_key: str | None = None
        self.available = False

    # ------------------------------------------------------------------ 登录
    async def async_login(self) -> bool:
        """登录并保存 sess_key。失败返回 False（不抛异常，避免拖垮整个集成）。"""
        body = {
            "username": self._username,
            "passwd": self._password,
            "pass": self._password,
            "remember_password": "true",
        }
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with self._session.post(
                    f"{self._base}/Action/login",
                    json=body,
                    headers={"Content-Type": "application/json;charset=UTF-8"},
                    ssl=None if self._verify_ssl else False,
                ) as resp:
                    payload = await _safe_json(resp)
                    cookies = {c.key: c.value for c in resp.cookies}
        except Exception as err:  # noqa: BLE001
            LOGGER.debug("iKuai 传统接口登录失败: %s", err)
            self.available = False
            return False

        key = cookies.get("sess_key") or payload.get("sess_key")
        if not key:
            LOGGER.warning(
                "iKuai 传统接口登录被拒绝（%s），依赖该接口的实体将不可用",
                payload.get("message", "无 sess_key"),
            )
            self.available = False
            return False

        self._sess_key = key
        self.available = True
        return True

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Cookie": f"username={self._username}; login=1; sess_key={self._sess_key}",
            "Content-Type": "application/json;charset=UTF-8",
        }

    # ------------------------------------------------------------------ 调用
    async def async_call(
        self, func_name: str, action: str, param: dict[str, Any] | None = None, *, _retry: bool = True
    ) -> dict[str, Any]:
        """调用 ``/Action/call``，返回 Data/results 数据段。"""
        if not self._sess_key and not await self.async_login():
            raise IkuaiLegacyAuthError("未登录")

        body = {"func_name": func_name, "action": action, "param": param or {}}
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with self._session.post(
                    f"{self._base}/Action/call",
                    json=body,
                    headers=self._headers,
                    ssl=None if self._verify_ssl else False,
                ) as resp:
                    payload = await _safe_json(resp)
        except Exception as err:  # noqa: BLE001
            raise IkuaiLegacyError(str(err)) from err

        if payload.get("code") == 10014 or payload.get("Result") == 10014:
            # 会话过期：重新登录后重试一次
            self._sess_key = None
            if _retry and await self.async_login():
                return await self.async_call(func_name, action, param, _retry=False)
            raise IkuaiLegacyAuthError("会话已过期")

        if payload.get("code") not in (0, None) and payload.get("Result") != 30000:
            LOGGER.debug("传统接口 %s/%s 返回 %s", func_name, action, payload.get("message"))
            return {}

        data = payload.get("results") or payload.get("Data") or {}
        return data if isinstance(data, dict) else {}

    # -------------------------------------------------------------- 具体能力
    async def get_arp_options(self) -> dict[str, Any]:
        return await self.async_call("arp", "show", {"TYPE": "options"})

    async def set_arp_filter(self, enabled: bool) -> None:
        await self.async_call("arp", "seting", {"arp_filter": 1 if enabled else 0})

    async def get_stream_control(self) -> dict[str, Any]:
        return await self.async_call("stream_control", "show", {"TYPE": "stream_ctl_mode"})

    async def set_stream_control(self, enabled: bool) -> None:
        await self.async_call("stream_control", "seting", {"stream_ctl_mode": 1 if enabled else 0})

    async def reconnect_wan(self, interface_id: int = 1) -> None:
        """重拨指定 WAN 口（PPPoE 重连）。"""
        await self.async_call("wan", "link_pppoe_reconnect", {"id": interface_id})

    async def reboot(self) -> None:
        await self.async_call("reboots", "reboots", {})


async def _safe_json(resp: aiohttp.ClientResponse) -> dict[str, Any]:
    """读取响应并容错解析（iKuai 老接口可能返回 GBK 或非法 JSON）。"""
    raw = await resp.read()
    for encoding in ("utf-8", "gbk", "gb18030", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover - 兜底
        text = raw.decode("utf-8", errors="ignore")

    import json

    text = text.replace('"data":timeout', '"data":[]')
    try:
        data = json.loads(text, strict=False)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}
