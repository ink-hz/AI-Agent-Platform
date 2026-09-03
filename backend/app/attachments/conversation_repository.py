from __future__ import annotations

import secrets
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row

from app.config import Config
from app.control_plane.dsn import validate_control_dsn
from app.execution_relay.content_crypto import (
    ContentCodec,
    ContentCryptoError,
    SealedContent,
)
from app.local_secrets import SecretFileUnavailable, read_secret_file

from .conversation_models import (
    MAX_CONVERSATION_BYTES,
    MAX_CONVERSATION_FILES,
    MAX_FILE_BYTES,
    MAX_MESSAGE_BYTES,
    MAX_MESSAGE_FILES,
    UPLOAD_TTL_SECONDS,
    AttachmentRecord,
    ConversationAssets,
    UploadRecord,
    UploadTarget,
)


class ConversationAttachmentRepositoryError(RuntimeError):
    def __init__(
        self, message: str = "conversation attachment repository unavailable"
    ) -> None:
        super().__init__(message)


class ConversationAttachmentNotFound(ConversationAttachmentRepositoryError):
    def __init__(self) -> None:
        super().__init__("conversation attachment not found")


class ConversationAttachmentConflict(ConversationAttachmentRepositoryError):
    pass


class ConversationAttachmentQuotaExceeded(ConversationAttachmentConflict):
    pass


def attachment_name_subject(attachment_id: UUID) -> str:
    return f"attachment:{attachment_id}:original-name"


def attachment_object_subject(attachment_id: UUID) -> str:
    return f"attachment:{attachment_id}:object-ref"


def _require_uuid(value: object) -> UUID:
    if not isinstance(value, UUID):
        raise TypeError("UUID required")
    return value


def _require_text(value: object, label: str, *, max_bytes: int = 1024) -> str:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise ValueError(f"{label} invalid")
    selected = value.strip()
    try:
        if len(selected.encode("utf-8")) > max_bytes:
            raise ValueError(f"{label} invalid")
    except UnicodeError:
        raise ValueError(f"{label} invalid") from None
    return selected


