# iKuai Router for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![Version](https://img.shields.io/badge/version-1.0.0-blue.svg)](#)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

**English** | [简体中文](README.zh-Hans.md)

A Home Assistant custom integration for **iKuai (爱快) routers**, built on the
iKuai **OpenAPI v4**. Token authentication means **no router password is ever
stored** in Home Assistant.

The design borrows from [PraxiGEN/ha_ikuai_connect](https://github.com/PraxiGEN/ha_ikuai_connect)
(token authentication) and [dscao/ikuai](https://github.com/dscao/ikuai) (legacy
`/Action/call` control), which is kept as an optional add-on for the few features
OpenAPI does not expose.

## Features

- Token-based authentication over HTTPS, with HTTP fallback and redirect handling
- Router health: CPU, memory, uptime, connection count, total and current traffic
- Per-interface statistics, plus a connectivity sensor for every WAN line
- Client tracking (`device_tracker`) with IP, MAC, vendor, SSID and signal
- Health alerts as binary sensors: high CPU, high memory, low log storage,
  firmware update available
- Traffic ranking and DHCP static bindings
- MAC access-control switches and blacklist/whitelist mode selection
- Event entities for client presence, system log, DDNS log, wireless log and the
  message center
- Eight services, including temporary blocking with automatic unblock and a
  generic `call_api` escape hatch
- Wireless entities are only created when an AP is actually present

## Requirements

- Home Assistant **2024.11** or newer (tested on **2026.11.0**)
- iKuai firmware **4.0+** — OpenAPI v4 does not exist on 3.x and earlier
- An API token created on the router

Verified against: `4.0.315-alpha x64`, dual-WAN PPPoE, 49 wired clients, no
wireless radio.

## Installation

### HACS (recommended)

1. HACS → Integrations → ⋮ (top right) → **Custom repositories**
2. Add this repository URL, category **Integration**
3. Search for **iKuai Router**, install, then restart Home Assistant

### Manual

Copy the `custom_components/ikuai_router` folder into your Home Assistant
`config/custom_components/` directory and restart.

## Getting an API token

Router web UI → **System Settings → Account Settings / iKuai Cloud** → create an
API token. The token is a Base64-looking string; copy it immediately, it is only
shown once.

> The integration tries `https://` first and falls back to `http://`, following
> 302 redirects to re-detect the real scheme and port. **Just enter `192.168.2.1`**
> — do not prefix it with `http://`.

## Configuration

Settings → Devices & Services → **Add Integration** → *iKuai Router*

| Field | Description |
|---|---|
| Host | Router address, e.g. `192.168.2.1` (no scheme) |
| API token | The token created above |
| Verify SSL certificate | Leave **off** for self-signed certificates |

Then open **Configure** to adjust options:

| Option | Default | Description |
|---|---|---|
| Scan interval | 15 s | 10–30 s recommended; lower values load the router |
| Tracked clients | none | Multi-select of online clients; only these get a `device_tracker` |
| Add trackers manually | empty | Comma-separated **names or MACs** for devices that are currently offline |
| Create rate/online entities for tracked clients | off | +3 entities per tracked client |
| Offline grace | 2 min | Keeps `home` briefly so automations don't flap |
| CPU / memory / disk thresholds | 85 / 90 / 90 % | Drives the corresponding binary sensors |
| Enable legacy API | off | Needs username + password; unlocks ARP validation, stream control, WAN reconnect |

## Entities

Entities are grouped under one hub device plus a few sub-devices, linked with
`via_device`. Counts below are from the reference setup (2 WAN, 1 LAN, 1 disk,
no wireless).

### Hub device — router

CPU usage, memory usage, uptime, online clients, connection count, upload and
download speed, total upload / download, WAN IP.

Binary sensors: high CPU load, high memory usage, low log storage, firmware
update available.

Buttons: reboot router, check for updates, back up configuration, start firmware
upgrade (*WAN reconnect* appears only when the legacy API is enabled).

Events: client presence (online / offline), system log, DDNS log, wireless log,
message center.

### Network interfaces (`via_device` → router)

For every interface: up/down rate, total up/down, connection count. Each WAN also
gets a **connectivity** binary sensor, so a dropped line shows up immediately.

> Entities for backup WANs are **disabled by default** — only the link-status
> sensor is exposed to keep the entity list readable.

### Network & clients

DHCP static binding count, today's total traffic (Top 10 ranking in the
attributes).

