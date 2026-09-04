from __future__ import annotations

import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import BinaryIO
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row

from app.control_plane.dsn import validate_control_dsn
from app.execution_relay.content_crypto import ContentCodec, SealedContent

from .conversation_models import MAX_FILE_BYTES
from .conversation_repository import attachment_name_subject, attachment_object_subject
from .grant_service import TaskGrantUnavailable, bearer_token_sha256
from .object_writer import AttachmentObjectWriterError

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_MIME = re.compile(r"[A-Za-z0-9!#$%&'*+.^_`|~-]+/[A-Za-z0-9!#$%&'*+.^_`|~-]+\Z")
_SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z")


class ArtifactUploadError(RuntimeError):
    pass


class ArtifactUploadConflict(ArtifactUploadError):
    pass


@dataclass(frozen=True)
class BeginArtifactUpload:
    agent_id: str
    artifact_key: str
    producer_version_id: str
    display_name: str = field(repr=False)
    declared_mime: str
    declared_size: int
    sha256_hex: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.agent_id, str)
            or _IDENTIFIER.fullmatch(self.agent_id) is None
            or not isinstance(self.artifact_key, str)
            or _IDENTIFIER.fullmatch(self.artifact_key) is None
            or not isinstance(self.producer_version_id, str)
            or not 0 < len(self.producer_version_id.encode("utf-8")) <= 160
            or any(character in self.producer_version_id for character in "\x00\r\n")
            or not isinstance(self.display_name, str)
            or not self.display_name.strip()
            or len(self.display_name.encode("utf-8")) > 1024
            or any(character in self.display_name for character in "\x00\r\n")
            or not isinstance(self.declared_mime, str)
            or _MIME.fullmatch(self.declared_mime) is None
            or isinstance(self.declared_size, bool)
            or not isinstance(self.declared_size, int)
            or not 0 < self.declared_size <= MAX_FILE_BYTES
            or not isinstance(self.sha256_hex, str)
            or _SHA256.fullmatch(self.sha256_hex) is None
        ):
            raise ValueError("artifact upload request invalid")

    @property
    def expected_sha256(self) -> bytes:
        return bytes.fromhex(self.sha256_hex)


@dataclass(frozen=True)
class ArtifactUpload:
    upload_id: UUID
    attachment_id: UUID
    artifact_id: UUID
    artifact_version_id: UUID
    task_id: UUID
    agent_id: str
    owner_id: UUID
    conversation_id: UUID
    artifact_key: str
    producer_version_id: str
    display_name: str = field(repr=False)
    declared_mime: str
    declared_size: int
    expected_sha256: bytes = field(repr=False)
    version_no: int
    state: str
    expires_at: datetime
    replayed: bool = False


@dataclass(frozen=True)
class ArtifactWriteAttempt:
    attempt_id: UUID
    upload: ArtifactUpload
    object_ref: str = field(repr=False)


