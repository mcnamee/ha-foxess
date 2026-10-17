"""Asyncio TLS server that impersonates the FoxESS cloud for the local logger.

Accepts the logger's TLS connection (self-signed cert), answers its
login/heartbeat so it stays connected with the internet blocked, polls the live
holding registers, and calls `update_cb(dict)` with decoded telemetry.
"""
from __future__ import annotations

import asyncio
import logging
import ssl
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

        async def poller():
            txid = 0x1000
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
                        res = decode_read_response(body, pending)
                        if res and res[1] != "EXCEPTION" and res[0] is not None:
                            values = registers_to_values(res[0], res[1])
                            self._update_cb(values)
        except (asyncio.CancelledError, ConnectionError):
            pass
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("connection error: %s", err)
        finally:
            poll_task.cancel()
            writer.close()
            _LOGGER.info("logger disconnected (%s)", peer)
