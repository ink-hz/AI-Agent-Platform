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
    OrphanedWriteAttempt,
    UploadRecord,
    UploadTarget,
    WriteAttempt,
    WriteReconciliation,
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


def attachment_object_subject(
    attachment_id: UUID, attempt_id: UUID | None = None
) -> str:
    suffix = f":attempt:{attempt_id}" if attempt_id is not None else ""
    return f"attachment:{attachment_id}:object-ref{suffix}"


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
            with self._connection() as connection:
                row = connection.execute(
                    "select * from platform_attachments.create_upload_v64("
                    "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        upload_id,
                        attachment_id,
                        owner_id,
                        conversation_id,
                        name.ciphertext,
                        name.key_version,
                        object_value.ciphertext,
                        object_value.key_version,
                        declared_mime,
                        declared_size,
                        expires_at,
                        self._max_file_bytes,
                        self._max_conversation_files,
                        self._max_conversation_bytes,
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
        except psycopg.errors.NoDataFound:
            raise ConversationAttachmentNotFound() from None
        except psycopg.errors.ProgramLimitExceeded as error:
            quota = (
                "files"
                if error.diag.message_primary
                and "files" in error.diag.message_primary
                else "bytes"
            )
            raise ConversationAttachmentQuotaExceeded(
                f"conversation attachment {quota} quota exceeded"
            ) from None
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
                    "select object_ref_ciphertext,object_ref_key_version,"
                    "write_attempt_id "
                    "from platform_attachments.uploads where upload_id=%s "
                    "and owner_internal_user_id=%s",
                    (upload_id, owner_id),
                ).fetchone()
            if row is None:
                raise ConversationAttachmentNotFound()
            value = self._unseal(
                attachment_object_subject(
                    upload.attachment_id, row["write_attempt_id"]
                ),
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

    def claim_write(self, owner_id: UUID, upload_id: UUID) -> WriteAttempt:
        upload = self.upload_for_owner(owner_id, upload_id)
        if upload.state != "uploading":
            raise ConversationAttachmentConflict("attachment upload not writable")
        now = datetime.now(UTC)
        if upload.expires_at <= now:
            raise ConversationAttachmentConflict("attachment upload expired")
        attempt_id = uuid4()
        object_ref = secrets.token_hex(32)
        lease_expires_at = min(
            upload.expires_at, now + timedelta(minutes=5)
        )
        object_value = self.content_codec.seal_json(
            attachment_object_subject(upload.attachment_id, attempt_id),
            {"object_ref": object_ref},
        )
        try:
            with self._connection() as connection, connection.transaction():
                row = connection.execute(
                    "select platform_attachments.claim_upload_write_v64("
                    "%s,%s,%s,%s,%s,%s) as attachment_id",
                    (
                        upload_id,
                        owner_id,
                        attempt_id,
                        object_value.ciphertext,
                        object_value.key_version,
                        lease_expires_at,
                    ),
                ).fetchone()
            if row is None or row["attachment_id"] != upload.attachment_id:
                raise ConversationAttachmentConflict(
                    "attachment upload write lease unavailable"
                )
            return WriteAttempt(
                attempt_id,
                upload,
                object_ref,
                lease_expires_at,
            )
        except ConversationAttachmentRepositoryError:
            raise
        except psycopg.errors.NoDataFound:
            raise ConversationAttachmentConflict(
                "attachment upload write lease unavailable"
            ) from None
        except Exception as error:
            raise ConversationAttachmentRepositoryError() from error

    def abandon_write(
        self, owner_id: UUID, upload_id: UUID, attempt_id: UUID
    ) -> None:
        owner_id = _require_uuid(owner_id)
        upload_id = _require_uuid(upload_id)
        attempt_id = _require_uuid(attempt_id)
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select platform_attachments.abandon_upload_write_v64("
                    "%s,%s,%s) as attachment_id",
                    (upload_id, owner_id, attempt_id),
                ).fetchone()
            if row is None or row["attachment_id"] is None:
                raise ConversationAttachmentConflict(
                    "attachment upload abandonment unavailable"
                )
        except ConversationAttachmentRepositoryError:
            raise
        except psycopg.errors.NoDataFound:
            raise ConversationAttachmentConflict(
                "attachment upload abandonment unavailable"
            ) from None
        except Exception as error:
            raise ConversationAttachmentRepositoryError() from error

    def complete_upload(
        self,
        owner_id: UUID,
        upload_id: UUID,
        attempt_id: UUID,
        actual_size: int,
        sha256: bytes,
    ) -> AttachmentRecord:
        upload = self.upload_for_owner(owner_id, upload_id)
        attempt_id = _require_uuid(attempt_id)
        if (
            isinstance(actual_size, bool)
            or not isinstance(actual_size, int)
            or actual_size < 0
            or not isinstance(sha256, bytes)
            or len(sha256) != 32
        ):
            raise ValueError("attachment receipt invalid")
        if upload.state != "uploading":
            reconciliation = self.reconcile_write(
                owner_id, upload_id, attempt_id, actual_size, sha256
            )
            if reconciliation.attachment is None:
                raise ConversationAttachmentConflict(
                    "attachment upload receipt conflict"
                )
            return reconciliation.attachment
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
                    "%s,%s,%s,%s,%s,%s) as attachment_id",
                    (
                        upload_id,
                        owner_id,
                        attempt_id,
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
        except psycopg.errors.NoDataFound:
            reconciliation = self.reconcile_write(
                owner_id, upload_id, attempt_id, actual_size, sha256
            )
            if reconciliation.attachment is not None:
                return reconciliation.attachment
            raise ConversationAttachmentConflict(
                "attachment upload receipt conflict"
            ) from None
        except Exception as error:
            raise ConversationAttachmentRepositoryError() from error

    def reconcile_write(
        self,
        owner_id: UUID,
        upload_id: UUID,
        attempt_id: UUID,
        actual_size: int,
        sha256: bytes,
    ) -> WriteReconciliation:
        owner_id = _require_uuid(owner_id)
        upload_id = _require_uuid(upload_id)
        attempt_id = _require_uuid(attempt_id)
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select attempt.state as attempt_state,"
                    "attempt.size_bytes as attempt_size,"
                    "attempt.sha256 as attempt_sha256,"
                    "upload.write_attempt_id,upload.state,"
                    "upload.size_bytes,upload.sha256,attachment.attachment_id,"
                    "attachment.owner_internal_user_id,attachment.conversation_id,"
                    "attachment.original_name_ciphertext,"
                    "attachment.original_name_key_version,attachment.declared_mime,"
                    "attachment.detected_mime,attachment.size_bytes as attachment_size,"
                    "attachment.sha256 as attachment_sha256,"
                    "attachment.state as attachment_state,attachment.created_at,"
                    "attachment.retained_until "
                    "from platform_attachments.upload_write_attempts attempt "
                    "join platform_attachments.uploads upload "
                    "on upload.upload_id=attempt.upload_id "
                    "join platform_attachments.attachments attachment "
                    "on attachment.attachment_id=upload.attachment_id "
                    "where attempt.attempt_id=%s and attempt.upload_id=%s "
                    "and attempt.owner_internal_user_id=%s",
                    (attempt_id, upload_id, owner_id),
                ).fetchone()
            if row is None:
                return WriteReconciliation(None, cleanup_safe=False)
            if row["attempt_state"] in ("superseded", "abandoned"):
                return WriteReconciliation(None, cleanup_safe=True)
            if (
                row["attempt_state"] != "canonical"
                or row["write_attempt_id"] != attempt_id
                or row["state"] == "uploading"
            ):
                return WriteReconciliation(None, cleanup_safe=False)
            if (
                row["attempt_size"] is None
                or int(row["attempt_size"]) != actual_size
                or row["attempt_sha256"] is None
                or bytes(row["attempt_sha256"]) != sha256
                or int(row["size_bytes"]) != actual_size
                or row["sha256"] is None
                or bytes(row["sha256"]) != sha256
                or int(row["attachment_size"]) != actual_size
                or row["attachment_sha256"] is None
                or bytes(row["attachment_sha256"]) != sha256
            ):
                return WriteReconciliation(None, cleanup_safe=False)
            attachment_row = {
                **row,
                "size_bytes": row["attachment_size"],
                "sha256": row["attachment_sha256"],
                "state": row["attachment_state"],
            }
            return WriteReconciliation(
                self._attachment_from_row(attachment_row),
                cleanup_safe=False,
            )
        except ConversationAttachmentRepositoryError:
            raise
        except Exception as error:
            raise ConversationAttachmentRepositoryError() from error

    def list_orphaned_writes(
        self, *, limit: int = 100
    ) -> tuple[OrphanedWriteAttempt, ...]:
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or limit <= 0
            or limit > 100
        ):
            raise ValueError("orphan reconciliation limit invalid")
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "select attempt.attempt_id,attempt.attachment_id,"
                    "attempt.object_ref_ciphertext,attempt.object_ref_key_version "
                    "from platform_attachments.upload_write_attempts attempt "
                    "join platform_attachments.uploads upload "
                    "on upload.upload_id=attempt.upload_id "
                    "where attempt.state in ('superseded','abandoned') or "
                    "(attempt.state='claimed' and upload.state='uploading' "
                    "and upload.expires_at <= now()) "
                    "order by attempt.lease_expires_at,attempt.created_at "
                    "limit %s",
                    (limit,),
                ).fetchall()
            result = []
            for row in rows:
                value = self._unseal(
                    attachment_object_subject(
                        row["attachment_id"], row["attempt_id"]
                    ),
                    row["object_ref_ciphertext"],
                    row["object_ref_key_version"],
                )
                if set(value) != {"object_ref"} or not isinstance(
                    value["object_ref"], str
                ):
                    raise ConversationAttachmentRepositoryError()
                result.append(
                    OrphanedWriteAttempt(
                        row["attempt_id"], value["object_ref"]
                    )
                )
            return tuple(result)
        except ConversationAttachmentRepositoryError:
            raise
        except Exception as error:
            raise ConversationAttachmentRepositoryError() from error

    def acknowledge_orphaned_write(self, attempt_id: UUID) -> None:
        attempt_id = _require_uuid(attempt_id)
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select platform_attachments."
                    "acknowledge_upload_write_cleanup_v64(%s) as attempt_id",
                    (attempt_id,),
                ).fetchone()
            if row is None or row["attempt_id"] != attempt_id:
                raise ConversationAttachmentConflict(
                    "attachment orphan acknowledgement unavailable"
                )
        except ConversationAttachmentRepositoryError:
            raise
        except psycopg.errors.NoDataFound:
            raise ConversationAttachmentConflict(
                "attachment orphan acknowledgement unavailable"
            ) from None
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

    def bind_turn_locked(
        self,
        cursor: Any,
        *,
        owner_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
        turn_id: UUID,
        attachment_ids: tuple[UUID, ...],
        active_attachment_ids: tuple[UUID, ...],
        agent_id: str | None,
        agent_supports_attachments: bool,
    ) -> None:
        """Validate and bind one submission using the caller's transaction."""

        owner_id = _require_uuid(owner_id)
        conversation_id = _require_uuid(conversation_id)
        message_id = _require_uuid(message_id)
        turn_id = _require_uuid(turn_id)
        invalid_selection = (
            any(not isinstance(value, UUID) for value in attachment_ids)
            or any(not isinstance(value, UUID) for value in active_attachment_ids)
            or len(set(attachment_ids)) != len(attachment_ids)
            or len(set(active_attachment_ids)) != len(active_attachment_ids)
            or len(attachment_ids) > self._max_message_files
            or len(active_attachment_ids) > self._max_conversation_files
            or not set(attachment_ids).issubset(active_attachment_ids)
        )
        if invalid_selection:
            raise ConversationAttachmentConflict("attachment selection invalid")
        if (
            active_attachment_ids
            and agent_id is not None
            and not agent_supports_attachments
        ):
            raise ConversationAttachmentConflict(
                "agent does not support attachments"
            )
        try:
            cursor.execute(
                "select platform_attachments.bind_conversation_turn_v64("
                "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    owner_id,
                    conversation_id,
                    message_id,
                    turn_id,
                    list(attachment_ids),
                    list(active_attachment_ids),
                    agent_id,
                    agent_supports_attachments,
                    self._max_message_files,
                    self._max_message_bytes,
                    self._max_conversation_files,
                    self._max_conversation_bytes,
                ),
            )
        except ConversationAttachmentRepositoryError:
            raise
        except (
            psycopg.errors.CheckViolation,
            psycopg.errors.NoDataFound,
            psycopg.errors.ProgramLimitExceeded,
        ) as error:
            if isinstance(error, psycopg.errors.ProgramLimitExceeded):
                raise ConversationAttachmentQuotaExceeded(
                    "conversation attachment quota exceeded"
                ) from None
            raise ConversationAttachmentConflict("attachment unavailable") from None
        except psycopg.Error as error:
            raise ConversationAttachmentRepositoryError() from error
