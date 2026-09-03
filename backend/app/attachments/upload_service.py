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
from .conversation_repository import ConversationAttachmentRepositoryError
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
        except (ConversationAttachmentRepositoryError, ValueError) as error:
            raise AttachmentUploadConflict("attachment upload rejected") from error

    def write(
        self,
        owner_id: UUID,
        upload_id: UUID,
        body: BinaryIO,
        content_length: int,
    ) -> UploadRecord:
        try:
            target = self._repository.upload_target(owner_id, upload_id)
        except Exception as error:
            raise AttachmentUploadConflict("attachment upload unavailable") from error
        upload = target.upload
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
            receipt = self._object_writer.put_stream(
                target.object_ref, body, content_length
            )
        except (AttachmentObjectWriterError, ValueError) as error:
            raise AttachmentUploadConflict("attachment object write failed") from error
        try:
            self._repository.complete_upload(
                owner_id, upload_id, receipt.size_bytes, receipt.sha256
            )
        except Exception as error:
            try:
                self._object_writer.delete(target.object_ref)
            except AttachmentObjectWriterError:
                pass
            raise AttachmentUploadConflict(
                "attachment upload finalize failed"
            ) from error
        try:
            return self._repository.upload_for_owner(owner_id, upload_id)
        except Exception as error:
            raise AttachmentUploadConflict(
                "attachment upload finalize failed"
            ) from error

    def complete(self, owner_id: UUID, upload_id: UUID) -> AttachmentRecord:
        try:
            return self._repository.completed_attachment(owner_id, upload_id)
        except Exception as error:
            raise AttachmentUploadConflict("attachment upload incomplete") from error
