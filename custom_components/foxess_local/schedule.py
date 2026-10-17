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
_TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "schedule_template.json")


def load_template():
    with open(_TEMPLATE_PATH) as f:
        return json.load(f)


def _hhmm(s: str) -> int:
    h, m = (int(x) for x in s.split(":"))
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"bad time {s}")
    return (h << 8) | m


def build_force_charge(start: str, end: str, max_soc: int,
                       fd_pwr: int = 10500, work_mode: str = "ForceCharge"):
    """Return [(reg, [u16,...]), ...] for all 14 writes, group1 of 0xBB8A set."""
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
