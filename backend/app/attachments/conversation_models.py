from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

MEBIBYTE = 1024 * 1024
MAX_FILE_BYTES = 50 * MEBIBYTE
MAX_MESSAGE_FILES = 5
MAX_MESSAGE_BYTES = 50 * MEBIBYTE
MAX_CONVERSATION_FILES = 50
MAX_CONVERSATION_BYTES = 500 * MEBIBYTE
MAX_TASK_OUTPUT_FILES = 20
MAX_TASK_OUTPUT_BYTES = 250 * MEBIBYTE
UPLOAD_TTL_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class BeginUpload:
    conversation_id: UUID | None
    original_name: str
    declared_mime: str
    declared_size: int


@dataclass(frozen=True)
class UploadRecord:
    upload_id: UUID
    attachment_id: UUID
    owner_id: UUID
    conversation_id: UUID | None
    original_name: str
    declared_mime: str
    declared_size: int
    expires_at: datetime
    state: str
    actual_size: int | None
    sha256: bytes | None = field(repr=False)


@dataclass(frozen=True)
class UploadTarget:
    upload: UploadRecord
    object_ref: str = field(repr=False)


@dataclass(frozen=True)
class ObjectReceipt:
    size_bytes: int
    sha256: bytes = field(repr=False)


@dataclass(frozen=True)
class AttachmentRecord:
    attachment_id: UUID
    owner_id: UUID
    conversation_id: UUID | None
    original_name: str
    declared_mime: str
    detected_mime: str | None
    size_bytes: int
    sha256: bytes | None = field(repr=False)
    state: str
    created_at: datetime
    retained_until: datetime


@dataclass(frozen=True)
class ConversationAssets:
    conversation_id: UUID
    attachments: tuple[AttachmentRecord, ...]

    @property
    def file_count(self) -> int:
        return len(self.attachments)

    @property
    def size_bytes(self) -> int:
        return sum(attachment.size_bytes for attachment in self.attachments)
