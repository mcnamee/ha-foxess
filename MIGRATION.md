# Migrating from the cloud `foxess-ha` to `foxess_local`

This is the safe cut-over from the cloud integration (`macxq/foxess-ha`) and the
cloud scheduler (`foxess-battery-scheduler`) to this local integration, so you
can block the inverter's internet access.

The guiding principle: **run both in parallel, verify, back up, then swap** —
nothing is removed until you've confirmed the local data matches.

> Your daily-cost history is **not** lost by this. `electricity_cost_today`,
> `electricity_cost_since` and `electricity_saved_today` are your own
> template/SQL sensors; you don't delete them, so their recorded history stays.
> The only dependency the cost math has on `foxess-ha` is the entity
> `sensor.foxess_grid_consumption` (daily grid-import kWh), which the local
> integration reproduces exactly from the inverter's own counter (register 0x7D0E).

---

## 0. Before you start

- [ ] This integration installed via HACS and **added** (Settings → Devices &
      Services → Add Integration → FoxESS Local), with the UniFi Dest-NAT
      pointing the logger at the HA host and sensors populating.
- [ ] **Take a full Home Assistant backup** (Settings → System → Backups).
- [ ] Note the local integration's actual entity ids: Settings → Entities →
      filter "FoxESS KH (local)". They are likely `sensor.foxess_kh_local_*`.

## 1. Parallel-run verification (a day is ideal)

Leave `foxess-ha` installed and compare, over normal operation:

- [ ] `sensor.foxess_kh_local_grid_consumption` tracks `sensor.foxess_grid_consumption`
      (same daily kWh, both reset at midnight) — **this is the cost-critical one.**
- [ ] `..._battery_soc` ≈ `foxess_bat_soc`
- [ ] `..._solar_power` ≈ `foxess_solar_power`
- [ ] `..._load_power` ≈ `foxess_load_power`
- [ ] `..._grid_consumption_power` ≈ `foxess_grid_consumption_power`

If they match, continue.

## 2. Dry-run the scheduler, then one live test

- [ ] Call `foxess_local.force_charge` with a benign window and **`dry_run: true`**
      (Developer Tools → Actions). Confirm it logs 14 writes with no error.
- [ ] Then one **live** test: `dry_run: false` with a benign near-future window,
      and confirm in the FoxESS app / inverter console that the schedule appears
      as you set it. (If it doesn't take effect, the scheduler "enable" flag may
      need a separate write — raise an issue before relying on it.)

## 3. Back up, then remove the cloud integrations

- [ ] **Backup again.**
- [ ] Remove `foxess-ha` (Settings → Devices & Services → FoxESS → Delete).
- [ ] Disable/retire the cloud scheduler (`shell_command.foxess_force_charge`) —
      you'll replace its automations' action in step 5.

## 4. Rename the local entities to adopt the old ids

Settings → Entities → click each → ⚙ → change **Entity ID**:

| Rename this local entity | → to this id |
|---|---|
| `sensor.foxess_kh_local_grid_consumption` | `sensor.foxess_grid_consumption` |
| `sensor.foxess_kh_local_battery_soc` | `sensor.foxess_bat_soc` |
| `sensor.foxess_kh_local_solar_power` | `sensor.foxess_solar_power` |
| `sensor.foxess_kh_local_load_power` | `sensor.foxess_load_power` |
| `sensor.foxess_kh_local_grid_consumption_power` | `sensor.foxess_grid_consumption_power` |

Because the id is the same string, HA continues the existing history/statistics
series under it — your cost chain, dashboards and the `battery_soc` template work
untouched. Expect at most a small gap covering the swap.

## 5. Swap the scheduler automations to the local service

In each force-charge automation, replace the `shell_command.foxess_force_charge`
action with `foxess_local.force_charge`, mapping the CLI args to data fields
(`--start`→`start`, `--end`→`end`, `--max-soc`→`max_soc`), and add `dry_run: false`.

**a) "Force Charge Battery - Weekend Free"** (static args):

```yaml
# was:
- action: shell_command.foxess_force_charge
  data: { args: "--start 13:00 --end 14:59 --max-soc 100" }
# becomes:
- action: foxess_local.force_charge
  data:
    start: "13:00"
    end: "14:59"
    max_soc: 100
    dry_run: false
```

**b) "Force Charge Battery - Off Peak (Solcast)"** (templated args). The computed
variables (`start_time_str`, `window_end`, `max_soc`) carry straight over — the
service coerces the rendered strings to numbers:

```yaml
# was:
- alias: Write the charge window to the inverter
  action: shell_command.foxess_force_charge
  data:
    args: "--start {{ start_time_str }} --end {{ window_end }} --max-soc {{ max_soc }}"
  response_variable: charge_result
# becomes:
- alias: Write the charge window to the inverter
  action: foxess_local.force_charge
  data:
    start: "{{ start_time_str }}"
    end: "{{ window_end }}"
    max_soc: "{{ max_soc }}"
    dry_run: false
  response_variable: charge_result
```

> **Check the response handling.** The old shell command put the script's text
> output into `charge_result`; the service instead returns a dict
> (`{written, schedule}` on a write, or `{dry_run, ...}`). If any later step in
> that automation inspects `charge_result` as text (e.g. to confirm success or
> set `input_datetime.foxess_charge_scheduled_at`), update it to read the dict —
> e.g. treat a non-null `charge_result.schedule` as success.

## 6. Recreate the daily Load total

`sensor.foxess_load` (daily load kWh, used by your load-total automation) has no
inverter register — recreate it from `load_power` with the helper package:

- [ ] Add `packages/foxess_local_load.yaml` to `<config>/packages/` (enable
      `homeassistant: packages: !include_dir_named packages`), set its `source:`
      to your renamed `sensor.foxess_load_power`, and restart.
- [ ] Confirm `sensor.foxess_load` repopulates (it's an *approximation* via power
      integration, close but not identical to the cloud's metered value).

## 7. Block the internet

- [ ] With everything verified, block the logger's WAN at the gateway (firewall
      rule on its IP). The integration supplies the time the logger would get
      from the cloud, so it stays connected and functional offline.
- [ ] Re-check: sensors still update, cost still calculates, a scheduler call
      still works — all with the internet blocked.

> Grid-compliance note: if your install is under a state "emergency backstop" /
> VPP obligation, blocking the inverter's internet may breach your connection
> agreement. Check before step 7.

## Rollback

If anything misbehaves: restore the pre-removal backup, or re-add `foxess-ha`
and disable the UniFi Dest-NAT (logger returns to the cloud). Nothing here is
irreversible before step 7.
