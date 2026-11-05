"""iKuai Router 集成常量。"""
from __future__ import annotations

import logging
from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "ikuai_router"
LOGGER = logging.getLogger(__package__)

PLATFORMS: Final = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
    Platform.SELECT,
    Platform.BUTTON,
    Platform.DEVICE_TRACKER,
    Platform.EVENT,
]

# ---------------------------------------------------------------------------
# 配置项
# ---------------------------------------------------------------------------
CONF_HOST: Final = "host"
CONF_TOKEN: Final = "token"
CONF_BASE_URL: Final = "base_url"          # 探测成功后固化的实际地址（含协议）
CONF_VERIFY_SSL: Final = "verify_ssl"
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_ENABLE_CLIENTS: Final = "enable_client_entities"
CONF_TRACKERS: Final = "trackers"          # {mac: {"name": str}}
CONF_TRACKERS_EXTRA: Final = "trackers_extra"  # 手填设备名 / MAC（逗号分隔），便于添加离线终端
CONF_OFFLINE_GRACE: Final = "offline_grace"
CONF_CPU_THRESHOLD: Final = "cpu_threshold"
CONF_MEM_THRESHOLD: Final = "mem_threshold"
CONF_DISK_THRESHOLD: Final = "disk_threshold"
CONF_ENABLE_WIRELESS: Final = "enable_wireless"
CONF_ENABLE_LEGACY: Final = "enable_legacy"
CONF_LEGACY_USERNAME: Final = "legacy_username"
CONF_LEGACY_PASSWORD: Final = "legacy_password"

DEFAULT_SCAN_INTERVAL: Final = 15
MIN_SCAN_INTERVAL: Final = 5
DEFAULT_OFFLINE_GRACE: Final = 2          # 分钟
DEFAULT_CPU_THRESHOLD: Final = 85         # %
DEFAULT_MEM_THRESHOLD: Final = 90         # %
DEFAULT_DISK_THRESHOLD: Final = 90        # %

# MAC 访问控制模式
MAC_ACL_MODES: Final = {0: "blacklist", 1: "whitelist"}
MAC_ACL_MODES_REVERSE: Final = {v: k for k, v in MAC_ACL_MODES.items()}
MODE_BLACKLIST: Final = 0

# 磁盘用途映射（iKuai 固件内置）
DISK_PURPOSE: Final = {
    "0": "普通储存",
    "1": "有余繁星",
    "2": "视频缓存",
    "3": "行为记录",
    "4": "钉钉闪传",
}

ATTR_LAST_UPDATE: Final = "last_update"
