"""iKuai OpenAPI v4 异步客户端。

设计要点（针对真实固件的坑）：
1. 大多数固件默认强制 HTTPS：HTTP 请求会返回 302，必须自动跟随到 https。
   因此提供 :func:`async_detect_base_url` 做协议探测，并缓存探测结果。
2. 少数接口在 JSON 里返回非法片段 ``"data":timeout``，直接 json.loads 会炸，
   统一做字符串修补。
3. 老固件 / 未开启的服务返回 404，做 10 分钟负缓存，避免每轮都打。
4. 令牌模式只依赖 ``Authorization: Bearer``，不保存账号密码。
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from urllib.parse import urlparse, urlunparse

import aiohttp

from .const import LOGGER

REQUEST_TIMEOUT = 15
MISSING_TTL = 600          # 404 负缓存时长
MIN_REQUEST_INTERVAL = 0   # 预留


class IkuaiError(Exception):
    """iKuai 集成基础异常。"""


class IkuaiConnectionError(IkuaiError):
    """网络层失败（超时 / 拒绝连接）。"""


class IkuaiAuthError(IkuaiError):
    """令牌或账号无效。"""


class IkuaiPermissionError(IkuaiError):
    """令牌有效，但该 API 账号没有执行此操作的权限。"""


class IkuaiApiError(IkuaiError):
    """接口返回了错误状态。"""


def _as_dict_list(value: Any) -> list[dict[str, Any]]:
    """只保留列表里的字典项，免得各处重复判断。"""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _clean_json(text: str) -> Any:
    """修补 iKuai 偶发的非法 JSON 片段。"""
    text = text.replace('"data":timeout', '"data":[]')
    text = text.replace('"data":timeout,', '"data":[],')
    try:
        return json.loads(text, strict=False)
    except json.JSONDecodeError:
        # 再兜底一次：去掉控制字符
        cleaned = "".join(ch for ch in text if ch >= " " or ch in "\n\r\t")
        return json.loads(cleaned, strict=False)


def _normalize_base(url: str) -> str:
    """把用户输入 / 重定向地址整理成 ``scheme://host[:port]``。"""
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        url = f"http://{url}"
    parsed = urlparse(url)
    scheme = parsed.scheme or "http"
    host = parsed.hostname or ""
    port = parsed.port
    if not host:
        return ""
    default_port = 443 if scheme == "https" else 80
    netloc = host if port in (None, default_port) else f"{host}:{port}"
    return urlunparse((scheme, netloc, "", "", "", ""))


def _redirect_base(current: str, location: str) -> str | None:
    """由 302 的 Location 推导出新的 base_url。"""
    if not location:
        return None
    parsed = urlparse(location)
    if not parsed.scheme:
        # 相对跳转（例如 /login）不代表协议变化
        return None
    if not parsed.hostname:
        return None
    return _normalize_base(f"{parsed.scheme}://{parsed.netloc}")


async def async_detect_base_url(
    session: aiohttp.ClientSession, host: str, token: str, verify_ssl: bool = False
) -> tuple[str, dict[str, Any], dict[str, str]]:
    """探测路由器真实可用的 base_url。

    返回 ``(base_url, system_results, response_headers)``。
    失败抛出 :class:`IkuaiAuthError` / :class:`IkuaiConnectionError`。
    """
    raw = (host or "").strip().rstrip("/")
    if not raw:
        raise IkuaiConnectionError("地址为空")

    seeds: list[str] = []
    if raw.startswith(("http://", "https://")):
        seeds.append(_normalize_base(raw))
        swapped = "https://" if raw.startswith("http://") else "http://"
        seeds.append(_normalize_base(swapped + raw.split("://", 1)[1]))
    else:
        # 优先 HTTPS：绝大多数 4.x 固件强制 HTTPS，HTTP 只会 302
        seeds.append(_normalize_base(f"https://{raw}"))
        seeds.append(_normalize_base(f"http://{raw}"))

    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    last_error: Exception | None = None
    tried: set[str] = set()

    queue: list[str] = [s for s in seeds if s]
    while queue:
        base = queue.pop(0)
        if not base or base in tried:
            continue
        tried.add(base)
        url = f"{base}/api/v4.0/monitoring/system"
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with session.get(
                    url,
                    headers=headers,
                    ssl=None if verify_ssl else False,
                    allow_redirects=False,
                ) as resp:
                    if resp.status == 200:
                        payload = _clean_json(await resp.text())
                        if not isinstance(payload, dict) or payload.get("code") not in (0, None):
                            raise IkuaiAuthError("接口返回异常")
                        info = payload.get("results") or {}
                        return base, info, dict(resp.headers)
                    if resp.status in (401, 403):
                        raise IkuaiAuthError("令牌无效或已被禁用")
                    if resp.status in (301, 302, 303, 307, 308):
                        nxt = _redirect_base(base, resp.headers.get("Location", ""))
                        if nxt and nxt not in tried:
                            queue.insert(0, nxt)
                        continue
                    if resp.status == 404:
                        # 该固件没有 v4 OpenAPI（3.x 及更早），换个协议再试一次
                        last_error = IkuaiApiError("固件不支持 OpenAPI v4.0")
                        continue
                    last_error = IkuaiApiError(f"HTTP {resp.status}")
        except IkuaiAuthError:
            raise
        except (TimeoutError, asyncio.TimeoutError):
            last_error = IkuaiConnectionError("连接超时")
        except aiohttp.ClientError as err:
            last_error = IkuaiConnectionError(str(err))

    raise last_error or IkuaiConnectionError("无法连接到路由器")


