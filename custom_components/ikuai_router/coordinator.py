"""iKuai Router 数据协调器：一次抓取，统一清洗成实体友好的结构。"""
from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import IkuaiApi, IkuaiAuthError, IkuaiError
from .const import (
    CONF_DISK_THRESHOLD,
    CONF_OFFLINE_GRACE,
    CONF_TRACKERS,
    DEFAULT_DISK_THRESHOLD,
    DEFAULT_OFFLINE_GRACE,
    DISK_PURPOSE,
    DOMAIN,
    LOGGER,
    MAC_ACL_MODES,
)
from .legacy import IkuaiLegacyClient

OFFLINE_NAME_HINT = "未知终端"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, "", "--"):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value in (None, "", "--"):
            return default
        return float(str(value).strip().rstrip("%"))
    except (TypeError, ValueError):
        return default


def _kb_to_mb(value: Any) -> float | None:
    try:
        return round(float(value) / 1024, 2)
    except (TypeError, ValueError):
        return None


def normalize_mac(mac: str | None) -> str:
    """MAC 统一小写冒号分隔，兼容横杠/点分格式。"""
    if not mac:
        return ""
    return mac.strip().lower().replace("-", ":").replace(".", ":")


def clean_client_name(client: dict[str, Any]) -> str:
    """尽量给终端一个好读的名字。"""
    for key in ("termname", "comment", "client_model", "hostname"):
        name = (client.get(key) or "").strip()
        if name and name != "--":
            return name
    mac = normalize_mac(client.get("mac"))
    return f"终端 {mac[-5:].replace(':', '').upper()}" if mac else OFFLINE_NAME_HINT


class IkuaiCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """抓取并清洗全家桶数据。"""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: IkuaiApi,
        legacy: IkuaiLegacyClient | None,
        scan_interval: int,
    ) -> None:
        super().__init__(
            hass,
            LOGGER,
            name=f"{DOMAIN}_{entry.data.get('host', 'router')}",
            update_interval=timedelta(seconds=scan_interval),
        )
        self.entry = entry
        self.api = api
        self.legacy = legacy
        self.gwid: str = entry.entry_id
        self.hub_device_id: str | None = None
        self.hostname: str = "iKuai"
        self.sw_version: str | None = None
        self.hw_version: str | None = None
        self.wifi_supported: bool = False
        self._last_seen: dict[str, float] = {}
        self._cursors: dict[str, int | None] = {
            "presence": None,
            "system": None,
            "ddns": None,
            "wireless": None,
        }
        self._seen_message_sigs: set[str] = set()

    # ------------------------------------------------------------------ 抓取
    async def _async_update_data(self) -> dict[str, Any]:
        try:
            results = await asyncio.gather(
                self.api.get_system(),
                self.api.get_clients(),
                self.api.get_interfaces(),
                self.api.get_wireless(),
                self.api.get_ipv6_traffic(),
                self.api.get_mac_mode(),
                self.api.get_mac_rules(),
                self.api.get_message_center(),
                self.api.get_presence_log(),
                self.api.get_system_log(),
                self.api.get_ddns_log(),
                self.api.get_wireless_log(),
                self.api.get_backup(),
                self.api.get_upgrade(),
                self.api.get_upgrade_status(),
                self.api.get_disks(),
                self.api.get_traffic_summary(),
                self.api.get_dhcp_static(),
                return_exceptions=True,
            )
        except IkuaiAuthError as err:
            raise UpdateFailed(str(err)) from err
        except IkuaiError as err:
            raise UpdateFailed(str(err)) from err

        def safe(index: int) -> dict[str, Any]:
            item = results[index]
            if isinstance(item, Exception):
                if isinstance(item, IkuaiAuthError):
                    LOGGER.warning("iKuai 令牌失效: %s", item)
                else:
                    LOGGER.debug("接口 %s 抓取失败: %s", index, item)
                return {}
            return _as_dict(item)

        (
            sys_res,
            clients_res,
            iface_res,
            wifi_res,
            v6_res,
            mac_mode_res,
            mac_rules_res,
            msg_res,
            presence_res,
            system_log_res,
            ddns_res,
            wifi_log_res,
            backup_res,
            upgrade_res,
            upgrade_status_res,
            disks_res,
            traffic_res,
            dhcp_res,
        ) = (safe(i) for i in range(18))

        if not sys_res:
            raise UpdateFailed("无法读取系统信息，请检查令牌权限与网络连通性")

        data: dict[str, Any] = {
            "system": self._parse_system(sys_res, iface_res, v6_res, wifi_res),
            "interfaces": {},
            "wan": {"online": 0, "total": 0},
            "clients": {},
            "clients_online": {},
            "traffic": self._parse_traffic(traffic_res),
            "security": self._parse_security(mac_mode_res, mac_rules_res),
            "maintenance": self._parse_maintenance(upgrade_res, upgrade_status_res),
            "backup": self._parse_backup(backup_res),
            "disks": self._parse_disks(disks_res),
            "dhcp": self._parse_dhcp(dhcp_res),
            "events": self._parse_events(msg_res, presence_res, system_log_res, ddns_res, wifi_log_res),
            "legacy": {"arp_filter": None, "stream_control": None},
        }

        data["interfaces"] = self._parse_interfaces(iface_res, v6_res)
        wan_lines = {k: v for k, v in data["interfaces"].items() if v.get("is_wan")}
        data["wan"] = {
            "online": sum(1 for v in wan_lines.values() if v.get("connected")),
            "total": len(wan_lines),
        }

        online_map = self._parse_clients(clients_res)
        data["clients_online"] = online_map
        data["clients"] = self._apply_tracker_grace(online_map)

        if self.legacy is not None and self.legacy.available:
            data["legacy"] = await self._fetch_legacy()

        return data

    # ------------------------------------------------------------- 各段解析
    def _parse_system(
        self,
        sys_res: dict[str, Any],
        iface_res: dict[str, Any],
        v6_res: dict[str, Any],
        wifi_res: dict[str, Any],
    ) -> dict[str, Any]:
        sysinfo = _as_dict(sys_res.get("sysinfo"))
        verinfo = _as_dict(sysinfo.get("verinfo"))
        memory = _as_dict(sysinfo.get("memory"))
        users = _as_dict(sysinfo.get("online_user"))
        stream = _as_dict(sysinfo.get("stream"))
        disk = _as_dict(sysinfo.get("disk"))

        self.gwid = str(sysinfo.get("gwid") or self.gwid)
        self.hostname = sysinfo.get("hostname") or "iKuai"
        self.sw_version = verinfo.get("version") or verinfo.get("verstring") or None
        self.hw_version = verinfo.get("arch") or None

        cpu_list = _as_list(sysinfo.get("cpu"))
        cpu_load = _to_float(cpu_list[0]) if cpu_list else None
        temp_list = _as_list(sysinfo.get("cputemp"))
        temperature = _to_float(temp_list[0]) if temp_list else None

        # WAN IPv4 汇总（多拨 / 多 WAN）
        wan_ips: list[str] = []
        for item in _as_list(iface_res.get("iface_check")):
            if not isinstance(item, dict):
                continue
            ip = item.get("ip_addr")
            if ip and ip not in ("--", "0.0.0.0") and str(item.get("result", "")).lower() in (
                "success",
                "ok",
                "1",
            ):
                wan_ips.append(str(ip))

        # IPv6 汇总
        v6 = {"up": 0, "down": 0, "total_up": 0, "total_down": 0, "conn": 0}
        for item in _as_list(v6_res.get("data")):
            if not isinstance(item, dict):
                continue
            v6["up"] += _to_int(item.get("upload"))
            v6["down"] += _to_int(item.get("download"))
            v6["total_up"] += _to_int(item.get("total_upload"))
            v6["total_down"] += _to_int(item.get("total_download"))
            v6["conn"] += _to_int(item.get("conn"))

        ap_status = _as_dict(_as_dict(wifi_res).get("ap_status"))
        clt_status = _as_dict(_as_dict(wifi_res).get("clt_status"))
        self.wifi_supported = _to_int(ap_status.get("ap_count")) > 0

        return {
            "cpu_load": cpu_load,
            "cpu_cores": len(cpu_list),
            "freq": _as_list(sysinfo.get("freq")),
            "memory_usage": _to_float(memory.get("used")),
            "memory_detail": {
                k: _kb_to_mb(memory.get(k)) for k in ("total", "available", "free", "cached", "buffers")
            },
            "uptime": _to_int(sysinfo.get("uptime")),
            "temperature": temperature,
            "verinfo": verinfo,
            "ver_string": verinfo.get("verstring"),
            "boot_time": _to_int(sysinfo.get("boot_time")),
            "wan_ip": wan_ips[0] if wan_ips else None,
            "wan_ip_list": wan_ips,
            "online_users": _to_int(users.get("count")),
            "online_user_detail": {
                "wired": _to_int(users.get("count_wired")),
                "wireless": _to_int(users.get("count_wireless")),
                "24g": _to_int(users.get("count_2g")),
                "5g": _to_int(users.get("count_5g")),
            },
            "connection_count": _to_int(stream.get("connect_num")),
            "connect_detail": {
                "tcp": _to_int(stream.get("tcp_connect_num")),
                "udp": _to_int(stream.get("udp_connect_num")),
                "icmp": _to_int(stream.get("icmp_connect_num")),
                "ipv6": v6["conn"],
            },
            "upload": _to_int(stream.get("upload")),
            "download": _to_int(stream.get("download")),
            "total_up": _to_int(stream.get("total_up")),
            "total_down": _to_int(stream.get("total_down")),
            "v6_stats": {
                "upload_speed": round(v6["up"] / 1024 / 1024, 3),
                "download_speed": round(v6["down"] / 1024 / 1024, 3),
                "total_upload": round(v6["total_up"] / 1_000_000_000, 2),
                "total_download": round(v6["total_down"] / 1_000_000_000, 2),
                "conn": v6["conn"],
            },
            "ap_online": _to_int(ap_status.get("ap_online")),
            "wireless_detail": {
                "ap_total": _to_int(ap_status.get("ap_count")),
                "ap_offline": _to_int(ap_status.get("ap_offline")),
                "clients_24g": _to_int(clt_status.get("clt_count_2g")),
                "clients_5g": _to_int(clt_status.get("clt_count_5g")),
                "clients_active": _to_int(clt_status.get("clt_active")),
            },
            "log_disk": {
                "partition": disk.get("partition"),
                "total": _to_int(disk.get("total_size")),
                "used": _to_int(disk.get("used_size")),
                "available": _to_int(disk.get("available_size")),
                "use_percent": _to_float(disk.get("use_percent")),
            },
        }

    def _parse_interfaces(self, iface_res: dict[str, Any], v6_res: dict[str, Any]) -> dict[str, Any]:
        checks = {
            item.get("interface"): item
            for item in _as_list(iface_res.get("iface_check"))
            if isinstance(item, dict)
        }
        v6_map = {
            item.get("interface"): item
            for item in _as_list(v6_res.get("data"))
            if isinstance(item, dict)
        }
        children: dict[str, list[dict[str, Any]]] = {}
        for check in checks.values():
            children.setdefault(str(check.get("parent_interface")), []).append(check)

        parsed: dict[str, Any] = {}
        for item in _as_list(iface_res.get("iface_stream")):
            if not isinstance(item, dict):
                continue
            name = str(item.get("interface") or "")
            if not name:
                continue
            check = checks.get(name)
            v6 = v6_map.get(name)

            if check is not None:
                connected = str(check.get("result", "")).lower() in ("success", "ok", "1")
                is_wan = True
            elif name.startswith("wan"):
                siblings = children.get(name, [])
                connected = any(
                    str(c.get("result", "")).lower() in ("success", "ok", "1") for c in siblings
                )
                is_wan = True
            else:
                connected = True
                is_wan = False

            parsed[name] = {
                "name": name,
                "comment": item.get("comment") or (check or {}).get("comment") or "",
                "ip": None if item.get("ip_addr") in ("--", None) else item.get("ip_addr"),
                "upload": _to_int(item.get("upload")),
                "download": _to_int(item.get("download")),
                "total_up": _to_int(item.get("total_up")),
                "total_down": _to_int(item.get("total_down")),
                "connect_num": _to_int(item.get("connect_num")),
                "updropped": _to_int(item.get("updropped")),
                "downdropped": _to_int(item.get("downdropped")),
                "connected": connected,
                "is_wan": is_wan,
                "protocol": (check or {}).get("internet"),
                "gateway": (check or {}).get("gateway"),
                "connect_since": (check or {}).get("updatetime"),
                "auto_switch": (check or {}).get("auto_switch"),
                "errmsg": (check or {}).get("errmsg"),
                "parent": (check or {}).get("parent_interface"),
                "has_v6": isinstance(v6, dict),
                "v6_upload": _to_int((v6 or {}).get("upload")),
                "v6_download": _to_int((v6 or {}).get("download")),
                "v6_total_up": _to_int((v6 or {}).get("total_upload")),
                "v6_total_down": _to_int((v6 or {}).get("total_download")),
                "v6_conn": _to_int((v6 or {}).get("conn")),
            }
        return parsed

    def _parse_clients(self, clients_res: dict[str, Any]) -> dict[str, Any]:
        online: dict[str, Any] = {}
        for item in _as_list(clients_res.get("data")):
            if not isinstance(item, dict):
                continue
            mac = normalize_mac(item.get("mac"))
            if not mac:
                continue
            online[mac] = {
                "mac": mac,
                "name": clean_client_name(item),
                "ip": item.get("ip_addr"),
                "upload": _to_int(item.get("upload")),
                "download": _to_int(item.get("download")),
                "total_up": _to_int(item.get("total_up")),
                "total_down": _to_int(item.get("total_down")),
                "connect_num": _to_int(item.get("connect_num")),
                "vendor": item.get("client_vendor"),
                "client_type": item.get("client_type"),
                "comment": item.get("comment"),
                "apmac": item.get("apmac"),
                "ssid": item.get("ssid"),
                "signal": _to_int(item.get("signal")),
                "interface": item.get("interface"),
                "online_since": item.get("uptime"),
                "username": item.get("username"),
            }
        return online

    def _trackers(self) -> dict[str, Any]:
        trackers = self.entry.options.get(CONF_TRACKERS) or self.entry.data.get(CONF_TRACKERS) or {}
        return trackers if isinstance(trackers, dict) else {}

    def _apply_tracker_grace(self, online_map: dict[str, Any]) -> dict[str, Any]:
        """只保留需要"关注"的终端，并按宽限期延后离线判断。"""
        trackers = self._trackers()
        if not trackers:
            return {}

        grace = _to_int(
            self.entry.options.get(CONF_OFFLINE_GRACE, DEFAULT_OFFLINE_GRACE), DEFAULT_OFFLINE_GRACE
        ) * 60
        now = time.monotonic()
        previous = self.data.get("clients", {}) if isinstance(self.data, dict) else {}
        result: dict[str, Any] = {}

        for mac_raw, conf in trackers.items():
            mac = normalize_mac(mac_raw)
            if not mac:
                continue
            if mac in online_map:
                self._last_seen[mac] = now
                client = dict(online_map[mac])
                if isinstance(conf, dict) and conf.get("name"):
                    client["name"] = conf["name"]
                result[mac] = client
                continue

            last_seen = self._last_seen.get(mac, 0)
            if last_seen and now - last_seen < grace:
                cached = previous.get(mac)
                if cached:
                    cached = dict(cached)
                    cached["upload"] = 0
                    cached["download"] = 0
                    cached["offline_buffering"] = True
                    result[mac] = cached
        return result

    def _parse_traffic(self, traffic_res: dict[str, Any]) -> dict[str, Any]:
        items = []
        for item in _as_list(traffic_res.get("terminal")):
            if not isinstance(item, dict):
                continue
            items.append(
                {
                    "mac": normalize_mac(item.get("mac")),
                    "ip": item.get("ip_addr"),
                    "name": item.get("comment") or item.get("username") or item.get("mac"),
                    "total_mb": round(_to_int(item.get("sum_total")) / 1024 / 1024, 2),
                    "up_mb": round(_to_int(item.get("sum_total_up")) / 1024 / 1024, 2),
                    "down_mb": round(_to_int(item.get("sum_total_down")) / 1024 / 1024, 2),
                }
            )
        items.sort(key=lambda x: x["total_mb"], reverse=True)
        return {
            "total_mb": round(_to_int(traffic_res.get("terminal_total_flow")) / 1024 / 1024, 2),
            "client_count": _to_int(traffic_res.get("terminal_total")),
            "ranking": items[:10],
            "top_name": items[0]["name"] if items else None,
            "top_mb": items[0]["total_mb"] if items else None,
        }

    def _parse_security(self, mode_res: dict[str, Any], rules_res: dict[str, Any]) -> dict[str, Any]:
        mode = _to_int(mode_res.get("acl_mac"))
        rules = {}
        for item in _as_list(rules_res.get("data")):
            if isinstance(item, dict) and item.get("id") is not None:
                rules[str(item["id"])] = item
        return {
            "mode": mode,
            "mode_label": MAC_ACL_MODES.get(mode, "blacklist"),
            "rules": rules,
        }

    def _parse_maintenance(self, upgrade_res: dict[str, Any], status_res: dict[str, Any]) -> dict[str, Any]:
        info = _as_dict(upgrade_res.get("data"))
        status = _as_dict(status_res.get("auto_upgrade"))
        current = info.get("system_ver")
        latest = info.get("new_system_ver")
        code = _to_int(status.get("status"))
        message = status.get("status_msg") or ""

        if code != 0:
            state = message or "正在升级…"
        elif latest and current and latest != current:
            state = f"发现新版本 {latest}"
        else:
            state = "已是最新版本"

        return {
            "state": state,
            "new_version_available": bool(latest and current and latest != current),
            "detail": {
                "current_version": current,
                "latest_version": latest,
                "version_type": info.get("version_type"),
                "build_date": info.get("build_date"),
                "update_content": info.get("update_content"),
                "libproto_ver": info.get("libproto_ver"),
                "libaudit_ver": info.get("libaudit_ver"),
                "libdomain_ver": info.get("libdomain_ver"),
                "new_libproto_ver": info.get("new_libproto_ver"),
                "new_libaudit_ver": info.get("new_libaudit_ver"),
                "new_libdomain_ver": info.get("new_libdomain_ver"),
                "upgrade_status": code,
                "upgrade_message": message,
            },
        }

    def _parse_backup(self, backup_res: dict[str, Any]) -> dict[str, Any]:
        entries = [i for i in _as_list(backup_res.get("backup_info")) if isinstance(i, dict)]
        if not entries:
            return {}
        latest = max(entries, key=lambda x: _to_int(x.get("timestamp")))
        return {
            "filename": latest.get("filename"),
            "version": latest.get("version"),
            "size": _to_int(latest.get("filesize")),
            "timestamp": _to_int(latest.get("timestamp")),
            "count": len(entries),
        }

    def _parse_disks(self, disks_res: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for disk in _as_list(disks_res.get("data")):
            if not isinstance(disk, dict):
                continue
            disk_id = str(disk.get("disk"))
            total_bytes = 0
            used_bytes = 0
            partitions = []
            for part in _as_list(disk.get("partition")):
                mounted = _as_dict(part.get("mounted"))
                part_total = _to_int(mounted.get("mt_total"))
                part_used = _to_int(mounted.get("mt_used"))
                if part_total > 0:
                    total_bytes += part_total
                    used_bytes += part_used
                partitions.append(
                    {
                        "name": part.get("name"),
                        "mount": mounted.get("mt_name"),
                        "usage": mounted.get("mt_uses"),
                        "purpose": DISK_PURPOSE.get(str(mounted.get("mt_purpose")), "未知"),
                    }
                )
            result[disk_id] = {
                "id": disk_id,
                "model": disk.get("model") or disk_id,
                "type": disk.get("type"),
                "system": disk.get("system") == 1 or disk.get("system") is True,
                "size": _to_int(disk.get("size")),
                "total": total_bytes,
                "used": used_bytes,
                "usage": round(used_bytes / total_bytes * 100, 1) if total_bytes else 0,
                "partitions": partitions,
            }
        return result

    def _parse_dhcp(self, dhcp_res: dict[str, Any]) -> dict[str, Any]:
        items = []
        for item in _as_list(dhcp_res.get("static_data")):
            if not isinstance(item, dict):
                continue
            items.append(
                {
                    "mac": normalize_mac(item.get("mac")),
                    "ip": item.get("ip_addr"),
                    "comment": item.get("comment"),
                    "enabled": item.get("enabled"),
                }
            )
        return {"total": _to_int(dhcp_res.get("static_total"), len(items)), "items": items}

    def _next_events(self, key: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        """按 id 游标取增量；冷启动只记游标、不触发历史事件。"""
        items = _as_list(payload.get("data"))
        if not items:
            return []
        current_max = _to_int(items[0].get("id"))
        cursor = self._cursors.get(key)
        if cursor is None:
            self._cursors[key] = current_max
            return []
        new_items = [i for i in items if isinstance(i, dict) and _to_int(i.get("id")) > cursor]
        if new_items:
            self._cursors[key] = max(_to_int(i.get("id")) for i in new_items)
        return sorted(new_items, key=lambda x: _to_int(x.get("id")))

    def _parse_events(
        self,
        msg_res: dict[str, Any],
        presence_res: dict[str, Any],
        system_res: dict[str, Any],
        ddns_res: dict[str, Any],
        wireless_res: dict[str, Any],
    ) -> dict[str, Any]:
        messages = []
        cold_start = not self._seen_message_sigs
        for item in _as_list(msg_res.get("data")):
            if not isinstance(item, dict):
                continue
            sig = "|".join(str(item.get(k)) for k in ("type", "title", "detail"))
            if sig in self._seen_message_sigs:
                continue
            self._seen_message_sigs.add(sig)
            if not cold_start:
                messages.append(item)
        if len(self._seen_message_sigs) > 300:
            self._seen_message_sigs = set(list(self._seen_message_sigs)[-150:])

        return {
            "presence": self._next_events("presence", presence_res),
            "system": self._next_events("system", system_res),
            "ddns": self._next_events("ddns", ddns_res),
            "wireless": self._next_events("wireless", wireless_res),
            "messages": messages,
        }

    async def _fetch_legacy(self) -> dict[str, Any]:
        """传统接口只取两个开关状态，失败不影响主流程。"""
        assert self.legacy is not None
        arp, stream = await asyncio.gather(
            self.legacy.get_arp_options(),
            self.legacy.get_stream_control(),
            return_exceptions=True,
        )
        return {
            "arp_filter": None
            if isinstance(arp, Exception) or not arp
            else _to_int(arp.get("arp_filter")) == 1,
            "stream_control": None
            if isinstance(stream, Exception) or not stream
            else _to_int(stream.get("stream_ctl_mode")) == 1,
        }

    # ------------------------------------------------------------------ 辅助
    @property
    def disk_alert(self) -> bool:
        percent = (self.data or {}).get("system", {}).get("log_disk", {}).get("use_percent")
        threshold = _to_int(
            self.entry.options.get(CONF_DISK_THRESHOLD, DEFAULT_DISK_THRESHOLD), DEFAULT_DISK_THRESHOLD
        )
        return percent is not None and percent >= threshold

    def wan_lines(self) -> dict[str, Any]:
        interfaces = (self.data or {}).get("interfaces", {})
        return {k: v for k, v in interfaces.items() if v.get("is_wan")}

    def client_by_mac(self, mac: str) -> dict[str, Any]:
        mac = normalize_mac(mac)
        clients = (self.data or {}).get("clients_online", {})
        return clients.get(mac, {})
