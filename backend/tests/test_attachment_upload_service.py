from __future__ import annotations

import hashlib
import io
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.attachments.conversation_models import (
    MAX_FILE_BYTES,
    AttachmentRecord,
    BeginUpload,
    UploadRecord,
    UploadTarget,
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
        self.target = UploadTarget(self.upload, "objects/random")
        self.finalize_fail_once = False
        self.complete_calls = 0
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
        self.target = UploadTarget(self.upload, self.target.object_ref)
        return self.upload

    def upload_target(self, owner_id, upload_id):
        if owner_id != self.owner_id or upload_id != self.upload.upload_id:
            raise RuntimeError("not found")
        return self.target

    def complete_upload(self, owner_id, upload_id, actual_size, sha256):
        self.complete_calls += 1
        if self.finalize_fail_once:
            self.finalize_fail_once = False
            raise RuntimeError("database unavailable")
        self.upload = replace(
            self.upload,
            state="validating",
            actual_size=actual_size,
            sha256=sha256,
        )
        self.target = UploadTarget(self.upload, self.target.object_ref)
        self.attachment = AttachmentRecord(
            attachment_id=self.upload.attachment_id,
            owner_id=owner_id,
            conversation_id=self.upload.conversation_id,
            original_name=self.upload.original_name,
            declared_mime=self.upload.declared_mime,
            detected_mime=self.upload.declared_mime,
            size_bytes=actual_size,
            sha256=sha256,
            state="validating",
            created_at=datetime.now(UTC),
            retained_until=datetime.now(UTC) + timedelta(days=365),
        )
        return self.attachment

    def upload_for_owner(self, owner_id, upload_id):
        self.upload_target(owner_id, upload_id)
        return self.upload

    def completed_attachment(self, owner_id, upload_id):
        self.upload_target(owner_id, upload_id)
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


def test_finalize_failure_deletes_object_and_same_upload_can_retry() -> None:
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
    result = service.write(
        repository.owner_id,
        repository.upload.upload_id,
        io.BytesIO(b"payload"),
        7,
    )

    assert result.state == "validating"
    assert result.actual_size == 7
    assert s3.deletes == [("private-attachments", "objects/random")]
    assert len(s3.puts) == 2


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
