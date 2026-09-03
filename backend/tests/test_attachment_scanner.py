from __future__ import annotations

import hashlib
import io
import struct
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from app.attachments.scanner import (
    ClamAVScanner,
    ScanDisposition,
    ScannerUnavailable,
)
from app.attachments.validation import OpenedObject, ValidationResult
from app.attachments.worker import (
    AttachmentProcessor,
    ProcessingJob,
    ProcessingTransitionError,
    ReconciliationStatus,
)
from app.attachments.worker_runtime import (
    AttachmentProcessingRepository,
    S3ProcessingObjectStore,
    run,
)
from app.execution_relay.content_crypto import SealedContent

FIXTURES = Path(__file__).parent / "fixtures" / "conversation_attachments"
NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
FRESH_VERSION = b"ClamAV 1.4.2/28000/Wed Sep  2 12:00:00 2026\0"


class BoundedChunks:
    def __init__(self, *chunks: bytes, on_deadline=None) -> None:
        self.chunks = chunks
        self.on_deadline = on_deadline

    def iter_chunks_until(self, deadline, monotonic):
        if self.on_deadline:
            self.on_deadline(deadline, monotonic)
        yield from self.chunks


def bounded(*chunks: bytes) -> BoundedChunks:
    return BoundedChunks(*chunks)


class FakeSocket:
    def __init__(self, response: bytes | BaseException | list[bytes]) -> None:
        self.responses = list(response) if isinstance(response, list) else [response]
        self.sent = bytearray()
        self.timeout = None
        self.timeouts = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout
        self.timeouts.append(timeout)

    def sendall(self, data: bytes) -> None:
        self.sent.extend(data)

    def recv(self, _size: int) -> bytes:
        if not self.responses:
            return b""
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class SocketFactory:
    def __init__(self, *responses: bytes | BaseException | list[bytes]) -> None:
        self.sockets = [FakeSocket(response) for response in responses]
        self.sockets_used: list[FakeSocket] = []

    def __call__(self, address, timeout):
        assert address == ("127.0.0.1", 3310)
        assert 0 < timeout <= 2.0
        selected = self.sockets.pop(0)
        self.sockets_used.append(selected)
        return selected


def scanner_with(
    *responses: bytes | BaseException | list[bytes],
    monotonic=lambda: 0.0,
) -> tuple[ClamAVScanner, SocketFactory]:
    factory = SocketFactory(*responses)
    scanner = ClamAVScanner(
        host="127.0.0.1",
        port=3310,
        timeout_seconds=2.0,
        max_database_age_seconds=48 * 60 * 60,
        connect=factory,
        now=lambda: NOW,
        monotonic=monotonic,
    )
    return scanner, factory


def test_clamav_uses_bounded_instream_protocol_for_clean_content() -> None:
    scanner, factory = scanner_with(FRESH_VERSION, b"stream: OK\0")
    chunks = bounded(b"abc", b"def")

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

    result = scanner.scan_stream(bounded(eicar), size=len(eicar))

    assert result.disposition is ScanDisposition.INFECTED
    assert "Eicar" not in repr(result)


@pytest.mark.parametrize(
    "responses",
    (
        (OSError("daemon host detail"),),
        (TimeoutError("secret timeout"),),
        (b"unexpected reply\0",),
        (b"ClamAV 1.4.2/28000/Sun Aug  2 12:00:00 2026\0",),
        (FRESH_VERSION, b"stream: protocol ERROR\0"),
    ),
)
def test_clamav_failures_and_stale_database_fail_closed(responses) -> None:
    scanner, _factory = scanner_with(*responses)

    with pytest.raises(ScannerUnavailable) as captured:
        scanner.scan_stream(bounded(b"clean"), size=5)

    assert str(captured.value) == "attachment scanner unavailable"
    assert "secret" not in repr(captured.value)


def test_clamav_accepts_fragmented_replies_only_when_nul_terminated() -> None:
    scanner, _factory = scanner_with(
        [b"ClamAV 1.4.2/", b"28000/Wed Sep  2 12:00:00 2026", b"\0"],
        [b"stream", b": O", b"K", b"\0"],
    )

    result = scanner.scan_stream(bounded(b"clean"), size=5)

    assert result.disposition is ScanDisposition.CLEAN


