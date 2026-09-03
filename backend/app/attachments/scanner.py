from __future__ import annotations

import socket
import struct
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Protocol

from .conversation_models import MAX_FILE_BYTES

_CLAMD_CHUNK_BYTES = 1024 * 1024
_MAX_REPLY_BYTES = 4096


class ScannerUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("attachment scanner unavailable")


class ScanDisposition(str, Enum):
    CLEAN = "clean"
    INFECTED = "infected"


@dataclass(frozen=True)
class ScanResult:
    disposition: ScanDisposition
    database_version: int
    signature: str | None = field(default=None, repr=False)


class MalwareScanner(Protocol):
    def scan_stream(self, chunks: Iterable[bytes], *, size: int) -> ScanResult: ...


class ClamAVScanner:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 3310,
        *,
        timeout_seconds: float = 5.0,
        max_database_age_seconds: int = 48 * 60 * 60,
        max_file_bytes: int = MAX_FILE_BYTES,
        connect: Callable[..., socket.socket] = socket.create_connection,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if host not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("attachment scanner host invalid")
        if (
            isinstance(port, bool)
            or not isinstance(port, int)
            or not 1 <= port <= 65535
        ):
            raise ValueError("attachment scanner port invalid")
        if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise ValueError("attachment scanner timeout invalid")
        if (
            isinstance(max_database_age_seconds, bool)
            or not isinstance(max_database_age_seconds, int)
            or max_database_age_seconds <= 0
        ):
            raise ValueError("attachment scanner freshness invalid")
        if (
            isinstance(max_file_bytes, bool)
            or not isinstance(max_file_bytes, int)
            or max_file_bytes <= 0
            or max_file_bytes > MAX_FILE_BYTES
        ):
            raise ValueError("attachment scanner size invalid")
        self._address = (host, port)
        self._timeout_seconds = float(timeout_seconds)
        self._max_database_age_seconds = max_database_age_seconds
        self._max_file_bytes = max_file_bytes
        self._connect = connect
        self._now = now

    def __repr__(self) -> str:
        return "ClamAVScanner(address=<redacted>)"

    def scan_stream(self, chunks: Iterable[bytes], *, size: int) -> ScanResult:
        if (
            isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
            or size > self._max_file_bytes
        ):
            raise ScannerUnavailable()
        database_version = self._fresh_database_version()
        try:
            with self._connect(
                self._address, timeout=self._timeout_seconds
            ) as connection:
                connection.settimeout(self._timeout_seconds)
                connection.sendall(b"zINSTREAM\0")
                streamed = 0
                for chunk in chunks:
                    if not isinstance(chunk, bytes):
                        raise ScannerUnavailable()
                    for offset in range(0, len(chunk), _CLAMD_CHUNK_BYTES):
                        selected = chunk[offset : offset + _CLAMD_CHUNK_BYTES]
                        streamed += len(selected)
                        if streamed > size or streamed > self._max_file_bytes:
                            raise ScannerUnavailable()
                        connection.sendall(struct.pack("!I", len(selected)))
                        connection.sendall(selected)
                if streamed != size:
                    raise ScannerUnavailable()
                connection.sendall(struct.pack("!I", 0))
                reply = self._recv_reply(connection)
        except ScannerUnavailable:
            raise
        except (OSError, RuntimeError, ValueError):
            raise ScannerUnavailable() from None

        normalized = reply.rstrip(b"\0\r\n")
        if normalized == b"stream: OK":
            return ScanResult(ScanDisposition.CLEAN, database_version)
        if normalized.startswith(b"stream: ") and normalized.endswith(b" FOUND"):
            signature_bytes = normalized[len(b"stream: ") : -len(b" FOUND")]
            if not signature_bytes or len(signature_bytes) > 255:
                raise ScannerUnavailable()
            try:
                signature = signature_bytes.decode("ascii")
            except UnicodeDecodeError:
                raise ScannerUnavailable() from None
            return ScanResult(
                ScanDisposition.INFECTED,
                database_version,
                signature=signature,
            )
        raise ScannerUnavailable()

    def _fresh_database_version(self) -> int:
        try:
            with self._connect(
                self._address, timeout=self._timeout_seconds
            ) as connection:
                connection.settimeout(self._timeout_seconds)
                connection.sendall(b"zVERSION\0")
                response = self._recv_reply(connection).rstrip(b"\0\r\n")
            parts = response.split(b"/")
            if len(parts) < 3 or not parts[0].startswith(b"ClamAV "):
                raise ScannerUnavailable()
            version = int(parts[1])
            updated = datetime.strptime(
                parts[2].decode("ascii"), "%a %b %d %H:%M:%S %Y"
            ).replace(tzinfo=UTC)
            age = (self._now().astimezone(UTC) - updated).total_seconds()
            if age < 0 or age > self._max_database_age_seconds:
                raise ScannerUnavailable()
            return version
        except ScannerUnavailable:
            raise
        except (OSError, RuntimeError, UnicodeError, ValueError):
            raise ScannerUnavailable() from None

    @staticmethod
    def _recv_reply(connection: socket.socket) -> bytes:
        result = bytearray()
        while len(result) < _MAX_REPLY_BYTES:
            chunk = connection.recv(min(1024, _MAX_REPLY_BYTES - len(result)))
            if not isinstance(chunk, bytes):
                raise ScannerUnavailable()
            if not chunk:
                break
            result.extend(chunk)
            if b"\0" in chunk or b"\n" in chunk:
                break
        if not result or len(result) >= _MAX_REPLY_BYTES:
            raise ScannerUnavailable()
        return bytes(result)
