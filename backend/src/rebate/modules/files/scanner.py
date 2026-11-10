"""ClamAV ``clamd`` INSTREAM client (stdlib socket; the ``clamd`` PyPI client is unmaintained).

Protocol: ``zINSTREAM\\0``, then chunks as 4-byte big-endian length + data, then a zero length; the
reply is ``stream: OK``, ``stream: <signature> FOUND`` or ``... ERROR`` (NUL-terminated).
"""

import socket
import struct
from collections.abc import Iterable
from dataclasses import dataclass

from rebate.core.config import Settings

CHUNK = 64 * 1024


class ScannerError(Exception):
    """clamd unreachable or returned an error: transient, the scan job retries."""


@dataclass(frozen=True)
class ScanResult:
    clean: bool
    signature: str | None = None


def scan(settings: Settings, chunks: Iterable[bytes]) -> ScanResult:
    try:
        with socket.create_connection((settings.clamd_host, settings.clamd_port), timeout=120) as sock:
            sock.sendall(b"zINSTREAM\0")
            for chunk in chunks:
                for i in range(0, len(chunk), CHUNK):
                    part = chunk[i : i + CHUNK]
                    sock.sendall(struct.pack("!I", len(part)) + part)
            sock.sendall(struct.pack("!I", 0))
            reply = b""
            while not reply.endswith(b"\0"):
                data = sock.recv(4096)
                if not data:
                    break
                reply += data
    except OSError as exc:
        raise ScannerError(type(exc).__name__) from None
    text = reply.rstrip(b"\0").decode("utf-8", "replace").strip()
    if text.endswith(" OK"):
        return ScanResult(clean=True)
    if text.endswith(" FOUND"):
        signature = text.removeprefix("stream:").removesuffix("FOUND").strip()
        return ScanResult(clean=False, signature=signature[:200])
    raise ScannerError(f"unexpected clamd reply: {text[:100]}")


def ping(settings: Settings) -> bool:
    try:
        with socket.create_connection((settings.clamd_host, settings.clamd_port), timeout=5) as sock:
            sock.sendall(b"zPING\0")
            return sock.recv(16).startswith(b"PONG")
    except OSError:
        return False