@pytest.mark.parametrize(
    "reply",
    (
        b"stream: OK",
        b"stream: OK\n",
        b"stream: OK\0trailing",
    ),
)
def test_clamav_rejects_missing_newline_or_nonfinal_nul_terminators(reply: bytes) -> None:
    scanner, _factory = scanner_with(FRESH_VERSION, reply)

    with pytest.raises(ScannerUnavailable):
        scanner.scan_stream(bounded(b"clean"), size=5)


def test_clamav_uses_one_total_deadline_across_source_and_protocol() -> None:
    class Clock:
        value = 0.0

        def __call__(self):
            return self.value

    clock = Clock()
    scanner, _factory = scanner_with(FRESH_VERSION, b"stream: OK\0", monotonic=clock)

    def slow_source(_deadline, _monotonic):
        clock.value = 1.25

    scanner._timeout_seconds = 1.0
    with pytest.raises(ScannerUnavailable):
        scanner.scan_stream(BoundedChunks(b"clean", on_deadline=slow_source), size=5)


def test_clamav_bounds_slow_drip_reply_by_total_deadline() -> None:
    class DripClock:
        value = 0.0

        def __call__(self):
            current = self.value
            self.value += 0.16
            return current

    clock = DripClock()
    scanner, _factory = scanner_with(
        [b"Clam", b"AV 1.4.2/", b"28000/", b"Wed Sep  2 12:00:00 2026", b"\0"],
        b"stream: OK\0",
        monotonic=clock,
    )
    scanner._timeout_seconds = 0.5

    with pytest.raises(ScannerUnavailable):
        scanner.scan_stream(bounded(b"clean"), size=5)


def test_clamav_rejects_an_unbounded_source_without_starting_a_thread() -> None:
    scanner, _factory = scanner_with(
        FRESH_VERSION,
        b"stream: OK\0",
        monotonic=time.monotonic,
    )
    before = {thread.ident for thread in threading.enumerate()}
    touched = False

    def blocked_source():
        nonlocal touched
        touched = True
        threading.Event().wait()
        yield b"clean"

    with pytest.raises(ScannerUnavailable):
        scanner.scan_stream(blocked_source(), size=5)

    assert touched is False
    assert {thread.ident for thread in threading.enumerate()} == before


class FakeRepository:
    def __init__(self, job: ProcessingJob) -> None:
        self.job = job
        self.results: list[tuple[str, str | None]] = []

    def claim(self, _worker_id: str):
        job, self.job = self.job, None
        return job

    def record_result(self, _job, state, reason, *, validation=None):
        self.results.append((state, reason))

    def reconcile_result(self, _job, state):
        return (
            ReconciliationStatus.COMMITTED
            if self.results and self.results[-1][0] == state
            else ReconciliationStatus.RUNNING
        )


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


class CleanScanner:
    def scan_stream(self, chunks, *, size):
        assert sum(
            len(chunk) for chunk in chunks.iter_chunks_until(float("inf"), lambda: 0)
        ) == size
        from app.attachments.scanner import ScanResult

        return ScanResult(ScanDisposition.CLEAN, 28000)


@pytest.mark.asyncio
async def test_scan_rejects_same_size_object_replacement_before_ready() -> None:
    expected = b"clean"
    replacement = b"other"
    job = ProcessingJob(
        uuid4(), uuid4(), "scan", None, "opaque", len(expected), hashlib.sha256(expected).digest(), "text/plain", "etag:\"v1\""
    )
    repository = FakeRepository(job)

    class ReplacedStore:
        def open(self, _object_ref, immutable_locator):
            assert immutable_locator == 'etag:"v1"'
            return OpenedObject(io.BytesIO(replacement), len(replacement))

    processor = AttachmentProcessor(
        repository=repository,
        object_store=ReplacedStore(),
        validator=None,
        scanner=CleanScanner(),
        derivatives=ForbiddenBuilder(),
        worker_id="attachment-worker.1",
    )

    assert await processor.process_next() is True
    assert repository.results == [("rejected", "integrity_mismatch")]