class ArtifactRepository:
    def __init__(
        self,
        control_database_url: str,
        *,
        content_codec: ContentCodec,
        upload_ttl_seconds: int = 24 * 60 * 60,
        connect: Callable[..., object] = psycopg.connect,
    ) -> None:
        validate_control_dsn(control_database_url, purpose="app")
        if not isinstance(content_codec, ContentCodec):
            raise TypeError("content codec required")
        if (
            isinstance(upload_ttl_seconds, bool)
            or not isinstance(upload_ttl_seconds, int)
            or not 0 < upload_ttl_seconds <= 24 * 60 * 60
        ):
            raise ValueError("artifact upload lifetime invalid")
        self._database_url = control_database_url
        self._codec = content_codec
        self._upload_ttl_seconds = upload_ttl_seconds
        self._connect = connect

    def __repr__(self) -> str:
        return "ArtifactRepository(database_url=<redacted>, content_codec=<redacted>)"

    def _connection(self):
        return self._connect(
            self._database_url,
            connect_timeout=3,
            options="-c statement_timeout=10000 -c timezone=UTC",
            row_factory=dict_row,
        )

    @staticmethod
    def _upload_query() -> str:
        return (
            "select upload.upload_id,attachment.attachment_id,artifact.artifact_id,"
            "version.artifact_version_id,artifact.task_id,artifact.agent_id,"
            "attachment.owner_internal_user_id,attachment.conversation_id,"
            "artifact.artifact_key,version.producer_version_id,version.version_no,"
            "attachment.original_name_ciphertext,attachment.original_name_key_version,"
            "upload.declared_mime,upload.size_bytes,upload.expected_sha256,"
            "upload.expires_at,upload.state "
            "from platform_attachments.uploads upload "
            "join platform_attachments.attachments attachment "
            "on attachment.attachment_id=upload.attachment_id "
            "join platform_attachments.artifact_versions version "
            "on version.attachment_id=attachment.attachment_id "
            "join platform_attachments.artifacts artifact "
            "on artifact.artifact_id=version.artifact_id "
        )

    def _upload_from_row(self, row: dict, *, replayed: bool = False) -> ArtifactUpload:
        try:
            name = self._codec.unseal_json(
                attachment_name_subject(row["attachment_id"]),
                SealedContent(
                    bytes(row["original_name_ciphertext"]),
                    int(row["original_name_key_version"]),
                ),
            )
            if set(name) != {"original_name"} or not isinstance(name["original_name"], str):
                raise ValueError
            return ArtifactUpload(
                upload_id=row["upload_id"],
                attachment_id=row["attachment_id"],
                artifact_id=row["artifact_id"],
                artifact_version_id=row["artifact_version_id"],
                task_id=row["task_id"],
                agent_id=row["agent_id"],
                owner_id=row["owner_internal_user_id"],
                conversation_id=row["conversation_id"],
                artifact_key=row["artifact_key"],
                producer_version_id=row["producer_version_id"],
                display_name=name["original_name"],
                declared_mime=row["declared_mime"],
                declared_size=int(row["size_bytes"]),
                expected_sha256=bytes(row["expected_sha256"]),
                version_no=int(row["version_no"]),
                state=row["state"],
                expires_at=row["expires_at"],
                replayed=replayed,
            )
        except Exception:  # noqa: BLE001 - crypto/database failures are opaque
            raise ArtifactUploadConflict("artifact upload unavailable") from None

    def register(
        self,
        *,
        token_sha256: bytes,
        task_id: UUID,
        agent_id: str,
        artifact_key: str,
        producer_version_id: str,
        display_name: str,
        declared_mime: str,
        declared_size: int,
        expected_sha256: bytes,
    ) -> ArtifactUpload:
        upload_id, attachment_id = uuid4(), uuid4()
        artifact_id, artifact_version_id = uuid4(), uuid4()
        initial_object_ref = secrets.token_hex(32)
        name = self._codec.seal_json(
            attachment_name_subject(attachment_id), {"original_name": display_name}
        )
        object_value = self._codec.seal_json(
            attachment_object_subject(attachment_id), {"object_ref": initial_object_ref}
        )
        expires_at = datetime.now(UTC) + timedelta(seconds=self._upload_ttl_seconds)
        try:
            with self._connection() as connection, connection.transaction():
                result = connection.execute(
                    "select * from platform_attachments.create_artifact_upload_v64("
                    "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        token_sha256,
                        task_id,
                        agent_id,
                        upload_id,
                        attachment_id,
                        artifact_id,
                        artifact_version_id,
                        artifact_key,
                        producer_version_id,
                        name.ciphertext,
                        name.key_version,
                        object_value.ciphertext,
                        object_value.key_version,
                        declared_mime,
                        declared_size,
                        expected_sha256,
                        expires_at,
                    ),
                ).fetchone()
                if result is None:
                    raise ArtifactUploadConflict("artifact upload unavailable")
                row = connection.execute(
                    self._upload_query() + "where upload.upload_id=%s",
                    (result["upload_id"],),
                ).fetchone()
            if row is None:
                raise ArtifactUploadConflict("artifact upload unavailable")
            upload = self._upload_from_row(row, replayed=bool(result["replayed"]))
            if (
                upload.task_id != task_id
                or upload.agent_id != agent_id
                or upload.artifact_key != artifact_key
                or upload.producer_version_id != producer_version_id
                or upload.display_name != display_name
                or upload.declared_mime != declared_mime
                or upload.declared_size != declared_size
                or upload.expected_sha256 != expected_sha256
            ):
                raise ArtifactUploadConflict("artifact upload replay conflict")
            return upload
        except ArtifactUploadError:
            raise
        except Exception:  # noqa: BLE001 - authorization failures are opaque
            raise ArtifactUploadConflict("artifact upload unavailable") from None

    def upload(self, *, token_sha256: bytes, upload_id: UUID) -> ArtifactUpload:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    self._upload_query()
                    + "join platform_attachments.task_grants grant_row "
                    "on grant_row.task_id=artifact.task_id "
                    "and grant_row.agent_id=artifact.agent_id "
                    "where upload.upload_id=%s and grant_row.token_sha256=%s "
                    "and grant_row.scope='write_output' and grant_row.revoked_at is null "
                    "and grant_row.expires_at > now()",
                    (upload_id, token_sha256),
                ).fetchone()
            if row is None:
                raise ArtifactUploadConflict("artifact upload unavailable")
            return self._upload_from_row(row)
        except ArtifactUploadError:
            raise
        except Exception:  # noqa: BLE001
            raise ArtifactUploadConflict("artifact upload unavailable") from None

    def claim_write(
        self, *, token_sha256: bytes, upload_id: UUID
    ) -> ArtifactWriteAttempt:
        upload = self.upload(token_sha256=token_sha256, upload_id=upload_id)
        attempt_id = uuid4()
        object_ref = secrets.token_hex(32)
        sealed = self._codec.seal_json(
            attachment_object_subject(upload.attachment_id, attempt_id),
            {"object_ref": object_ref},
        )
        lease_expires_at = min(upload.expires_at, datetime.now(UTC) + timedelta(minutes=5))
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select platform_attachments.claim_artifact_upload_write_v64("
                    "%s,%s,%s,%s,%s,%s) as attachment_id",
                    (
                        token_sha256,
                        upload_id,
                        attempt_id,
                        sealed.ciphertext,
                        sealed.key_version,
                        lease_expires_at,
                    ),
                ).fetchone()
            if row is None or row["attachment_id"] != upload.attachment_id:
                raise ArtifactUploadConflict("artifact upload unavailable")
            return ArtifactWriteAttempt(attempt_id, upload, object_ref)
        except ArtifactUploadError:
            raise
        except Exception:  # noqa: BLE001
            raise ArtifactUploadConflict("artifact upload unavailable") from None

    def abandon_write(
        self, *, token_sha256: bytes, upload_id: UUID, attempt_id: UUID
    ) -> None:
        try:
            with self._connection() as connection:
                connection.execute(
                    "select platform_attachments.abandon_artifact_upload_write_v64("
                    "%s,%s,%s)",
                    (token_sha256, upload_id, attempt_id),
                )
        except Exception:  # noqa: BLE001 - cleanup is safe to retry
            raise ArtifactUploadConflict("artifact upload cleanup unavailable") from None

    def finalize(
        self,
        *,
        token_sha256: bytes,
        upload_id: UUID,
        attempt_id: UUID,
        declared_mime: str,
        size_bytes: int,
        sha256: bytes,
    ) -> ArtifactUpload:
        try:
            with self._connection() as connection:
                connection.execute(
                    "select platform_attachments.finalize_artifact_upload_v64("
                    "%s,%s,%s,%s,%s,%s)",
                    (
                        token_sha256,
                        upload_id,
                        attempt_id,
                        declared_mime,
                        size_bytes,
                        sha256,
                    ),
                )
            return self.upload(token_sha256=token_sha256, upload_id=upload_id)
        except ArtifactUploadError:
            raise
        except Exception:  # noqa: BLE001
            raise ArtifactUploadConflict("artifact upload finalize unavailable") from None


