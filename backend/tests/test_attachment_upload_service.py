from __future__ import annotations

import hashlib
import io
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.attachments.conversation_models import (
    MAX_FILE_BYTES,
    AttachmentRecord,
    BeginUpload,
    UploadRecord,
    WriteAttempt,
    WriteReconciliation,
)
from app.attachments.object_writer import (
    AttachmentObjectWriter,
    AttachmentObjectWriterError,
    AttachmentObjectWriterSizeMismatch,
)
from app.attachments.upload_service import (
    AttachmentUploadConflict,
    AttachmentUploadService,
)
from botocore.exceptions import EndpointConnectionError


class GeneratedStream:
    def __init__(self, size: int, fill: bytes = b"x") -> None:
        self.remaining = size
        self.fill = fill
        self.largest_read = 0

    def read(self, size: int = -1) -> bytes:
        assert 0 < size <= 1024 * 1024
        self.largest_read = max(self.largest_read, size)
        selected = min(size, self.remaining)
        self.remaining -= selected
        return self.fill * selected


class StreamingS3:
    def __init__(self, *, fail_once: bool = False) -> None:
        self.fail_once = fail_once
        self.puts: list[tuple[str, str, int]] = []
        self.deletes: list[tuple[str, str]] = []

    def put_object(self, *, Bucket, Key, Body, ContentLength):
        total = 0
        while chunk := Body.read(1024 * 1024):
            total += len(chunk)
            if self.fail_once:
                self.fail_once = False
                raise OSError("partial transport failure")
        self.puts.append((Bucket, Key, total))
        return {"ETag": "opaque"}

    def delete_object(self, *, Bucket, Key):
        self.deletes.append((Bucket, Key))


