"""Build a FoxESS schedule write by template-replay.

A schedule change on this inverter is a fixed sequence of 14 func-10 writes
(captured in schedule_template.json). The ACTIVE schedule lives in block 0xBB8A
as 8 groups of 10 registers. We reproduce the captured sequence verbatim and
substitute only the well-understood fields of group 1 (the force-charge window).

Group (10 registers) layout, confirmed against the console + a known capture:
  reg0 = enable (1)
  reg1 = start  (hour<<8 | minute)
  reg2 = end    (hour<<8 | minute)
  reg3 = (kept from template)
  reg4 = fdSoc<<8 | minSocOnGrid        (kept from template)
  reg5 = maxSoc (charge cap %)
  reg6 = fdPwr  (grid-charge ceiling, W)
  reg7 = 0
  reg8 = workMode (0 SelfUse,1 Feedin,2 Backup,3 ForceCharge,4 ForceDischarge)
  reg9 = 2 (kept)
"""
from __future__ import annotations

import json
import os

SCHEDULE_BLOCK = 0xBB8A
WORK_MODES = {"SelfUse": 0, "Feedin": 1, "Backup": 2,
              "ForceCharge": 3, "ForceDischarge": 4}
WORK_MODE_NAMES = {v: k for k, v in WORK_MODES.items()}
# Reading the 80-reg schedule block back, in func-03 0x10 chunks.
SCHEDULE_READ_CHUNKS = [(0x03, SCHEDULE_BLOCK + i * 0x10, 0x10) for i in range(5)]
_TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "schedule_template.json")


def parse_schedule(regs):
    """regs = 80 u16 values of block 0xBB8A -> list of enabled group dicts."""
    groups = []
    for gi in range(8):
        g = regs[gi * 10: gi * 10 + 10]
        if len(g) < 10 or not g[0]:        # reg0 = enable flag
            continue
        groups.append({
            "group": gi + 1,
            "mode": WORK_MODE_NAMES.get(g[8], f"mode{g[8]}"),
            "start": f"{g[1] >> 8:02d}:{g[1] & 0xFF:02d}",
            "end": f"{g[2] >> 8:02d}:{g[2] & 0xFF:02d}",
            "max_soc": g[5],
            "min_soc": g[4] & 0xFF,
            "fd_pwr": g[6],
        })
    return groups


def schedule_summary(groups):
    if not groups:
        return "no active schedule"
    return "; ".join(
        f"{x['mode']} {x['start']}-{x['end']}→{x['max_soc']}%" for x in groups)


def load_template():
    with open(_TEMPLATE_PATH) as f:
        return json.load(f)


def _hhmm(s: str) -> int:
    h, m = (int(x) for x in s.split(":"))
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"bad time {s}")
    return (h << 8) | m


def set_self_use_max_soc_in_block(regs: list, max_soc: int) -> bool:
    """In a 0xBB8A 80-register block, set maxSoc (offset 5) of each enabled
    Self-Use group. Mutates `regs`; returns True if any group changed."""
    changed = False
    for gi in range(8):
        base = gi * 10
        if len(regs) < base + 10:
            break
        if regs[base] and regs[base + 8] == WORK_MODES["SelfUse"]:  # enabled + SelfUse
            regs[base + 5] = int(max_soc)
            changed = True
    return changed


def build_force_charge(start: str, end: str, max_soc: int,
                       fd_pwr: int = 10500, work_mode: str = "ForceCharge",
                       self_use_max_soc: int | None = None):
    """Return [(reg, [u16,...]), ...] for all 14 writes, group1 of 0xBB8A set.
    If `self_use_max_soc` is given, the Self-Use group's cap is set too, so a
    force-charge write doesn't reset the standard max SoC back to the template's."""
    if work_mode not in WORK_MODES:
        raise ValueError(f"bad work_mode {work_mode}")
    writes = []
    for w in load_template():
        vals = list(w["values"])
        if w["reg"] == SCHEDULE_BLOCK:
            g = vals  # group 1 = vals[0:10]
            g[0] = 1                     # enable
            g[1] = _hhmm(start)
            g[2] = _hhmm(end)
            g[5] = int(max_soc)
            g[6] = int(fd_pwr)
            g[8] = WORK_MODES[work_mode]
            # g[3], g[4] (fdSoc/minSoc), g[7], g[9] kept from the template
            if self_use_max_soc is not None:
                set_self_use_max_soc_in_block(vals, int(self_use_max_soc))
        writes.append((w["reg"], vals))
    return writes


def _self_test():
    """The template IS a known schedule (13:30-14:00, ForceCharge, maxSoc 80).
    Rebuilding with those params must reproduce block 0xBB8A exactly."""
    tmpl = {w["reg"]: w["values"] for w in load_template()}
    built = dict(build_force_charge("13:30", "14:00", 80))
    ok = built[SCHEDULE_BLOCK] == tmpl[SCHEDULE_BLOCK]
    g = built[SCHEDULE_BLOCK][:10]
    return ok, g


if __name__ == "__main__":
    ok, g = _self_test()
    print("group1 rebuilt:", [hex(x) for x in g])
    print("byte-matches captured 0xBB8A:", ok)
