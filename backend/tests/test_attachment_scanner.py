from __future__ import annotations

import io
import struct
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from app.attachments.scanner import (
    ClamAVScanner,
    ScanDisposition,
    ScannerUnavailable,
)
from app.attachments.validation import OpenedObject
from app.attachments.worker import AttachmentProcessor, ProcessingJob
from app.attachments.worker_runtime import AttachmentProcessingRepository
from app.execution_relay.content_crypto import SealedContent

FIXTURES = Path(__file__).parent / "fixtures" / "conversation_attachments"
NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
FRESH_VERSION = b"ClamAV 1.4.2/28000/Wed Sep  2 12:00:00 2026\n"


class FakeSocket:
    def __init__(self, response: bytes | BaseException) -> None:
        self.response = response
        self.sent = bytearray()
        self.timeout = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def sendall(self, data: bytes) -> None:
        self.sent.extend(data)

    def recv(self, _size: int) -> bytes:
        if isinstance(self.response, BaseException):
            raise self.response
        response, self.response = self.response, b""
        return response


class SocketFactory:
    def __init__(self, *responses: bytes | BaseException) -> None:
        self.sockets = [FakeSocket(response) for response in responses]
        self.sockets_used: list[FakeSocket] = []

    def __call__(self, address, timeout):
        assert address == ("127.0.0.1", 3310)
        assert timeout == 2.0
        selected = self.sockets.pop(0)
        self.sockets_used.append(selected)
        return selected


def scanner_with(
    *responses: bytes | BaseException,
) -> tuple[ClamAVScanner, SocketFactory]:
    factory = SocketFactory(*responses)
    scanner = ClamAVScanner(
        host="127.0.0.1",
        port=3310,
        timeout_seconds=2.0,
        max_database_age_seconds=48 * 60 * 60,
        connect=factory,
        now=lambda: NOW,
    )
    return scanner, factory


def test_clamav_uses_bounded_instream_protocol_for_clean_content() -> None:
    scanner, factory = scanner_with(FRESH_VERSION, b"stream: OK\0")
    chunks = (b"abc", b"def")

    result = scanner.scan_stream(chunks, size=6)

    assert result.disposition is ScanDisposition.CLEAN
    assert factory.sockets == []  # all connections consumed
    version_socket, scan_socket = factory.sockets_used
    assert version_socket.sent == b"zVERSION\0"
    assert scan_socket.sent == (
        b"zINSTREAM\0"
        + struct.pack("!I", 3)
        + b"abc"
        + struct.pack("!I", 3)
        + b"def"
        + struct.pack("!I", 0)
    )
    assert version_socket.timeout == scan_socket.timeout == 2.0


def test_clamav_reports_eicar_as_infected_without_exposing_signature() -> None:
    scanner, _factory = scanner_with(FRESH_VERSION, b"stream: Eicar-Signature FOUND\0")
    eicar = (FIXTURES / "eicar.txt").read_bytes()

    result = scanner.scan_stream((eicar,), size=len(eicar))

    assert result.disposition is ScanDisposition.INFECTED
    assert "Eicar" not in repr(result)


@pytest.mark.parametrize(
    "responses",
    (
        (OSError("daemon host detail"),),
        (TimeoutError("secret timeout"),),
        (b"unexpected reply\0",),
        (b"ClamAV 1.4.2/28000/Sun Aug  2 12:00:00 2026\n",),
        (FRESH_VERSION, b"stream: protocol ERROR\0"),
    ),
)
def test_clamav_failures_and_stale_database_fail_closed(responses) -> None:
    scanner, _factory = scanner_with(*responses)

    with pytest.raises(ScannerUnavailable) as captured:
        scanner.scan_stream((b"clean",), size=5)

    assert str(captured.value) == "attachment scanner unavailable"
    assert "secret" not in repr(captured.value)


class FakeRepository:
    def __init__(self, job: ProcessingJob) -> None:
        self.job = job
        self.results: list[tuple[str, str | None]] = []

    def claim(self, _worker_id: str):
        job, self.job = self.job, None
        return job

    def record_result(self, _job, state, reason, *, validation=None):
        self.results.append((state, reason))


class FakeStore:
    def open(self, _object_ref: str) -> OpenedObject:
        return OpenedObject(io.BytesIO(b"clean"), 5)


class UnavailableScanner:
    def scan_stream(self, _chunks, *, size):
        raise ScannerUnavailable()


class ForbiddenBuilder:
    def build(self, *_args):
        raise AssertionError("derivative parser ran before a clean scan")


@pytest.mark.asyncio
async def test_scanner_unavailable_retries_and_never_marks_ready_or_parses() -> None:
    repository = FakeRepository(
        ProcessingJob(uuid4(), uuid4(), "scan", None, "opaque", 5, b"d" * 32, None)
    )
    processor = AttachmentProcessor(
        repository=repository,
        object_store=FakeStore(),
        validator=None,
        scanner=UnavailableScanner(),
        derivatives=ForbiddenBuilder(),
        worker_id="attachment-worker.1",
    )

    assert await processor.process_next() is True
    assert repository.results == [("retry", "scanner_unavailable")]


class FakeCodec:
    connection = None

    def unseal_json(self, subject, sealed: SealedContent):
        if self.connection is not None:
            assert self.connection.entered is True
        assert subject.endswith(":object-ref")
        assert isinstance(sealed, SealedContent)
        return {"object_ref": "opaque-object"}


class FakeConnection:
    def __init__(self) -> None:
        self.queries: list[tuple[str, object]] = []
        self.entered = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, *_args):
        self.entered = False

    def execute(self, query, params=None):
        self.queries.append((query, params))
        return self

    def fetchone(self):
        query = self.queries[-1][0]
        if "claim_attachment_processing_job_v64" in query:
            return {
                "processing_job_id": uuid4(),
                "attachment_id": uuid4(),
                "job_kind": "validate",
                "derivative_kind": None,
            }
        return {
            "object_ref_ciphertext": b"c" * 29,
            "object_ref_key_version": 1,
            "size_bytes": 5,
            "sha256": b"d" * 32,
            "detected_mime": None,
            "write_attempt_id": None,
        }


def test_runtime_repository_claims_and_mutates_only_through_protected_functions() -> (
    None
):
    connection = FakeConnection()
    codec = FakeCodec()
    codec.connection = connection
    repository = AttachmentProcessingRepository(
        "postgresql://platform_brain_worker@localhost/agent_platform_control",
        content_codec=codec,
        connect=lambda *_args, **_kwargs: connection,
    )

    job = repository.claim("worker.1")
    assert job is not None
    repository.record_result(job, "retry", "scanner_unavailable")

    statements = [query.lower() for query, _params in connection.queries]
    assert any("claim_attachment_processing_job_v64" in query for query in statements)
    assert any(
        "record_attachment_processing_result_v64" in query for query in statements
    )
    assert not any(
        token in query
        for query in statements
        for token in ("insert into", "update ", "delete from")
    )
    assert "opaque-object" not in repr(repository)
