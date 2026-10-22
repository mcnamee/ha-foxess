"""Asyncio TLS server that impersonates the FoxESS cloud for the local logger.

Accepts the logger's TLS connection (self-signed cert), answers its
login/heartbeat so it stays connected with the internet blocked, polls the live
holding registers, and calls `update_cb(dict)` with decoded telemetry.
"""
from __future__ import annotations

import asyncio
import logging
import ssl
import struct
from datetime import datetime, timedelta

try:  # package import (inside Home Assistant)
    from .protocol import (
        CTRL,
        DATA,
        FrameReader,
        LIVE_BLOCKS,
        build_frame,
        build_read,
        control_reply,
        decode_read_response,
        registers_to_values,
    )
except ImportError:  # standalone run (Mac testing)
    from protocol import (
        CTRL,
        DATA,
        FrameReader,
        LIVE_BLOCKS,
        build_frame,
        build_read,
        control_reply,
        decode_read_response,
        registers_to_values,
    )

_LOGGER = logging.getLogger(__name__)

POLL_INTERVAL = 15  # seconds between live-register poll cycles


def ensure_cert(cert_path: str, key_path: str) -> None:
    """Generate a self-signed cert/key if missing (uses cryptography, bundled in HA)."""
    import os

    if os.path.exists(cert_path) and os.path.exists(key_path):
        return
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "www.foxesscloud.com")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.utcnow() - timedelta(days=1))
        .not_valid_after(datetime.utcnow() + timedelta(days=3650))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("www.foxesscloud.com")]), False
        )
        .sign(key, hashes.SHA256())
    )
    with open(key_path, "wb") as f:
        f.write(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ))
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))


class FoxessServer:
    def __init__(self, port, cert_path, key_path, update_cb):
        self._port = port
        self._cert_path = cert_path
        self._key_path = key_path
        self._update_cb = update_cb
        self._server = None
        self._active_send = None   # set while a logger is connected
        self._write_txid = 0x7000
        self._od_txid = 0x3000     # on-demand read txids
        self._pending_reads = {}   # txid -> asyncio.Future

    async def _request(self, func, start, count):
        """On-demand register read; returns list of u16 or None."""
        if self._active_send is None:
            return None
        self._od_txid = (self._od_txid + 1) & 0xFFFFFFFF
        tx = self._od_txid
        fut = asyncio.get_event_loop().create_future()
        self._pending_reads[tx] = fut
        try:
            await self._active_send(build_read(tx, start, count, func))
            return await asyncio.wait_for(fut, timeout=6)
        except (asyncio.TimeoutError, Exception):  # noqa: BLE001
            self._pending_reads.pop(tx, None)
            return None

    async def read_schedule(self):
        """Read block 0xBB8A back and return {groups, summary} or None."""
        from .schedule import SCHEDULE_READ_CHUNKS, parse_schedule, schedule_summary

        regs = []
        for func, start, count in SCHEDULE_READ_CHUNKS:
            chunk = await self._request(func, start, count)
            if not chunk:
                return None
            regs.extend(chunk)
            await asyncio.sleep(0.2)
        groups = parse_schedule(regs)
        sched = {"groups": groups, "summary": schedule_summary(groups)}
        self._update_cb({"schedule_summary": sched["summary"],
                         "schedule_groups": groups})
        return sched

    async def send_schedule(self, writes, dry_run=True):
        """Send a schedule (list of (reg, [u16,...])) as func-10 writes.

        dry_run=True (default) only builds and logs the frames — nothing is sent
        to the inverter. Pass dry_run=False to actually write.
        Returns a list of human-readable frame summaries.
        """
        from .protocol import build_write  # local import to avoid cycles

        summaries = []
        for reg, vals in writes:
            self._write_txid = (self._write_txid + 1) & 0xFFFFFFFF
            frame = build_write(self._write_txid, reg, vals)
            summaries.append(f"reg=0x{reg:04x} count={len(vals)} bytes={len(frame)}")
        if dry_run:
            _LOGGER.warning("DRY-RUN schedule (%d writes, NOT sent): %s",
                            len(writes), summaries)
            return summaries
        if self._active_send is None:
            raise RuntimeError("logger not connected — cannot write schedule")
        self._write_txid = 0x7000
        for reg, vals in writes:
            self._write_txid = (self._write_txid + 1) & 0xFFFFFFFF
            await self._active_send(build_write(self._write_txid, reg, vals))
            await asyncio.sleep(0.3)
        _LOGGER.warning("schedule WRITTEN to inverter (%d writes)", len(writes))
        # read the schedule back so the result is verifiable without the app
        await asyncio.sleep(0.5)
        sched = await self.read_schedule()
        if sched:
            _LOGGER.warning("schedule read-back: %s", sched["summary"])
        return {"written": len(writes), "schedule": sched}

    async def start(self):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(self._cert_path, self._key_path)
        self._server = await asyncio.start_server(
            self._handle, "0.0.0.0", self._port, ssl=ctx
        )
        _LOGGER.info("FoxESS local server listening on :%s", self._port)

    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        peer = writer.get_extra_info("peername")
        _LOGGER.info("logger connected from %s", peer)
        frames = FrameReader()
        pending: dict[int, int] = {}
        write_lock = asyncio.Lock()

        async def send(data: bytes):
            async with write_lock:
                writer.write(data)
                await writer.drain()

        self._active_send = send

        async def poller():
            txid = 0x1000
            cycle = 0
            await asyncio.sleep(8)
            while not writer.is_closing():
                for func, start, count in LIVE_BLOCKS:
                    txid = (txid + 1) & 0xFFFFFFFF
                    pending[txid] = start
                    try:
                        await send(build_read(txid, start, count, func))
                    except Exception:  # noqa: BLE001
                        return
                    await asyncio.sleep(0.5)
                # refresh the current schedule periodically (first cycle + ~5 min)
                if cycle % 20 == 0:
                    try:
                        await self.read_schedule()
                    except Exception:  # noqa: BLE001
                        pass
                cycle += 1
                await asyncio.sleep(POLL_INTERVAL)

        poll_task = asyncio.create_task(poller())
        try:
            while True:
                data = await reader.read(8192)
                if not data:
                    break
                for mark, body, _crc_ok in frames.feed(data):
                    if mark == CTRL:
                        reply = control_reply(body)
                        if reply is not None:
                            await send(build_frame(CTRL, reply))
                    elif body and body[0] == 0x91:
                        tx = struct.unpack(">I", body[1:5])[0]
                        fut = self._pending_reads.pop(tx, None)
                        if fut is not None:
                            mb = body[7:]
                            regs = []
                            if len(mb) >= 3 and mb[1] in (0x03, 0x04):
                                data = mb[3 : 3 + mb[2]]
                                regs = [int.from_bytes(data[i : i + 2], "big")
                                        for i in range(0, len(data) - 1, 2)]
                            if not fut.done():
                                fut.set_result(regs)
                        else:
                            res = decode_read_response(body, pending)
                            if res and res[1] != "EXCEPTION" and res[0] is not None:
                                self._update_cb(registers_to_values(res[0], res[1]))
        except (asyncio.CancelledError, ConnectionError):
            pass
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("connection error: %s", err)
        finally:
            poll_task.cancel()
            self._active_send = None
            writer.close()
            _LOGGER.info("logger disconnected (%s)", peer)