class ArtifactOutputService:
    def __init__(self, repository, object_writer, *, position_linker=None) -> None:
        self._repository = repository
        self._object_writer = object_writer
        self._position_linker = position_linker

    def set_position_linker(self, position_linker) -> None:
        if not callable(getattr(position_linker, "link_artifact", None)):
            raise ValueError("artifact position linker invalid")
        self._position_linker = position_linker

    def _link_position(self, upload: ArtifactUpload) -> None:
        if self._position_linker is None:
            return
        try:
            self._position_linker.link_artifact(
                upload.owner_id, upload.conversation_id, upload.artifact_id
            )
        except Exception as error:  # noqa: BLE001 - cross-domain failures are opaque
            raise ArtifactUploadConflict("artifact position link unavailable") from error

    @staticmethod
    def _digest(token: str) -> bytes:
        try:
            return bearer_token_sha256(token)
        except TaskGrantUnavailable:
            raise ArtifactUploadConflict("artifact grant unavailable") from None

    def begin(
        self, bearer_token: str, task_id: UUID, request: BeginArtifactUpload
    ) -> ArtifactUpload:
        if not isinstance(task_id, UUID) or not isinstance(request, BeginArtifactUpload):
            raise TypeError("artifact upload request invalid")
        return self._repository.register(
            token_sha256=self._digest(bearer_token),
            task_id=task_id,
            agent_id=request.agent_id,
            artifact_key=request.artifact_key,
            producer_version_id=request.producer_version_id,
            display_name=request.display_name.strip(),
            declared_mime=request.declared_mime,
            declared_size=request.declared_size,
            expected_sha256=request.expected_sha256,
        )

    def write(
        self,
        bearer_token: str,
        upload_id: UUID,
        body: BinaryIO,
        content_length: int,
    ) -> ArtifactUpload:
        token_sha256 = self._digest(bearer_token)
        upload = self._repository.upload(
            token_sha256=token_sha256, upload_id=upload_id
        )
        if upload.state != "uploading":
            self._link_position(upload)
            return upload
        if content_length != upload.declared_size:
            raise ArtifactUploadConflict("artifact content length mismatch")
        attempt = self._repository.claim_write(
            token_sha256=token_sha256, upload_id=upload_id
        )
        try:
            receipt = self._object_writer.put_stream(
                attempt.object_ref, body, content_length
            )
        except (AttachmentObjectWriterError, ValueError):
            try:
                self._repository.abandon_write(
                    token_sha256=token_sha256,
                    upload_id=upload_id,
                    attempt_id=attempt.attempt_id,
                )
            except ArtifactUploadError:
                pass
            raise ArtifactUploadConflict("artifact object write failed") from None
        if (
            receipt.size_bytes != upload.declared_size
            or receipt.sha256 != upload.expected_sha256
        ):
            try:
                self._object_writer.delete(attempt.object_ref)
            except (AttachmentObjectWriterError, ValueError):
                pass
            self._repository.abandon_write(
                token_sha256=token_sha256,
                upload_id=upload_id,
                attempt_id=attempt.attempt_id,
            )
            raise ArtifactUploadConflict("artifact integrity mismatch")
        finalized = self._repository.finalize(
            token_sha256=token_sha256,
            upload_id=upload_id,
            attempt_id=attempt.attempt_id,
            declared_mime=upload.declared_mime,
            size_bytes=receipt.size_bytes,
            sha256=receipt.sha256,
        )
        self._link_position(finalized)
        return finalized

    def complete(self, bearer_token: str, upload_id: UUID) -> ArtifactUpload:
        upload = self._repository.upload(
            token_sha256=self._digest(bearer_token), upload_id=upload_id
        )
        self._link_position(upload)
        return upload
