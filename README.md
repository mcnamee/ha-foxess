# FoxESS Local (cloud replacement)

A Home Assistant custom integration that **replaces the FoxESS cloud** for a
FoxESS Wi‑Fi logger on your LAN, so your inverter/battery keeps working with its
internet access blocked.

It runs a small TLS server inside Home Assistant that the logger connects to
instead of `www.foxesscloud.com`. It answers the logger's login/heartbeat so the
logger stays happily connected offline, polls the live Modbus holding registers,
and exposes them as sensors. (Control entities — work mode, min SoC, charge
schedule — are on the roadmap; the write format is already reverse‑engineered.)

> Unofficial, not affiliated with FoxESS. Use at your own risk. Tested with a
> **FoxESS KH9.9** and a `609W…`-series Wi‑Fi logger.

## How it works

The logger talks TLS to a hard‑coded cloud IP (`8.209.116.72:14431`) with a
proprietary framed protocol that tunnels Modbus. The logger does **not** validate
the server certificate, so a local server with a self‑signed cert can stand in
for the cloud. This integration is that server.

```
 logger ──TLS──▶ [gateway Dest‑NAT] ──▶ Home Assistant :14431 (this integration)
```

## Install (HACS)

1. Push this repo to GitHub.
2. HACS → ⋮ → **Custom repositories** → add the repo URL, category **Integration**.
3. Install **FoxESS Local**, then restart Home Assistant.
4. Settings → Devices & Services → **Add Integration** → *FoxESS Local*. Accept
   the default port **14431** (unless you change the Dest‑NAT target).

A self‑signed cert is generated automatically in your HA config directory
(`foxess_local_cert.pem` / `_key.pem`) — nothing to copy.

## Redirect the logger to Home Assistant (UniFi)

Create a **Destination NAT** rule so the logger's cloud traffic goes to HA:

- Settings → **Policy Engine → NAT → Create** (on some versions:
  Settings → Policy Table → Create New Policy → NAT)
- Type: **Dest. NAT**
- Protocol: **TCP**
- Incoming interface: the **IoT/VLAN the logger is on**
- Destination (match): `8.209.116.72`, port `14431`
- Translated IP: **your Home Assistant host IP**, translated port `14431`
- Source (optional, recommended): the logger's IP, to scope the rule

If HA is on a different VLAN than the logger, also ensure a firewall rule allows
the logger → HA host on TCP 14431 (UniFi usually auto‑creates an allow for the
NAT, but verify).

Force the logger to reconnect (reseat the Wi‑Fi dongle, or drop its connection)
and it will land on Home Assistant.

## Block the internet (the point of all this)

Once sensors are populating in HA, block the logger's WAN access at the gateway
(firewall rule on the logger's IP). The integration supplies the time the logger
would otherwise get from the cloud, so it stays connected and functional.
Note: blocking the inverter's internet may affect grid‑compliance features
(e.g. emergency‑backstop/VPP) — check your connection agreement first.

## Entities

All correlated against the inverter console and foxess-ha.

**Live power (kW):** `solar_power` (AC-coupled CT), `grid_power` (+export/−import),
`inverter_power`, `battery_power` (−charge/+discharge), plus derived
`grid_consumption_power`, `feedin_power`, `load_power`, `battery_charge_power`,
`battery_discharge_power`.

**Battery & grid:** `battery_soc` (%), `battery_voltage`, `battery_current`,
`grid_voltage`, `grid_frequency`.

**Daily energy (kWh, reset at midnight):** `grid_consumption` (= the input to
electricity-cost calcs), `feedin`, `battery_charge`, `battery_discharge` — read
straight from the inverter's own daily counters (register 0x7D0E etc.), so they
match the cloud exactly.

Raw `r7918…r7957` register sensors (disabled by default, *diagnostic*) are also
exposed for future mapping.

### Migrating from the cloud `foxess-ha` integration

To keep existing dashboards/automations working, once you remove `foxess-ha`
rename these to adopt its entity_ids — notably `sensor.grid_consumption` →
`sensor.foxess_grid_consumption` (the electricity-cost chain's only dependency).

## Control — `foxess_local.force_charge` service

Writes a grid force-charge schedule to the inverter by **template-replay**: it
reproduces the exact 14-write func-10 sequence the cloud sends for a schedule
change, substituting your window/SoC/mode into schedule block `0xBB8A`.

Fields: `start` ("HH:MM"), `end` ("HH:MM"), `max_soc` (%), optional `fd_pwr` (W,
default 10500), `work_mode` (default ForceCharge), and `dry_run`.

**Safety:** `dry_run` defaults to **true** — the service only *logs* the frames
it would send. Set `dry_run: false` to actually write. The builder is self-tested
to reproduce a known captured schedule byte-for-byte. First live use should be a
benign window, verified by reading the schedule back.

Example (replaces the cloud `shell_command.foxess_force_charge` in your automations):

```yaml
service: foxess_local.force_charge
data:
  start: "13:00"
  end: "14:59"
  max_soc: 100
  dry_run: false
```

## Status / known limitations

The integration is feature-complete: offline keep-alive, full telemetry
(including the daily energy counters that drive electricity-cost calcs), derived
power sensors, and the `force_charge` scheduler. The scheduler write path is
**live-verified** — a func-10 write to the schedule block is accepted by the
inverter, persists, and reads back correctly. A daily-load helper is provided in
`packages/`.

Remaining minor item:

- A schedule change writes 14 blocks (the `force_charge` service replays exactly
  what the cloud sent). The capture didn't include a separate "enable" write, so
  on your **first real `force_charge`**, confirm in the app that the new schedule
  becomes active; if it doesn't, an enable step may be needed. (The write
  mechanism itself is proven.)
