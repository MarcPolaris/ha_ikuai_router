# iKuai Router for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![Version](https://img.shields.io/badge/version-1.0.0-blue.svg)](#)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

[English](README.md) | **简体中文**

面向 **iKuai 爱快路由器** 的 Home Assistant 自定义集成，基于爱快 **OpenAPI v4**。
它把 [PraxiGEN/ha_ikuai_connect](https://github.com/PraxiGEN/ha_ikuai_connect)
（令牌方案）和 [dscao/ikuai](https://github.com/dscao/ikuai)（传统 `/Action/call`
方案）重新融合重写，并补上了两边都缺的东西：HTTPS 自动识别、逐条线路断线告警、
流量排行、一键临时封禁、健康度告警。

采用令牌认证，**不会在 Home Assistant 里保存路由器密码**。

---

## 特性对比

| 能力 | ha_ikuai_connect | dscao/ikuai | 本集成 |
|---|---|---|---|
| 认证方式 | Bearer 令牌 | 账号密码（`sess_key`） | 令牌为主，密码可选 |
| 路由器强制 HTTPS（302 → https） | ❌ 直接连不上 | ❌ 直接连不上 | ✅ 自动探测协议与端口 |
| 每条 WAN 独立断线告警 | ❌ | ❌ | ✅ 每条 WAN 一个二元传感器 |
| 每终端实体 | 仅追踪 | 仅追踪 | ✅ 追踪 + 在线 + 上下行速率 |
| 流量排行 | 服务 | ❌ | ✅ 传感器 + 服务 |
| 一键临时封禁（到点自动解封） | ❌ | ❌ | ✅ 服务 |
| CPU / 内存 / 磁盘 / 固件告警 | ❌ | ❌ | ✅ |
| 静态 DHCP 绑定 | ❌ | ❌ | ✅ 只读传感器 |
| 事件 | 5 类 | ❌ | 5 类，带消息去重 |
| 无无线硬件时的无线实体 | 生成一堆死实体 | 生成一堆死实体 | ✅ 自动跳过 |

## 支持范围

- Home Assistant **2024.11** 及以上（实测 **2026.11.0**）
- iKuai 固件 **4.0 及以上**（OpenAPI v4 在 3.x 及更早版本上不存在）
- 需要在路由器上创建一个 API 令牌

已实测环境：`4.0.315-alpha x64`，双 WAN PPPoE，49 台有线终端，无无线网卡。

## 安装

### HACS（推荐）

1. HACS → 集成 → 右上角 ⋮ → **自定义存储库**
2. 填入本仓库地址，类别选「集成」
3. 搜索 **iKuai Router** 安装，重启 Home Assistant

### 手动

把 `custom_components/ikuai_router` 整个目录复制到 HA 配置目录的
`custom_components/` 下，重启。

## 获取 OpenAPI 令牌

路由器后台 → **系统设置 → 账户设置 / 爱快云** → 新建 API 令牌。
令牌是一串 Base64 样式的字符，只显示一次，创建后立刻复制保存。

> 集成会先试 `https://`，失败再回退 `http://`，并跟随 302 的 `Location`
> 重新探测真实协议和端口。**地址只填 `192.168.2.1`**，不要带 `http://`。

## 配置

设置 → 设备与服务 → 添加集成 → **iKuai Router**

| 字段 | 说明 |
|---|---|
| 路由器地址 | 例如 `192.168.2.1`（不带协议） |
| OpenAPI 令牌 | 上面创建的令牌 |
| 校验 SSL 证书 | 自签证书请保持**关闭** |

添加后点「配置」进入选项：

| 选项 | 默认 | 说明 |
|---|---|---|
| 轮询间隔 | 15 秒 | 建议 10–30 秒，过低会加重路由器负担 |
| 追踪的终端 | 空 | 多选在线终端，只有被选中的才生成 `device_tracker` |
| 手动添加终端 | 空 | 逗号分隔的**设备名或 MAC**，用于补录当前不在线的设备 |
| 为追踪终端生成速率/在线实体 | 关 | 每个终端 +3 个实体 |
| 离线宽限 | 2 分钟 | 短暂掉线不立刻变 `not_home`，避免自动化抖动 |
| CPU / 内存 / 磁盘告警阈值 | 85 / 90 / 90 % | 超过即对应二元传感器置位 |
| 启用传统接口 | 关 | 需填账号密码，解锁 ARP 校验、智能流控、WAN 重拨 |

## 实体

实体按设备分组，子设备通过 `via_device` 挂在主设备下。下列数量来自参考环境
（2 个 WAN、1 个 LAN、1 块硬盘、无无线）。

### 主设备（路由器）

CPU 使用率、内存使用率、运行时长、在线终端数、连接数、上传/下载速度、
累计上传/下载、WAN IP。

二元传感器：CPU 高负载、内存高占用、日志分区空间不足、固件有更新。

按钮：重启路由器、检测固件更新、备份配置、开始固件升级
（*WAN 重拨* 只在启用传统接口后出现）。

事件：终端上下线、系统日志、DDNS 日志、无线日志、消息中心。

### 网络接口（挂在路由器设备下）

每个接口都有：上传/下载速度、累计上传/下载、连接数。每条 WAN 额外有一个
**线路状态**二元传感器，掉线立刻可见。

> 备用 WAN 的速率类实体**默认禁用**，只保留线路状态，避免实体列表被噪音塞满。

### 网络与终端

静态绑定数量、今日总流量（属性里带 Top 10 排行）。

### 安全中心

全局 MAC 访问控制模式（黑/白名单选择器）以及每条 MAC 规则一个开关。
在路由器上增删规则后，开关会自动增删。

### 系统维护

固件版本、固件更新状态、最新备份。

### 存储（有硬盘时才出现）

磁盘容量、已用空间、使用率，属性带分区明细。

### 追踪终端

每个被追踪的终端生成一个 `device_tracker`，来源为路由器，状态是
`home` / `not_home`，属性含 IP、MAC、厂商；无线终端还有 SSID 和信号强度。
可在「实体设置 → 高级」里给它挂一个区域。

## 服务

| 服务 | 用途 |
|---|---|
| `ikuai_router.get_traffic_ranking` | 当日终端流量排行（`limit`，默认 100） |
| `ikuai_router.get_protocol_stats` | 单终端协议分布 |
| `ikuai_router.get_offline_history` | 最近下线的终端 |
| `ikuai_router.add_mac_rule` / `delete_mac_rule` | 增删 MAC 访问控制规则 |
| `ikuai_router.block_client` | 封禁终端，`minutes` 到点自动解封 |
| `ikuai_router.unblock_client` | 解除本集成添加的封禁 |
| `ikuai_router.call_api` | 直接调用任意 OpenAPI 路径（探索未封装接口） |

`block_client` 只在**黑名单模式**下可用。白名单模式下"封禁"会变成放行，
所以服务会直接报错阻止。

### 示例

孩子设备定时断网两小时：

```yaml
action: ikuai_router.block_client
data:
  mac: "2a:56:85:f2:58:93"
  minutes: 120
```

线路掉线 2 分钟报警：

```yaml
triggers:
  - trigger: state
    entity_id: binary_sensor.wan1_connectivity
    to: "off"
    for: "00:02:00"
actions:
  - action: notify.persistent_notification
    data:
      title: WAN1 掉线
      message: WAN1 已连续 2 分钟不可用。
```

用追踪的手机做回家判断：

```yaml
triggers:
  - trigger: state
    entity_id: device_tracker.my_phone
    to: "home"
actions:
  - action: light.turn_on
    target:
      entity_id: light.hallway
```

## 排错

**提示"该操作被路由器拒绝：当前令牌没有这项权限"**
部分接口是按 API 账号授权限的。参考环境里 `POST /system/upgrade:check` 即使令牌
有效也返回 `403 / code 1002`，所以「检测固件更新」会静默退化为"重新读取路由器已知的
固件状态"。需要真正触发检测的话，去路由器给该账号放开权限；备份和升级按钮则会明确
报错，而不是假装成功。

**实体 ID 是 `sensor.ikuai_4_0_315_cpu_usage` 这种中文拼音**
Home Assistant 用**本地化后的实体名**生成 `entity_id`，中文界面下就会得到拼音 ID。
想要英文 ID 的话，在界面上重命名实体即可。

**追踪的手机在 home / not_home 之间反复跳**
iPhone 会对每个 Wi-Fi 轮换私有 MAC 地址。MAC 一变，追踪就匹配不上了——给那个 SSID
关掉「私有无线局域网地址」，或者把新的 MAC 加进追踪列表。

**固件升级后失效**
OpenAPI 路径和传统接口的会话流程在大版本之间可能变化。反馈问题时请附上固件版本，
以及用 `ikuai_router.call_api` 调用失败路径的输出。

## 已知限制

- **重启**通过「一次性重启计划」实现（OpenAPI 没有直接重启接口），实际约 1 分钟后重启。
- 固件升级和备份是长耗时操作，按钮只负责触发，结果看「固件更新状态」传感器。
- 传统接口（ARP / 流控 / WAN 重拨）依赖前端登录会话，固件大版本升级后更容易失效。
- 无线相关实体只在检测到 AP 时创建，软路由无网卡不会生成。
- 端口转发、DMZ、UPnP、网络唤醒在实测固件的 OpenAPI v4 里都没有暴露
  （全部返回 404），因此有意不做。

## 致谢

- [PraxiGEN/ha_ikuai_connect](https://github.com/PraxiGEN/ha_ikuai_connect) — OpenAPI v4 字段映射与端点梳理
- [dscao/ikuai](https://github.com/dscao/ikuai) — 传统接口登录流程与开关定义

## 许可

[MIT](LICENSE)。本项目是上述两个项目的衍生作品，署名见 `LICENSE`。
