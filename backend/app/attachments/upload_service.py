from __future__ import annotations

from datetime import UTC, datetime
from typing import BinaryIO
from uuid import UUID

from .conversation_models import (
    MAX_FILE_BYTES,
    AttachmentRecord,
    BeginUpload,
    UploadRecord,
)
from .object_writer import AttachmentObjectWriterError


class AttachmentUploadError(RuntimeError):
    pass


class AttachmentUploadConflict(AttachmentUploadError):
    pass


class AttachmentUploadService:
    def __init__(
        self,
        repository,
        object_writer,
        *,
        max_file_bytes: int = MAX_FILE_BYTES,
    ) -> None:
        if (
            isinstance(max_file_bytes, bool)
            or not isinstance(max_file_bytes, int)
            or max_file_bytes <= 0
            or max_file_bytes > MAX_FILE_BYTES
        ):
            raise ValueError("attachment file limit invalid")
        self._repository = repository
        self._object_writer = object_writer
        self._max_file_bytes = max_file_bytes

    def begin(self, owner_id: UUID, request: BeginUpload) -> UploadRecord:
        if not isinstance(request, BeginUpload):
            raise TypeError("begin upload request invalid")
        if (
            isinstance(request.declared_size, bool)
            or not isinstance(request.declared_size, int)
            or request.declared_size <= 0
            or request.declared_size > self._max_file_bytes
        ):
            raise AttachmentUploadConflict(
                "attachment exceeds the 50 MB single-file limit"
            )
        try:
            return self._repository.create_upload(
                owner_id,
                request.conversation_id,
                request.original_name,
                request.declared_mime,
                request.declared_size,
            )
        except Exception:  # noqa: BLE001 - repository boundary is intentionally opaque
            raise AttachmentUploadConflict("attachment upload rejected") from None

    def write(
        self,
        owner_id: UUID,
        upload_id: UUID,
        body: BinaryIO,
        content_length: int,
    ) -> UploadRecord:
        try:
            upload = self._repository.upload_for_owner(owner_id, upload_id)
        except Exception:  # noqa: BLE001 - repository boundary is intentionally opaque
            raise AttachmentUploadConflict("attachment upload unavailable") from None
        if upload.state != "uploading":
            return upload
        if upload.expires_at <= datetime.now(UTC):
            raise AttachmentUploadConflict("attachment upload expired")
        if (
            isinstance(content_length, bool)
            or not isinstance(content_length, int)
            or content_length != upload.declared_size
            or content_length > self._max_file_bytes
        ):
            raise AttachmentUploadConflict(
                "attachment content length mismatch"
            )
        try:
            attempt = self._repository.claim_write(owner_id, upload_id)
        except Exception:  # noqa: BLE001 - repository boundary is intentionally opaque
            raise AttachmentUploadConflict(
                "attachment upload write lease unavailable"
            ) from None
        try:
            receipt = self._object_writer.put_stream(
                attempt.object_ref, body, content_length
            )
        except (AttachmentObjectWriterError, ValueError):
            try:
                self._repository.abandon_write(
                    owner_id, upload_id, attempt.attempt_id
                )
            except Exception:  # noqa: BLE001 - keep uncertain attempt protected
                raise AttachmentUploadConflict(
                    "attachment object write failed; retry pending"
                ) from None
            raise AttachmentUploadConflict("attachment object write failed") from None
        try:
            attachment = self._repository.complete_upload(
                owner_id,
                upload_id,
                attempt.attempt_id,
                receipt.size_bytes,
                receipt.sha256,
            )
        except Exception:  # noqa: BLE001 - reconcile every finalize uncertainty
            try:
                reconciliation = self._repository.reconcile_write(
                    owner_id,
                    upload_id,
                    attempt.attempt_id,
                    receipt.size_bytes,
                    receipt.sha256,
                )
            except Exception:  # noqa: BLE001 - unreadable authority is fail-closed
                raise AttachmentUploadConflict(
                    "attachment upload finalize uncertain"
                ) from None
            if reconciliation.attachment is not None:
                attachment = reconciliation.attachment
            else:
                if reconciliation.cleanup_safe:
                    try:
                        self._object_writer.delete(attempt.object_ref)
                    except AttachmentObjectWriterError:
                        pass
                raise AttachmentUploadConflict(
                    "attachment upload finalize failed"
                ) from None
        try:
            return self._repository.upload_for_owner(owner_id, upload_id)
        except Exception:  # noqa: BLE001 - receipt already proves completion
            if attachment.state == "validating":
                return UploadRecord(
                    upload_id=upload_id,
                    attachment_id=attachment.attachment_id,
                    owner_id=attachment.owner_id,
                    conversation_id=attachment.conversation_id,
                    original_name=attachment.original_name,
                    declared_mime=attachment.declared_mime,
                    declared_size=attachment.size_bytes,
                    expires_at=upload.expires_at,
                    state=attachment.state,
                    actual_size=attachment.size_bytes,
                    sha256=attachment.sha256,
                )
            raise AttachmentUploadConflict(
                "attachment upload finalize failed"
            ) from None

    def complete(self, owner_id: UUID, upload_id: UUID) -> AttachmentRecord:
        try:
            return self._repository.completed_attachment(owner_id, upload_id)
        except Exception:  # noqa: BLE001 - expose only the service exception
            raise AttachmentUploadConflict("attachment upload incomplete") from None

    def cleanup_orphaned_writes(self, *, limit: int = 100) -> int:
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or limit <= 0
            or limit > 100
        ):
            raise ValueError("orphan cleanup limit invalid")
        try:
            attempts = self._repository.list_orphaned_writes(limit=limit)
        except Exception:  # noqa: BLE001 - expose only the service exception
            raise AttachmentUploadConflict(
                "attachment orphan cleanup unavailable"
            ) from None
        cleaned = 0
        for attempt in attempts:
            try:
                self._object_writer.delete(attempt.object_ref)
            except AttachmentObjectWriterError:
                continue
            try:
                self._repository.acknowledge_orphaned_write(attempt.attempt_id)
            except Exception:  # noqa: BLE001 - deletion is safe to retry
                raise AttachmentUploadConflict(
                    "attachment orphan cleanup acknowledgement unavailable"
                ) from None
            cleaned += 1
        return cleaned