def test_object_writer_streams_fifty_mb_and_calculates_sha256() -> None:
    s3 = StreamingS3()
    writer = AttachmentObjectWriter(s3, "private-attachments")
    stream = GeneratedStream(MAX_FILE_BYTES)

    receipt = writer.put_stream("objects/random", stream, MAX_FILE_BYTES)

    expected = hashlib.sha256()
    chunk = b"x" * (1024 * 1024)
    for _ in range(MAX_FILE_BYTES // len(chunk)):
        expected.update(chunk)
    assert receipt.size_bytes == MAX_FILE_BYTES
    assert receipt.sha256 == expected.digest()
    assert stream.largest_read <= 1024 * 1024
    assert s3.puts == [("private-attachments", "objects/random", MAX_FILE_BYTES)]


def test_object_writer_rejects_extra_bytes_and_deletes_written_object() -> None:
    s3 = StreamingS3()
    writer = AttachmentObjectWriter(s3, "private-attachments")

    with pytest.raises(AttachmentObjectWriterSizeMismatch):
        writer.put_stream("objects/random", io.BytesIO(b"abcd"), 3)

    assert s3.deletes == [("private-attachments", "objects/random")]


def test_object_writer_cleans_partial_failure_and_delete_is_idempotent() -> None:
    s3 = StreamingS3(fail_once=True)
    writer = AttachmentObjectWriter(s3, "private-attachments")

    with pytest.raises(AttachmentObjectWriterError, match="write failed"):
        writer.put_stream("objects/random", io.BytesIO(b"payload"), 7)
    writer.delete("objects/random")

    assert s3.deletes == [
        ("private-attachments", "objects/random"),
        ("private-attachments", "objects/random"),
    ]


def test_storage_error_redacts_endpoint_object_key_and_cause() -> None:
    endpoint = "http://storage-secret.invalid:9000"
    object_ref = "private-object-token"

    class FailedS3:
        def put_object(self, **_kwargs):
            raise EndpointConnectionError(endpoint_url=endpoint)

        def delete_object(self, **_kwargs):
            raise EndpointConnectionError(endpoint_url=endpoint)

    writer = AttachmentObjectWriter(FailedS3(), "private-attachments")

    with pytest.raises(AttachmentObjectWriterError) as captured:
        writer.put_stream(object_ref, io.BytesIO(b"payload"), 7)

    rendered = str(captured.value)
    assert captured.value.__cause__ is None
    for protected in (endpoint, object_ref, "storage-secret", "private-object"):
        assert protected not in rendered


class MemoryRepository:
    def __init__(self) -> None:
        self.owner_id = uuid4()
        self.upload = UploadRecord(
            upload_id=uuid4(),
            attachment_id=uuid4(),
            owner_id=self.owner_id,
            conversation_id=uuid4(),
            original_name="candidate.pdf",
            declared_mime="application/pdf",
            declared_size=7,
            expires_at=datetime.now(UTC) + timedelta(hours=24),
            state="uploading",
            actual_size=None,
            sha256=None,
        )
        self.attempts: list[WriteAttempt] = []
        self.finalize_fail_once = False
        self.commit_then_raise = False
        self.reconcile_unavailable = False
        self.cleanup_safe = True
        self.complete_calls = 0
        self.abandon_calls = 0
        self.abandon_unavailable = False
        self.attachment: AttachmentRecord | None = None

    def create_upload(self, owner_id, conversation_id, original_name, declared_mime, declared_size):
        assert owner_id == self.owner_id
        self.upload = replace(
            self.upload,
            conversation_id=conversation_id,
            original_name=original_name,
            declared_mime=declared_mime,
            declared_size=declared_size,
        )
        return self.upload

    def upload_for_owner(self, owner_id, upload_id):
        if owner_id != self.owner_id or upload_id != self.upload.upload_id:
            raise RuntimeError("not found")
        return self.upload

    def claim_write(self, owner_id, upload_id):
        self.upload_for_owner(owner_id, upload_id)
        attempt = WriteAttempt(
            attempt_id=uuid4(),
            upload=self.upload,
            object_ref=f"objects/random-{len(self.attempts)}",
            lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        self.attempts.append(attempt)
        return attempt

    def complete_upload(self, owner_id, upload_id, attempt_id, actual_size, sha256):
        self.complete_calls += 1
        self.upload = replace(
            self.upload,
            state="validating",
            actual_size=actual_size,
            sha256=sha256,
        )
        self.attachment = AttachmentRecord(
            attachment_id=self.upload.attachment_id,
            owner_id=owner_id,
            conversation_id=self.upload.conversation_id,
            original_name=self.upload.original_name,
            declared_mime=self.upload.declared_mime,
            detected_mime=None,
            size_bytes=actual_size,
            sha256=sha256,
            state="validating",
            created_at=datetime.now(UTC),
            retained_until=datetime.now(UTC) + timedelta(days=365),
        )
        if self.commit_then_raise:
            self.commit_then_raise = False
            raise RuntimeError("ambiguous database commit")
        if self.finalize_fail_once:
            self.finalize_fail_once = False
            self.attachment = None
            self.upload = replace(
                self.upload, state="uploading", actual_size=None, sha256=None
            )
            raise RuntimeError("database unavailable")
        return self.attachment

    def abandon_write(self, owner_id, upload_id, attempt_id):
        self.upload_for_owner(owner_id, upload_id)
        self.abandon_calls += 1
        if self.abandon_unavailable:
            raise RuntimeError("database unavailable with private token")

    def reconcile_write(self, owner_id, upload_id, attempt_id, actual_size, sha256):
        self.upload_for_owner(owner_id, upload_id)
        if self.reconcile_unavailable:
            raise RuntimeError("database still unavailable")
        if (
            self.attachment is not None
            and self.attachment.size_bytes == actual_size
            and self.attachment.sha256 == sha256
        ):
            return WriteReconciliation(self.attachment, cleanup_safe=False)
        return WriteReconciliation(None, cleanup_safe=self.cleanup_safe)

    def completed_attachment(self, owner_id, upload_id):
        self.upload_for_owner(owner_id, upload_id)
        if self.attachment is None:
            raise AttachmentUploadConflict("upload incomplete")
        return self.attachment


def test_service_rejects_oversize_before_repository_or_s3() -> None:
    repository = MemoryRepository()
    s3 = StreamingS3()
    service = AttachmentUploadService(
        repository, AttachmentObjectWriter(s3, "private-attachments")
    )

    with pytest.raises(AttachmentUploadConflict, match="50 MB"):
        service.begin(
            repository.owner_id,
            BeginUpload(
                conversation_id=uuid4(),
                original_name="too-large.pdf",
                declared_mime="application/pdf",
                declared_size=MAX_FILE_BYTES + 1,
            ),
        )

    assert s3.puts == []


def test_service_rejects_declared_content_length_mismatch_before_write() -> None:
    repository = MemoryRepository()
    s3 = StreamingS3()
    service = AttachmentUploadService(
        repository, AttachmentObjectWriter(s3, "private-attachments")
    )

    with pytest.raises(AttachmentUploadConflict, match="content length"):
        service.write(
            repository.owner_id,
            repository.upload.upload_id,
            io.BytesIO(b"short"),
            5,
        )

    assert s3.puts == []
    assert repository.complete_calls == 0


def test_failed_object_write_with_unavailable_release_reports_retry_pending() -> None:
    repository = MemoryRepository()
    repository.abandon_unavailable = True
    s3 = StreamingS3(fail_once=True)
    service = AttachmentUploadService(
        repository, AttachmentObjectWriter(s3, "private-attachments")
    )

    with pytest.raises(AttachmentUploadConflict, match="retry pending") as captured:
        service.write(
            repository.owner_id,
            repository.upload.upload_id,
            io.BytesIO(b"payload"),
            7,
        )

    assert captured.value.__cause__ is None
    assert "private token" not in str(captured.value)
    assert repository.abandon_calls == 1


def test_authoritatively_superseded_finalize_failure_deletes_only_its_object() -> None:
    repository = MemoryRepository()
    repository.finalize_fail_once = True
    s3 = StreamingS3()
    service = AttachmentUploadService(
        repository, AttachmentObjectWriter(s3, "private-attachments")
    )

    with pytest.raises(AttachmentUploadConflict, match="finalize"):
        service.write(
            repository.owner_id,
            repository.upload.upload_id,
            io.BytesIO(b"payload"),
            7,
        )
    assert s3.deletes == [("private-attachments", "objects/random-0")]
    assert repository.abandon_calls == 0


def test_ambiguous_post_commit_reconciles_without_deleting_canonical_object() -> None:
    repository = MemoryRepository()
    repository.commit_then_raise = True
    s3 = StreamingS3()
    service = AttachmentUploadService(
        repository, AttachmentObjectWriter(s3, "private-attachments")
    )

    result = service.write(
        repository.owner_id,
        repository.upload.upload_id,
        io.BytesIO(b"payload"),
        7,
    )

    assert result.state == "validating"
    assert s3.deletes == []
    assert repository.abandon_calls == 0


def test_unreadable_reconciliation_never_deletes_possibly_canonical_object() -> None:
    repository = MemoryRepository()
    repository.finalize_fail_once = True
    repository.reconcile_unavailable = True
    s3 = StreamingS3()
    service = AttachmentUploadService(
        repository, AttachmentObjectWriter(s3, "private-attachments")
    )

    with pytest.raises(AttachmentUploadConflict) as captured:
        service.write(
            repository.owner_id,
            repository.upload.upload_id,
            io.BytesIO(b"payload"),
            7,
        )

    assert captured.value.__cause__ is None
    assert s3.deletes == []
    assert repository.abandon_calls == 0


def test_concurrent_writers_do_not_share_keys_or_delete_the_winner() -> None:
    class LeasedRepository(MemoryRepository):
        def __init__(self):
            super().__init__()
            self.lock = threading.Lock()
            self.claimed = False

        def claim_write(self, owner_id, upload_id):
            with self.lock:
                if self.claimed:
                    raise RuntimeError("write lease unavailable")
                self.claimed = True
                return super().claim_write(owner_id, upload_id)

    class BlockingS3(StreamingS3):
        def __init__(self):
            super().__init__()
            self.started = threading.Event()
            self.release = threading.Event()

        def put_object(self, **kwargs):
            self.started.set()
            assert self.release.wait(timeout=5)
            return super().put_object(**kwargs)

    repository = LeasedRepository()
    s3 = BlockingS3()
    service = AttachmentUploadService(
        repository, AttachmentObjectWriter(s3, "private-attachments")
    )

    def write_one(_index):
        try:
            return service.write(
                repository.owner_id,
                repository.upload.upload_id,
                io.BytesIO(b"payload"),
                7,
            )
        except AttachmentUploadConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        winner = pool.submit(write_one, 0)
        assert s3.started.wait(timeout=5)
        loser = pool.submit(write_one, 1)
        assert loser.result(timeout=5) is None
        s3.release.set()
        results = [winner.result(timeout=5), None]

    assert sum(result is not None for result in results) == 1
    assert len({key for _bucket, key, _size in s3.puts}) == len(s3.puts) == 1
    assert s3.deletes == []


def test_complete_is_idempotent_after_write() -> None:
    repository = MemoryRepository()
    service = AttachmentUploadService(
        repository, AttachmentObjectWriter(StreamingS3(), "private-attachments")
    )
    service.write(
        repository.owner_id,
        repository.upload.upload_id,
        io.BytesIO(b"payload"),
        7,
    )

    first = service.complete(repository.owner_id, repository.upload.upload_id)
    replay = service.complete(repository.owner_id, repository.upload.upload_id)

    assert first == replay