class IkuaiApi:
    """OpenAPI v4 客户端。"""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        token: str,
        verify_ssl: bool = False,
    ) -> None:
        self._session = session
        self.base_url = _normalize_base(base_url)
        self._token = token
        self._verify_ssl = verify_ssl
        self._cache: dict[str, tuple[float, Any]] = {}
        self._missing: dict[str, float] = {}
        self._semaphore = asyncio.Semaphore(4)
        self._invalid_json = 0

    # ------------------------------------------------------------------ 基础
    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def invalidate(self, *paths: str) -> None:
        """按路径后缀清除缓存（缓存键形如 ``GET:/api/...``）。"""
        for store in (self._cache, self._missing):
            for key in list(store):
                if any(key.endswith(path) for path in paths):
                    store.pop(key, None)

    async def async_request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        ttl: int = 0,
        retry: bool = True,
    ) -> dict[str, Any]:
        """发起请求并返回 ``results`` 段内容。"""
        if not path.startswith("/api/"):
            raise ValueError("path 必须以 /api/ 开头")

        cache_key = f"{method}:{path}"
        now = time.monotonic()

        if self._missing.get(cache_key, 0) > now:
            return {}
        if ttl and method == "GET" and cache_key in self._cache:
            ts, data = self._cache[cache_key]
            if now - ts < ttl:
                return data

        url = f"{self.base_url}{path}"
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with self._semaphore:
                    async with self._session.request(
                        method,
                        url,
                        headers=self.headers,
                        json=json_body,
                        ssl=None if self._verify_ssl else False,
                    ) as resp:
                        if resp.status == 404:
                            self._missing[cache_key] = now + MISSING_TTL
                            if method != "GET":
                                raise IkuaiApiError(f"接口不存在: {path}")
                            return {}
                        if resp.status == 401:
                            raise IkuaiAuthError(
                                "令牌无效或已过期，请在路由器「系统设置 → 账户设置」重新生成"
                            )
                        if resp.status == 403:
                            # 令牌本身有效，只是这个 API 账号没有该操作的权限
                            raise IkuaiPermissionError(
                                f"当前令牌没有该操作的权限: {path}"
                            )
                        if resp.status >= 500 and retry:
                            await asyncio.sleep(0.4)
                            return await self.async_request(
                                method, path, json_body=json_body, ttl=0, retry=False
                            )
                        resp.raise_for_status()
                        text = await resp.text()
        except IkuaiAuthError:
            raise
        except (TimeoutError, asyncio.TimeoutError) as err:
            raise IkuaiConnectionError(f"请求超时: {path}") from err
        except aiohttp.ClientError as err:
            raise IkuaiConnectionError(f"网络错误: {err}") from err

        try:
            payload = _clean_json(text)
        except json.JSONDecodeError:
            self._invalid_json += 1
            LOGGER.debug("接口返回非 JSON（%s）: %.120s", path, text)
            return {}

        if not isinstance(payload, dict):
            return {}
        if payload.get("code") not in (0, None):
            message = payload.get("message") or "未知错误"
            if method != "GET":
                # 1002 = 你的账号没有执行权限：令牌有效，缺的是路由器侧的操作权限
                if str(payload.get("code")) == "1002" or "权限" in str(message):
                    raise IkuaiPermissionError(f"{path} 无权限: {message}")
                # 写操作必须让调用方知道失败，否则按钮/服务会"静默无效"
                raise IkuaiApiError(f"{path} 失败: {message}")
            LOGGER.debug("接口 %s 返回 code=%s message=%s", path, payload.get("code"), message)
            return {}
        results = payload.get("results")
        if not isinstance(results, dict):
            results = {} if results is None else {"data": results}

        if ttl and method == "GET":
            self._cache[cache_key] = (now, results)
        return results

    async def async_call(
        self,
        method: str,
        path: str,
        *,
        params: dict | list | str | None = None,
        timeout: float = REQUEST_TIMEOUT,
        raw: bool = False,
    ) -> dict[str, Any]:
        """通用调用（供服务 call_api 使用），返回统一结构。"""
        if isinstance(params, str):
            params = json.loads(params) if params.strip() else None

        url = f"{self.base_url}{path}"
        body = None
        if method.upper() == "GET":
            if isinstance(params, dict):
                from urllib.parse import urlencode

                sep = "&" if "?" in path else "?"
                url = f"{url}{sep}{urlencode(params)}"
        elif isinstance(params, (dict, list)):
            body = params

        try:
            async with asyncio.timeout(timeout):
                async with self._session.request(
                    method.upper(),
                    url,
                    headers=self.headers,
                    json=body,
                    ssl=None if self._verify_ssl else False,
                ) as resp:
                    status = resp.status
                    text = await resp.text()
        except Exception as err:  # noqa: BLE001 - 服务层需要返回错误而非抛出
            return {"success": False, "status": None, "code": None, "message": str(err), "data": None}

        try:
            payload = _clean_json(text)
        except json.JSONDecodeError:
            payload = {"_raw_text": text}
        if not isinstance(payload, dict):
            payload = {"_raw": payload}

        return {
            "success": 200 <= status < 300,
            "status": status,
            "code": payload.get("code"),
            "message": payload.get("message"),
            "data": payload if raw else payload.get("results", payload),
        }

    # ------------------------------------------------------------ 监控 / 概览
    async def get_system(self) -> dict[str, Any]:
        return await self.async_request("GET", "/api/v4.0/monitoring/system")

    async def get_clients(self) -> dict[str, Any]:
        return await self.async_request("GET", "/api/v4.0/monitoring/clients-online?limit=500", ttl=10)

    async def get_interfaces(self) -> dict[str, Any]:
        return await self.async_request("GET", "/api/v4.0/monitoring/interfaces-status", ttl=20)

    async def get_wireless(self) -> dict[str, Any]:
        return await self.async_request("GET", "/api/v4.0/monitoring/wireless-statistics", ttl=60)

    async def get_wireless_score(self) -> dict[str, Any]:
        return await self.async_request("GET", "/api/v4.0/monitoring/wireless-score", ttl=60)

    async def get_ipv6_traffic(self) -> dict[str, Any]:
        return await self.async_request("GET", "/api/v4.0/monitoring/interfaces-traffic-v6", ttl=15)

    async def get_traffic_summary(self) -> dict[str, Any]:
        return await self.async_request("GET", "/api/v4.0/monitoring/clients-traffic-summary?limit=100", ttl=60)

    async def get_offline_clients(self, limit: int = 20) -> dict[str, Any]:
        path = (
            "/api/v4.0/monitoring/clients-offline"
            f"?limit={limit}&order=desc&order_by=logout_time&page=1"
        )
        return await self.async_request("GET", path)

    async def get_protocol_stats(self, mac: str, ip: str) -> dict[str, Any]:
        path = f"/api/v4.0/monitoring/clients/protocols?mac={mac}&ip={ip}"
        return await self.async_request("GET", path, ttl=30)

    # ------------------------------------------------------------------ 日志
    async def get_message_center(self) -> dict[str, Any]:
        return await self.async_request(
            "GET", "/api/v4.0/log/message-center?limit=10&order=desc&order_by=id", ttl=60
        )

    async def get_presence_log(self) -> dict[str, Any]:
        return await self.async_request(
            "GET", "/api/v4.0/log/terminal-presence?limit=20&order=desc&order_by=id"
        )

    async def get_terminal_directory(self) -> dict[str, str]:
        """从「终端上线历史」整理出 mac → 名称 的名录。

        选项里允许用户直接填设备名（例如「小刘的iPhone13」），靠这张表翻译成 MAC；
        顺带也能给离线的终端取到好听的名字。
        """
        # 注意：不能带 order=desc，那样只会拿到最近几条，久未上线的终端会被挤掉
        res = await self.async_request(
            "GET", "/api/v4.0/log/terminal-presence?limit=2000", ttl=300
        )
        directory: dict[str, str] = {}
        for item in _as_dict_list(res.get("data")):
            mac = str(item.get("mac") or "").strip().lower().replace("-", ":")
            name = str(item.get("termname") or item.get("comment") or "").strip()
            if not mac or not name or name == "--":
                continue
            directory.setdefault(mac, name)
        return directory

    async def get_system_log(self) -> dict[str, Any]:
        return await self.async_request(
            "GET", "/api/v4.0/log/system?limit=20&order=desc&order_by=id", ttl=60
        )

    async def get_ddns_log(self) -> dict[str, Any]:
        return await self.async_request(
            "GET", "/api/v4.0/log/ddns?limit=10&order=desc&order_by=id", ttl=60
        )

    async def get_wireless_log(self) -> dict[str, Any]:
        return await self.async_request(
            "GET", "/api/v4.0/log/wireless?limit=10&order=desc&order_by=id"
        )

    # ------------------------------------------------------------ 安全 / 维护
    async def get_mac_mode(self) -> dict[str, Any]:
        return await self.async_request("GET", "/api/v4.0/security/mac-mode", ttl=60)

    async def get_mac_rules(self) -> dict[str, Any]:
        return await self.async_request("GET", "/api/v4.0/security/mac-rules?limit=200", ttl=30)

    async def set_mac_mode(self, mode: int) -> None:
        await self.async_request("PUT", "/api/v4.0/security/mac-mode", json_body={"acl_mac": mode})
        self.invalidate("/api/v4.0/security/mac-mode", "/api/v4.0/security/mac-rules?limit=200")

    async def add_mac_rule(self, payload: dict[str, Any]) -> dict[str, Any]:
        res = await self.async_request("POST", "/api/v4.0/security/mac-rules", json_body=payload)
        self.invalidate("/api/v4.0/security/mac-rules?limit=200")
        return res

    async def update_mac_rule(self, rule_id: int, payload: dict[str, Any]) -> None:
        await self.async_request(
            "PATCH", f"/api/v4.0/security/mac-rules/{rule_id}", json_body=payload
        )
        self.invalidate("/api/v4.0/security/mac-rules?limit=200")

    async def delete_mac_rule(self, rule_id: int) -> None:
        await self.async_request("DELETE", f"/api/v4.0/security/mac-rules/{rule_id}")
        self.invalidate("/api/v4.0/security/mac-rules?limit=200")

    async def get_backup(self) -> dict[str, Any]:
        return await self.async_request("GET", "/api/v4.0/system/backup", ttl=3600)

    async def trigger_backup(self) -> None:
        await self.async_request("POST", "/api/v4.0/system/backup")
        self.invalidate("/api/v4.0/system/backup")

    async def get_upgrade(self) -> dict[str, Any]:
        return await self.async_request("GET", "/api/v4.0/system/upgrade", ttl=3600)

    async def get_upgrade_status(self) -> dict[str, Any]:
        return await self.async_request("GET", "/api/v4.0/system/upgrade:status")

    async def check_upgrade(self) -> None:
        await self.async_request("POST", "/api/v4.0/system/upgrade:check")
        self.invalidate("/api/v4.0/system/upgrade")

    async def start_upgrade(self) -> None:
        await self.async_request(
            "POST", "/api/v4.0/system/upgrade:start", json_body={"type": "system"}
        )

    async def get_disks(self) -> dict[str, Any]:
        return await self.async_request("GET", "/api/v4.0/system/disks", ttl=3600)

    async def get_dhcp_static(self) -> dict[str, Any]:
        return await self.async_request("GET", "/api/v4.0/network/dhcp/static", ttl=300)

    async def get_reboot_schedules(self) -> dict[str, Any]:
        return await self.async_request("GET", "/api/v4.0/system/reboot-schedules", ttl=300)

    async def add_reboot_schedule(self, payload: dict[str, Any]) -> None:
        await self.async_request("POST", "/api/v4.0/system/reboot-schedules", json_body=payload)
        self.invalidate("/api/v4.0/system/reboot-schedules")

    async def delete_reboot_schedule(self, schedule_id: int) -> None:
        await self.async_request("DELETE", f"/api/v4.0/system/reboot-schedules/{schedule_id}")
        self.invalidate("/api/v4.0/system/reboot-schedules")
