"""FoxESS logger<->cloud protocol (reverse-engineered).

Pure, dependency-free helpers shared by the Home Assistant integration and the
standalone test server.

Frame:   MARK MARK | body | crc16_le | END END
  control frames: 7e 7e ... e7 e7   (login / heartbeat)
  data frames:    7f 7f ... f7 f7   (Modbus tunnel + telemetry pushes)
CRC: CRC16/Modbus over `body` (markers and crc excluded), little-endian.

Body: [type:1][id:4][00:1][len:1][payload:len]
  logger control types 2a/2c/2b  -> cloud replies aa/ac/ab (type|0x80)
  cloud read  (func 03/04): type 0x11, payload = 01 <func> <start> <count>
  logger reply to read:     type 0x91, payload = 01 <func> <bytecount> <data>
"""
from __future__ import annotations

import struct
import time

CTRL = 0x7E
DATA = 0x7F
CTRL_END = 0xE7
DATA_END = 0xF7

# Live telemetry: Modbus func 03 (holding) at base 0x7918 (=31000), 0x10 at a time.
# Cover 31000-31063 so the pre-1.33 grid/load mirror registers (31050/31054) are
# also read for cross-checking.
LIVE_BASE = 0x7918
LIVE_BLOCKS = [
    (0x03, 0x7918, 0x10),  # 31000-31015
    (0x03, 0x7928, 0x10),  # 31016-31031
    (0x03, 0x7938, 0x10),  # 31032-31047
    (0x03, 0x7948, 0x10),  # 31048-31063
]

# Holding registers for a FoxESS KH on 1.33+ firmware (nathanmarlor/foxess_modbus
# community map), confirmed against live capture. reg -> (key, name, unit, class, scale).
# NOTE: PV power and grid CT live at 39xxx on 1.33+ (NOT readable via this tunnel);
# solar_power and grid_power are resolved during the correlation session.
KNOWN_HOLDING = {
    0x791E: ("grid_voltage", "Grid Voltage", "V", "voltage", 0.1),         # 31006
    0x7920: ("inverter_power", "Inverter Power", "kW", "power", 0.001),     # 31008 S_WORD
    0x7928: ("load_power", "Load Power", "kW", "power", 0.001),             # 31016 S_WORD (verify)
    0x792C: ("battery_voltage", "Battery Voltage", "V", "voltage", 0.1),   # 31020
    0x792D: ("battery_current", "Battery Current", "A", "current", 0.1),   # 31021 S_WORD
    0x792E: ("battery_power", "Battery Power", "kW", "power", 0.001),       # 31022 S_WORD neg=charge
    0x7930: ("battery_soc", "Battery SoC", "%", "battery", 1),             # 31024 (scale verify: 1 vs 0.1)
    0x7931: ("battery_temperature", "Battery Temperature", "°C", "temperature", 0.1),  # 31025 S_WORD
    0x7932: ("grid_frequency", "Grid Frequency", "Hz", "frequency", 0.1),  # 31026 (candidate)
}


def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def build_frame(mark: int, body: bytes) -> bytes:
    end = CTRL_END if mark == CTRL else DATA_END
    return bytes([mark, mark]) + body + struct.pack("<H", crc16_modbus(body)) + bytes([end, end])


def build_read(txid: int, start: int, count: int, func: int = 0x03) -> bytes:
    modbus = bytes([0x01, func]) + struct.pack(">HH", start, count)
    body = bytes([0x11]) + struct.pack(">I", txid) + bytes([0x00, len(modbus)]) + modbus
    return build_frame(DATA, body)


def control_reply(body: bytes) -> bytes | None:
    """Reply the real cloud would send for a logger control frame, or None."""
    ctype = body[0]
    sess = struct.pack(">I", int(time.time()))  # cloud's 4-byte field ~ unix time
    if ctype == 0x2A:          # login
        p = b"\x01\x01\x01\x00\x00"
        return bytes([0xAA]) + sess + bytes([0x00, len(p)]) + p
    if ctype == 0x2C:          # poll / nothing pending
        return bytes([0xAC]) + sess + bytes([0x00, 0x00])
    if ctype == 0x2B:
        return bytes([0xAB]) + sess + bytes([0x00, 0x01, 0x01])
    return None


def decode_read_response(body: bytes, pending: dict[int, int]):
    """Return (start, [(u16, s16), ...]) or (start, 'EXCEPTION') or None."""
    txid = struct.unpack(">I", body[1:5])[0]
    start = pending.get(txid)
    modbus = body[7:]
    if len(modbus) >= 2 and modbus[1] in (0x83, 0x84):
        return (start, "EXCEPTION")
    if len(modbus) < 3 or modbus[0] != 0x01 or modbus[1] not in (0x03, 0x04):
        return None
    data = modbus[3 : 3 + modbus[2]]
    regs = []
    for i in range(0, len(data) - 1, 2):
        u = struct.unpack(">H", data[i : i + 2])[0]
        regs.append((u, u - 0x10000 if u >= 0x8000 else u))
    return start, regs


def registers_to_values(start: int, regs) -> dict:
    """Map a func-03 block to {key: value}: labelled values + raw r#### diagnostics."""
    out = {}
    for i, (u, s) in enumerate(regs):
        reg = start + i
        if reg in KNOWN_HOLDING:
            key, _name, _unit, _dc, scale = KNOWN_HOLDING[reg]
            out[key] = round(s * scale, 2)
        out[f"r{reg:04x}"] = s
    return out


class FrameReader:
    """Reassemble framed messages from a byte stream."""

    def __init__(self):
        self.buf = bytearray()

    def feed(self, data: bytes):
        self.buf += data
        out = []
        while len(self.buf) >= 2:
            mark = self.buf[0]
            if mark not in (CTRL, DATA) or self.buf[1] != mark:
                self.buf.pop(0)
                continue
            end = CTRL_END if mark == CTRL else DATA_END
            idx = -1
            for i in range(2, len(self.buf) - 1):
                if self.buf[i] == end and self.buf[i + 1] == end:
                    idx = i
                    break
            if idx == -1:
                break
            frame = bytes(self.buf[: idx + 2])
            del self.buf[: idx + 2]
            body = frame[2:-4]
            crc_ok = crc16_modbus(body) == struct.unpack("<H", frame[-4:-2])[0]
            out.append((mark, body, crc_ok))
        return out