### Security center

Global MAC access-control mode (blacklist/whitelist select) and one switch per
MAC rule. Switches are added and removed automatically when you edit rules on the
router.

### Maintenance

Firmware version, firmware status, latest backup.

### Storage (only when a disk is present)

Disk capacity, used space, usage percentage, with per-partition detail in the
attributes.

### Tracked clients

Each tracked client becomes a `device_tracker` using the router as source. State
is `home` / `not_home`, attributes include IP, MAC, vendor and, for wireless
clients, SSID and signal. Use **entity settings → advanced** to attach a zone.

## Services

| Service | Purpose |
|---|---|
| `ikuai_router.get_traffic_ranking` | Today's per-client traffic ranking (`limit`, default 100) |
| `ikuai_router.get_protocol_stats` | Protocol breakdown for one client |
| `ikuai_router.get_offline_history` | Recently disconnected clients |
| `ikuai_router.add_mac_rule` / `delete_mac_rule` | Manage MAC access-control rules |
| `ikuai_router.block_client` | Block a client, auto-unblock after `minutes` |
| `ikuai_router.unblock_client` | Remove a rule created by this integration |
| `ikuai_router.call_api` | Call any OpenAPI path directly (exploration / future endpoints) |

`block_client` only works in **blacklist** mode. In whitelist mode "blocking"
would actually whitelist the client, so the service refuses to run.

### Examples

Block a child's tablet for two hours:

```yaml
action: ikuai_router.block_client
data:
  mac: "2a:56:85:f2:58:93"
  minutes: 120
```

Notify when a WAN line drops:

```yaml
triggers:
  - trigger: state
    entity_id: binary_sensor.wan1_connectivity
    to: "off"
    for: "00:02:00"
actions:
  - action: notify.persistent_notification
    data:
      title: WAN1 down
      message: WAN1 has been offline for 2 minutes.
```

Mark presence with a tracked phone:

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

## Troubleshooting

**"The router rejected this operation: the current token lacks this permission"**
Some endpoints are permission-gated per API account. On the reference router
`POST /system/upgrade:check` returns `403 / code 1002` even with a valid token, so
"Check for updates" silently falls back to re-reading the firmware status the
router already knows. Grant the account more permissions on the router if you
need the real trigger; backup and upgrade buttons will report the error instead
of pretending to succeed.

**Entity IDs look like `sensor.ikuai_4_0_315_cpu_usage` / pinyin**
Home Assistant generates `entity_id`s from the *localized* entity name. With a
Chinese UI you get pinyin-based IDs. Rename any entity in the UI if you want
stable English IDs for automations.

**A tracked phone keeps flapping between home and not_home**
iPhones rotate their private Wi-Fi MAC address unless *Private Wi-Fi Address* is
disabled for that network. If the MAC changes, the tracker can no longer match
the client — turn the feature off for that SSID, or add the new MAC.

**Nothing works after a firmware upgrade**
OpenAPI paths and the legacy session flow can change between major versions. Open
an issue with your firmware version and the output of
`ikuai_router.call_api` for the failing path.

## Known limitations

- **Reboot** is implemented through a one-shot reboot schedule, because OpenAPI
  has no direct reboot endpoint; the router reboots about a minute later.
- Firmware upgrade and backup are long-running: the buttons only trigger them,
  watch the firmware status sensor for the result.
- The legacy API (ARP / stream control / WAN reconnect) uses the web UI session,
  which is more likely to break on firmware updates.
- Wireless entities only appear when an AP is detected — a soft router without a
  radio produces none.
- Port forwarding, DMZ, UPnP and Wake-on-LAN are **not** exposed by OpenAPI v4 on
  the firmware we tested (all return 404), so they are intentionally absent.

## Credits

- [PraxiGEN/ha_ikuai_connect](https://github.com/PraxiGEN/ha_ikuai_connect) —
  OpenAPI v4 field mappings and endpoint coverage
- [dscao/ikuai](https://github.com/dscao/ikuai) — legacy login flow and switch
  definitions

## License

[MIT](LICENSE). This project is a derivative work of the two projects above; see
`LICENSE` for the attribution notice.