@pytest.mark.asyncio
async def test_committed_scan_result_response_loss_is_reconciled_without_retry() -> None:
    data = b"clean"
    job = ProcessingJob(
        uuid4(), uuid4(), "scan", None, "opaque", len(data), hashlib.sha256(data).digest(), "text/plain"
    )

    class CommitLossRepository(FakeRepository):
        def record_result(self, job, state, reason, *, validation=None):
            super().record_result(job, state, reason, validation=validation)
            raise RuntimeError("response lost secret")

    repository = CommitLossRepository(job)
    processor = AttachmentProcessor(
        repository=repository,
        object_store=FakeStore(),
        validator=None,
        scanner=CleanScanner(),
        derivatives=ForbiddenBuilder(),
        worker_id="attachment-worker.1",
    )

    assert await processor.process_next() is True
    assert repository.results == [("ready", None)]


@pytest.mark.asyncio
async def test_unknown_scan_result_never_issues_a_second_transition() -> None:
    data = b"clean"
    job = ProcessingJob(
        uuid4(), uuid4(), "scan", None, "opaque", len(data), hashlib.sha256(data).digest(), "text/plain"
    )

    class UnknownRepository(FakeRepository):
        def record_result(self, job, state, reason, *, validation=None):
            self.results.append((state, reason))
            raise RuntimeError("response uncertain")

        def reconcile_result(self, _job, _state):
            return ReconciliationStatus.UNKNOWN

    repository = UnknownRepository(job)
    processor = AttachmentProcessor(
        repository=repository,
        object_store=FakeStore(),
        validator=None,
        scanner=CleanScanner(),
        derivatives=ForbiddenBuilder(),
        worker_id="attachment-worker.1",
    )

    with pytest.raises(ProcessingTransitionError):
        await processor.process_next()
    assert repository.results == [("ready", None)]


@pytest.mark.asyncio
async def test_stream_close_failure_cannot_override_persisted_terminal_result() -> None:
    data = b"clean"
    job = ProcessingJob(
        uuid4(), uuid4(), "scan", None, "opaque", len(data), hashlib.sha256(data).digest(), "text/plain"
    )
    repository = FakeRepository(job)

    class CloseFailure(io.BytesIO):
        def close(self):
            super().close()
            raise OSError("close secret")

    class Store:
        def open(self, _object_ref):
            return OpenedObject(CloseFailure(data), len(data))

    processor = AttachmentProcessor(
        repository=repository,
        object_store=Store(),
        validator=None,
        scanner=CleanScanner(),
        derivatives=ForbiddenBuilder(),
        worker_id="attachment-worker.1",
    )

    assert await processor.process_next() is True
    assert repository.results == [("ready", None)]


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
                "attempt_token": uuid4(),
            }
        return {
            "object_ref_ciphertext": b"c" * 29,
            "object_ref_key_version": 1,
            "size_bytes": 5,
            "sha256": b"d" * 32,
            "detected_mime": None,
            "immutable_locator": None,
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
    repository.record_result(
        job,
        "scanning",
        None,
        validation=ValidationResult(
            "text/plain",
            5,
            b"d" * 32,
            {"coverage": "metadata_only", "download": True, "inline_preview": False},
            "version:immutable-v1",
        ),
    )

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
    result_params = next(
        params
        for query, params in connection.queries
        if "record_attachment_processing_result_v64" in query
    )
    assert result_params[1] == job.attempt_token
    assert result_params[-1] == "version:immutable-v1"
    assert "opaque-object" not in repr(repository)


@pytest.mark.parametrize(
    ("database_token_matches", "expected"),
    ((True, ReconciliationStatus.RUNNING), (False, ReconciliationStatus.UNKNOWN)),
)
def test_runtime_derivative_reconciliation_is_attempt_scoped_without_a_row(
    database_token_matches, expected
) -> None:
    attempt = uuid4()
    job = ProcessingJob(
        uuid4(), uuid4(), "derive", "thumbnail", "opaque", 5, b"d" * 32,
        "image/png", "version:v1", attempt,
    )

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, query, params=None):
            assert "left join platform_attachments.derivatives" in query.lower()
            return self

        def fetchone(self):
            return {
                "derivative_id": None,
                "size_bytes": None,
                "sha256": None,
                "job_state": "running",
                "attempt_token": attempt if database_token_matches else uuid4(),
            }

    repository = AttachmentProcessingRepository(
        "postgresql://platform_brain_worker@localhost/agent_platform_control",
        content_codec=FakeCodec(),
        connect=lambda *_args, **_kwargs: Connection(),
    )

    assert repository.reconcile_derivative(
        job, type("Stored", (), {"size_bytes": 5, "sha256": b"d" * 32})()
    ) is expected


