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

Confirmed live sensors (FoxESS KH, 1.33+ firmware; func-03 holding registers):

- `battery_soc` (%), `battery_power` (kW, negative = charging), `battery_voltage`
  (V), `battery_current` (A), `battery_temperature` (°C)
- `inverter_power` (kW), `load_power` (kW), `grid_voltage` (V), `grid_frequency` (Hz)

Still being resolved (PV power and grid CT sit at 39xxx on 1.33+, which this tunnel
can't read — resolved via a short correlation session or derived):

- `solar_power`, `grid_power` (import/export), and cumulative kWh energy totals.

Raw `r7918…r7957` register sensors (disabled by default, *diagnostic*) are also
exposed — enable them and watch HA history while you change load/solar to confirm
any remaining mappings.

## Roadmap

- Battery SoC as a continuously‑polled sensor (currently only in the login burst
  frame; locating its holding register).
- Control: work mode, min SoC, charge/discharge schedule (Modbus func‑10 writes,
  format already captured).