class ConversationAttachmentRepository:
    def __init__(
        self,
        control_database_url: str,
        *,
        content_codec: ContentCodec,
        upload_ttl_seconds: int = UPLOAD_TTL_SECONDS,
        max_file_bytes: int = MAX_FILE_BYTES,
        max_conversation_files: int = MAX_CONVERSATION_FILES,
        max_conversation_bytes: int = MAX_CONVERSATION_BYTES,
        max_message_files: int = MAX_MESSAGE_FILES,
        max_message_bytes: int = MAX_MESSAGE_BYTES,
        connect: Callable[..., Any] = psycopg.connect,
    ) -> None:
        parsed = validate_control_dsn(control_database_url, purpose="app")
        if not isinstance(content_codec, ContentCodec):
            raise TypeError("content codec required")
        limits = (
            (upload_ttl_seconds, UPLOAD_TTL_SECONDS),
            (max_file_bytes, MAX_FILE_BYTES),
            (max_conversation_files, MAX_CONVERSATION_FILES),
            (max_conversation_bytes, MAX_CONVERSATION_BYTES),
            (max_message_files, MAX_MESSAGE_FILES),
            (max_message_bytes, MAX_MESSAGE_BYTES),
        )
        if any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or value <= 0
            or value > ceiling
            for value, ceiling in limits
        ):
            raise ValueError("attachment repository limits invalid")
        if max_message_bytes > max_conversation_bytes:
            raise ValueError("attachment repository limits invalid")
        self.environment = parsed.environment
        self.content_codec = content_codec
        self._control_database_url = control_database_url
        self._connect = connect
        self._upload_ttl_seconds = upload_ttl_seconds
        self._max_file_bytes = max_file_bytes
        self._max_conversation_files = max_conversation_files
        self._max_conversation_bytes = max_conversation_bytes
        self._max_message_files = max_message_files
        self._max_message_bytes = max_message_bytes

    @classmethod
    def from_config(
        cls, config: Config, *, content_codec: ContentCodec
    ) -> ConversationAttachmentRepository:
        try:
            control_database_url = read_secret_file(
                config.attachment_control_database_url_file
            )
        except (AttributeError, SecretFileUnavailable) as error:
            raise ValueError("attachment Control DB DSN unavailable") from error
        return cls(
            control_database_url,
            content_codec=content_codec,
            upload_ttl_seconds=config.attachment_upload_ttl_seconds,
            max_file_bytes=config.attachment_max_file_bytes,
            max_conversation_files=config.attachment_max_conversation_files,
            max_conversation_bytes=config.attachment_max_conversation_bytes,
            max_message_files=config.attachment_max_message_files,
            max_message_bytes=config.attachment_max_message_bytes,
        )

    def __repr__(self) -> str:
        return (
            "ConversationAttachmentRepository("
            "control_database_url=<redacted>, content_codec=<redacted>, "
            f"environment={self.environment!r})"
        )

    def _connection(self):
        return self._connect(
            self._control_database_url,
            connect_timeout=3,
            options="-c statement_timeout=10000 -c timezone=UTC",
            row_factory=dict_row,
        )

    def _unseal(self, subject: str, ciphertext: object, version: object) -> dict:
        if not isinstance(version, int):
            raise ConversationAttachmentRepositoryError()
        try:
            return self.content_codec.unseal_json(
                subject, SealedContent(bytes(ciphertext), version)
            )
        except (ContentCryptoError, TypeError, ValueError):
            raise ConversationAttachmentRepositoryError() from None

    def _upload_from_row(self, row: dict[str, Any]) -> UploadRecord:
        attachment_id = row["attachment_id"]
        name = self._unseal(
            attachment_name_subject(attachment_id),
            row["original_name_ciphertext"],
            row["original_name_key_version"],
        )
        if set(name) != {"original_name"} or not isinstance(
            name["original_name"], str
        ):
            raise ConversationAttachmentRepositoryError()
        state = row["state"]
        size = int(row["size_bytes"])
        digest = bytes(row["sha256"]) if row["sha256"] is not None else None
        return UploadRecord(
            upload_id=row["upload_id"],
            attachment_id=attachment_id,
            owner_id=row["owner_internal_user_id"],
            conversation_id=row["conversation_id"],
            original_name=name["original_name"],
            declared_mime=row["declared_mime"],
            declared_size=size,
            expires_at=row["expires_at"],
            state=state,
            actual_size=size if state != "uploading" else None,
            sha256=digest,
        )

    def _attachment_from_row(self, row: dict[str, Any]) -> AttachmentRecord:
        attachment_id = row["attachment_id"]
        name = self._unseal(
            attachment_name_subject(attachment_id),
            row["original_name_ciphertext"],
            row["original_name_key_version"],
        )
        if set(name) != {"original_name"} or not isinstance(
            name["original_name"], str
        ):
            raise ConversationAttachmentRepositoryError()
        return AttachmentRecord(
            attachment_id=attachment_id,
            owner_id=row["owner_internal_user_id"],
            conversation_id=row["conversation_id"],
            original_name=name["original_name"],
            declared_mime=row["declared_mime"],
            detected_mime=row["detected_mime"],
            size_bytes=int(row["size_bytes"]),
            sha256=(
                bytes(row["sha256"]) if row["sha256"] is not None else None
            ),
            state=row["state"],
            created_at=row["created_at"],
            retained_until=row["retained_until"],
        )

    @staticmethod
    def _upload_query() -> str:
        return (
            "select upload.upload_id,upload.attachment_id,"
            "upload.owner_internal_user_id,upload.conversation_id,"
            "upload.expires_at,upload.state,upload.size_bytes,upload.sha256,"
            "attachment.original_name_ciphertext,"
            "attachment.original_name_key_version,attachment.declared_mime "
            "from platform_attachments.uploads upload "
            "join platform_attachments.attachments attachment "
            "on attachment.attachment_id=upload.attachment_id "
        )

    @staticmethod
    def _attachment_query() -> str:
        return (
            "select attachment_id,owner_internal_user_id,conversation_id,"
            "original_name_ciphertext,original_name_key_version,declared_mime,"
            "detected_mime,size_bytes,sha256,state,created_at,retained_until "
            "from platform_attachments.attachments "
        )

    def create_upload(
        self,
        owner_id: UUID,
        conversation_id: UUID | None,
        original_name: str,
        declared_mime: str,
        declared_size: int,
    ) -> UploadRecord:
        owner_id = _require_uuid(owner_id)
        if conversation_id is not None:
            conversation_id = _require_uuid(conversation_id)
        original_name = _require_text(original_name, "attachment name")
        declared_mime = _require_text(
            declared_mime, "attachment MIME", max_bytes=255
        )
        if (
            isinstance(declared_size, bool)
            or not isinstance(declared_size, int)
            or declared_size <= 0
            or declared_size > self._max_file_bytes
        ):
            raise ConversationAttachmentQuotaExceeded(
                "attachment file bytes quota exceeded"
            )
        attachment_id = uuid4()
        upload_id = uuid4()
        object_ref = secrets.token_hex(32)
        name = self.content_codec.seal_json(
            attachment_name_subject(attachment_id),
            {"original_name": original_name},
        )
        object_value = self.content_codec.seal_json(
            attachment_object_subject(attachment_id),
            {"object_ref": object_ref},
        )
        expires_at = datetime.now(UTC) + timedelta(
            seconds=self._upload_ttl_seconds
        )
        try:
            with self._connection() as connection, connection.transaction():
                cursor = connection.cursor()
                if conversation_id is None:
                    owner = cursor.execute(
                        "select 1 from platform_control.internal_users "
                        "where internal_user_id=%s and status='active'",
                        (owner_id,),
                    ).fetchone()
                else:
                    owner = cursor.execute(
                        "select 1 from platform_control.conversations "
                        "where conversation_id=%s and owner_internal_user_id=%s "
                        "and status='active' for update",
                        (conversation_id, owner_id),
                    ).fetchone()
                if owner is None:
                    raise ConversationAttachmentNotFound()
                if conversation_id is not None:
                    usage = cursor.execute(
                        "select count(*),coalesce(sum(attachment.size_bytes),0) "
                        "from platform_attachments.attachments attachment "
                        "left join platform_attachments.uploads upload "
                        "on upload.attachment_id=attachment.attachment_id "
                        "where attachment.owner_internal_user_id=%s "
                        "and attachment.conversation_id=%s "
                        "and attachment.source_kind='user_input' "
                        "and attachment.state <> 'deleted' "
                        "and not (attachment.state='uploading' "
                        "and (upload.expires_at is null or upload.expires_at <= now()))",
                        (owner_id, conversation_id),
                    ).fetchone()
                    if int(usage["count"]) >= self._max_conversation_files:
                        raise ConversationAttachmentQuotaExceeded(
                            "conversation attachment files quota exceeded"
                        )
                    if (
                        int(usage["coalesce"]) + declared_size
                        > self._max_conversation_bytes
                    ):
                        raise ConversationAttachmentQuotaExceeded(
                            "conversation attachment bytes quota exceeded"
                        )
                cursor.execute(
                    "insert into platform_attachments.attachments "
                    "(attachment_id,owner_internal_user_id,conversation_id,"
                    "source_kind,original_name_ciphertext,original_name_key_version,"
                    "object_ref_ciphertext,object_ref_key_version,declared_mime,"
                    "size_bytes) values (%s,%s,%s,'user_input',%s,%s,%s,%s,%s,%s)",
                    (
                        attachment_id,
                        owner_id,
                        conversation_id,
                        name.ciphertext,
                        name.key_version,
                        object_value.ciphertext,
                        object_value.key_version,
                        declared_mime,
                        declared_size,
                    ),
                )
                row = cursor.execute(
                    "insert into platform_attachments.uploads "
                    "(upload_id,attachment_id,owner_internal_user_id,conversation_id,"
                    "object_ref_ciphertext,object_ref_key_version,size_bytes,expires_at) "
                    "values (%s,%s,%s,%s,%s,%s,%s,%s) returning upload_id,"
                    "attachment_id,owner_internal_user_id,conversation_id,expires_at,"
                    "state,size_bytes,sha256",
                    (
                        upload_id,
                        attachment_id,
                        owner_id,
                        conversation_id,
                        object_value.ciphertext,
                        object_value.key_version,
                        declared_size,
                        expires_at,
                    ),
                ).fetchone()
            return self._upload_from_row(
                {
                    **row,
                    "original_name_ciphertext": name.ciphertext,
                    "original_name_key_version": name.key_version,
                    "declared_mime": declared_mime,
                }
            )
        except (
            ConversationAttachmentRepositoryError,
            ContentCryptoError,
            ValueError,
        ):
            raise
        except Exception as error:
            raise ConversationAttachmentRepositoryError() from error

    def upload_for_owner(self, owner_id: UUID, upload_id: UUID) -> UploadRecord:
        owner_id = _require_uuid(owner_id)
        upload_id = _require_uuid(upload_id)
        try:
            with self._connection() as connection:
                row = connection.execute(
                    self._upload_query()
                    + "where upload.upload_id=%s "
                    "and upload.owner_internal_user_id=%s",
                    (upload_id, owner_id),
                ).fetchone()
            if row is None:
                raise ConversationAttachmentNotFound()
            return self._upload_from_row(row)
        except ConversationAttachmentRepositoryError:
            raise
        except Exception as error:
            raise ConversationAttachmentRepositoryError() from error

    def upload_target(self, owner_id: UUID, upload_id: UUID) -> UploadTarget:
        upload = self.upload_for_owner(owner_id, upload_id)
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select object_ref_ciphertext,object_ref_key_version "
                    "from platform_attachments.uploads where upload_id=%s "
                    "and owner_internal_user_id=%s",
                    (upload_id, owner_id),
                ).fetchone()
            if row is None:
                raise ConversationAttachmentNotFound()
            value = self._unseal(
                attachment_object_subject(upload.attachment_id),
                row["object_ref_ciphertext"],
                row["object_ref_key_version"],
            )
            if set(value) != {"object_ref"} or not isinstance(
                value["object_ref"], str
            ):
                raise ConversationAttachmentRepositoryError()
            return UploadTarget(upload, value["object_ref"])
        except ConversationAttachmentRepositoryError:
            raise
        except Exception as error:
            raise ConversationAttachmentRepositoryError() from error

    def complete_upload(
        self,
        owner_id: UUID,
        upload_id: UUID,
        actual_size: int,
        sha256: bytes,
    ) -> AttachmentRecord:
        upload = self.upload_for_owner(owner_id, upload_id)
        if (
            isinstance(actual_size, bool)
            or not isinstance(actual_size, int)
            or actual_size < 0
            or not isinstance(sha256, bytes)
            or len(sha256) != 32
        ):
            raise ValueError("attachment receipt invalid")
        if upload.state != "uploading":
            completed = self.completed_attachment(owner_id, upload_id)
            if completed.size_bytes != actual_size or completed.sha256 != sha256:
                raise ConversationAttachmentConflict(
                    "attachment upload receipt conflict"
                )
            return completed
        if upload.expires_at <= datetime.now(UTC):
            raise ConversationAttachmentConflict("attachment upload expired")
        if actual_size != upload.declared_size:
            raise ConversationAttachmentConflict(
                "attachment upload size mismatch"
            )
        try:
            with self._connection() as connection, connection.transaction():
                row = connection.execute(
                    "select platform_attachments.finalize_upload_v64("
                    "%s,%s,%s,%s,%s) as attachment_id",
                    (
                        upload_id,
                        owner_id,
                        upload.declared_mime,
                        actual_size,
                        sha256,
                    ),
                ).fetchone()
                if row is None or row["attachment_id"] is None:
                    raise ConversationAttachmentConflict(
                        "attachment upload finalize conflict"
                    )
                attachment = connection.execute(
                    self._attachment_query() + "where attachment_id=%s",
                    (row["attachment_id"],),
                ).fetchone()
            if attachment is None:
                raise ConversationAttachmentRepositoryError()
            return self._attachment_from_row(attachment)
        except ConversationAttachmentRepositoryError:
            raise
        except psycopg.errors.NoDataFound as error:
            raise ConversationAttachmentConflict(
                "attachment upload finalize conflict"
            ) from error
        except Exception as error:
            raise ConversationAttachmentRepositoryError() from error

    def completed_attachment(
        self, owner_id: UUID, upload_id: UUID
    ) -> AttachmentRecord:
        owner_id = _require_uuid(owner_id)
        upload_id = _require_uuid(upload_id)
        try:
            with self._connection() as connection:
                row = connection.execute(
                    self._attachment_query()
                    + "where attachment_id=(select attachment_id "
                    "from platform_attachments.uploads where upload_id=%s "
                    "and owner_internal_user_id=%s and state <> 'uploading') "
                    "and owner_internal_user_id=%s",
                    (upload_id, owner_id, owner_id),
                ).fetchone()
            if row is None:
                raise ConversationAttachmentConflict("attachment upload incomplete")
            return self._attachment_from_row(row)
        except ConversationAttachmentRepositoryError:
            raise
        except Exception as error:
            raise ConversationAttachmentRepositoryError() from error

    def list_conversation_assets(
        self, owner_id: UUID, conversation_id: UUID
    ) -> ConversationAssets:
        owner_id = _require_uuid(owner_id)
        conversation_id = _require_uuid(conversation_id)
        try:
            with self._connection() as connection:
                conversation = connection.execute(
                    "select 1 from platform_control.conversations "
                    "where conversation_id=%s and owner_internal_user_id=%s",
                    (conversation_id, owner_id),
                ).fetchone()
                if conversation is None:
                    raise ConversationAttachmentNotFound()
                rows = connection.execute(
                    self._attachment_query()
                    + "where owner_internal_user_id=%s and conversation_id=%s "
                    "and state <> 'deleted' order by created_at,attachment_id",
                    (owner_id, conversation_id),
                ).fetchall()
            return ConversationAssets(
                conversation_id,
                tuple(self._attachment_from_row(row) for row in rows),
            )
        except ConversationAttachmentRepositoryError:
            raise
        except Exception as error:
            raise ConversationAttachmentRepositoryError() from error

    def prepare_message(
        self,
        owner_id: UUID,
        conversation_id: UUID,
        attachment_ids: Iterable[UUID],
    ) -> tuple[AttachmentRecord, ...]:
        owner_id = _require_uuid(owner_id)
        conversation_id = _require_uuid(conversation_id)
        selected = tuple(attachment_ids)
        if any(not isinstance(value, UUID) for value in selected):
            raise ValueError("attachment IDs invalid")
        if len(set(selected)) != len(selected):
            raise ConversationAttachmentConflict("duplicate attachment selection")
        if len(selected) > self._max_message_files:
            raise ConversationAttachmentQuotaExceeded(
                "message attachment files quota exceeded"
            )
        if not selected:
            return ()
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    self._attachment_query()
                    + "where owner_internal_user_id=%s and conversation_id=%s "
                    "and attachment_id=any(%s) and state='ready' "
                    "and retained_until > now()",
                    (owner_id, conversation_id, list(selected)),
                ).fetchall()
            by_id = {
                row["attachment_id"]: self._attachment_from_row(row) for row in rows
            }
            if set(by_id) != set(selected):
                raise ConversationAttachmentNotFound()
            result = tuple(by_id[attachment_id] for attachment_id in selected)
            if sum(asset.size_bytes for asset in result) > self._max_message_bytes:
                raise ConversationAttachmentQuotaExceeded(
                    "message attachment bytes quota exceeded"
                )
            return result
        except ConversationAttachmentRepositoryError:
            raise
        except Exception as error:
            raise ConversationAttachmentRepositoryError() from error