@pytest.mark.parametrize(
    ("head_extra", "expected_binding"),
    (
        ({"VersionId": "immutable-v1", "ETag": '"etag"'}, ("VersionId", "immutable-v1")),
        ({"ETag": '"etag"'}, ("IfMatch", '"etag"')),
    ),
)
def test_s3_validation_open_returns_persistable_identity_and_prefers_version(
    head_extra, expected_binding
) -> None:
    class Client:
        get_args = None

        def head_object(self, **_kwargs):
            return {"ContentLength": 5, **head_extra}

        def get_object(self, **kwargs):
            self.get_args = kwargs
            return {"ContentLength": 5, "Body": io.BytesIO(b"clean")}

    client = Client()
    source = S3ProcessingObjectStore(client, "bucket").open("opaque", None)

    assert source.stream.read() == b"clean"
    assert client.get_args[expected_binding[0]] == expected_binding[1]
    expected_prefix = "version:" if expected_binding[0] == "VersionId" else "etag:"
    assert source.immutable_locator.startswith(expected_prefix)


@pytest.mark.parametrize(
    ("locator", "binding"),
    (("version:v1", ("VersionId", "v1")), ("etag:\"old\"", ("IfMatch", '"old"'))),
)
def test_s3_future_open_consumes_persisted_immutable_locator(locator, binding) -> None:
    class Client:
        args = None

        def head_object(self, **_kwargs):
            raise AssertionError("future read must not rebind to current object")

        def get_object(self, **kwargs):
            self.args = kwargs
            return {"ContentLength": 5, "Body": io.BytesIO(b"clean")}

    client = Client()
    source = S3ProcessingObjectStore(client, "bucket").open("opaque", locator)

    assert client.args[binding[0]] == binding[1]
    assert source.immutable_locator == locator


def test_s3_etag_overwrite_fails_closed_without_returning_new_bytes() -> None:
    class Client:
        def get_object(self, **kwargs):
            assert kwargs["IfMatch"] == '"old"'
            raise RuntimeError("PreconditionFailed new-object-secret")

    with pytest.raises(Exception, match="attachment worker unavailable") as captured:
        S3ProcessingObjectStore(Client(), "bucket").open("opaque", 'etag:"old"')
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is not None  # suppressed adapter context is never rendered


def test_s3_stream_read_timeout_is_applied_from_clam_deadline() -> None:
    class Body(io.BytesIO):
        def __init__(self, data):
            super().__init__(data)
            self.timeouts = []

        def set_socket_timeout(self, timeout):
            self.timeouts.append(timeout)

    body = Body(b"clean")

    class Client:
        def get_object(self, **_kwargs):
            return {"ContentLength": 5, "Body": body}

    source = S3ProcessingObjectStore(Client(), "bucket").open("opaque", "version:v1")
    list(source.iter_chunks_until(2.0, lambda: 0.5))
    assert body.timeouts == [pytest.approx(1.5), pytest.approx(1.5)]


@pytest.mark.asyncio
async def test_worker_loop_survives_job_failure_with_bounded_backoff() -> None:
    class Processor:
        calls = 0

        async def process_next(self):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("candidate secret")
            return False

    processor = Processor()
    delays = []

    async def fake_sleep(delay):
        delays.append(delay)

    await run(processor, sleep=fake_sleep, max_iterations=2)

    assert processor.calls == 2
    assert delays == [1.0, 1.0]
