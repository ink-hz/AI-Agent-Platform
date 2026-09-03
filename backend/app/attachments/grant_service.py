from __future__ import annotations

import hashlib
import re
import secrets
import shutil
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row

from app.control_plane.dsn import validate_control_dsn
from app.execution_relay.content_crypto import ContentCodec, SealedContent

from .conversation_models import (
    MAX_TASK_OUTPUT_BYTES,
    MAX_TASK_OUTPUT_FILES,
)
from .conversation_repository import attachment_name_subject, attachment_object_subject
from .download_service import DownloadAsset, OpenedDownload, _content_disposition

_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
_AGENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_MAX_GRANT_SECONDS = 24 * 60 * 60


class TaskGrantError(RuntimeError):
    pass


class TaskGrantUnavailable(TaskGrantError):
    def __init__(self) -> None:
        super().__init__("attachment grant unavailable")


@dataclass(frozen=True, repr=False)
class TaskAttachmentGrant:
    attachment_id: UUID
    display_name: str
    detected_mime: str
    size_bytes: int
    sha256_hex: str = field(repr=False)
    download_url: str
    bearer_token: str = field(repr=False)
    expires_at: datetime

    def __repr__(self) -> str:
        return (
            "TaskAttachmentGrant("
            f"attachment_id={self.attachment_id!r}, display_name=<redacted>, "
            f"detected_mime={self.detected_mime!r}, size_bytes={self.size_bytes!r}, "
            "sha256_hex=<redacted>, download_url=<redacted>, "
            f"bearer_token=<redacted>, expires_at={self.expires_at!r})"
        )


@dataclass(frozen=True, repr=False)
class OutputWriteGrant:
    task_id: UUID
    agent_id: str
    upload_url: str
    bearer_token: str = field(repr=False)
    max_files: int
    max_total_bytes: int

    def __repr__(self) -> str:
        return (
            "OutputWriteGrant("
            f"task_id={self.task_id!r}, agent_id={self.agent_id!r}, "
            "upload_url=<redacted>, bearer_token=<redacted>, "
            f"max_files={self.max_files!r}, max_total_bytes={self.max_total_bytes!r})"
        )


def bearer_token_sha256(value: str) -> bytes:
    if not isinstance(value, str) or _TOKEN.fullmatch(value) is None:
        raise TaskGrantUnavailable()
    return hashlib.sha256(value.encode("ascii")).digest()


class TaskGrantRepository:
    def __init__(
        self,
        control_database_url: str,
        *,
        content_codec: ContentCodec,
        connect: Callable[..., object] = psycopg.connect,
    ) -> None:
        validate_control_dsn(control_database_url, purpose="app")
        if not isinstance(content_codec, ContentCodec):
            raise TypeError("content codec required")
        self._database_url = control_database_url
        self._codec = content_codec
        self._connect = connect

    def __repr__(self) -> str:
        return "TaskGrantRepository(database_url=<redacted>, content_codec=<redacted>)"

    def _connection(self):
        return self._connect(
            self._database_url,
            connect_timeout=3,
            options="-c statement_timeout=10000 -c timezone=UTC",
            row_factory=dict_row,
        )

    def _unseal(self, subject: str, ciphertext: object, key_version: object) -> dict:
        try:
            return self._codec.unseal_json(
                subject, SealedContent(bytes(ciphertext), int(key_version))
            )
        except Exception:  # noqa: BLE001 - cryptographic failures are opaque
            raise TaskGrantUnavailable() from None

    def _asset_from_row(self, row: dict) -> DownloadAsset:
        attachment_id = row["attachment_id"]
        name = self._unseal(
            attachment_name_subject(attachment_id),
            row["original_name_ciphertext"],
            row["original_name_key_version"],
        )
        object_value = self._unseal(
            attachment_object_subject(attachment_id, row["write_attempt_id"]),
            row["object_ref_ciphertext"],
            row["object_ref_key_version"],
        )
        if (
            set(name) != {"original_name"}
            or not isinstance(name["original_name"], str)
            or set(object_value) != {"object_ref"}
            or not isinstance(object_value["object_ref"], str)
            or row["detected_mime"] is None
            or row["sha256"] is None
            or row["immutable_locator"] is None
        ):
            raise TaskGrantUnavailable()
        return DownloadAsset(
            attachment_id=attachment_id,
            owner_id=row["owner_internal_user_id"],
            conversation_id=row["conversation_id"],
            display_name=name["original_name"],
            media_type=row["detected_mime"],
            size_bytes=int(row["size_bytes"]),
            sha256=bytes(row["sha256"]),
            state=row["state"],
            object_ref=object_value["object_ref"],
            immutable_locator=row["immutable_locator"],
        )

    @staticmethod
    def _asset_query() -> str:
        return (
            "select attachment.attachment_id,attachment.owner_internal_user_id,"
            "attachment.conversation_id,attachment.original_name_ciphertext,"
            "attachment.original_name_key_version,attachment.object_ref_ciphertext,"
            "attachment.object_ref_key_version,attachment.detected_mime,"
            "attachment.size_bytes,attachment.sha256,attachment.state,"
            "attachment.immutable_locator,upload.write_attempt_id "
            "from platform_attachments.attachments attachment "
            "left join platform_attachments.uploads upload "
            "on upload.attachment_id=attachment.attachment_id "
        )

    def issue_read(
        self,
        *,
        grant_id: UUID,
        token_sha256: bytes,
        task_id: UUID,
        attachment_id: UUID,
        agent_id: str,
        expires_at: datetime,
        max_reads: int,
    ) -> DownloadAsset:
        try:
            with self._connection() as connection, connection.transaction():
                row = connection.execute(
                    self._asset_query()
                    + "where attachment.attachment_id=%s and attachment.state='ready'",
                    (attachment_id,),
                ).fetchone()
                if row is None:
                    raise TaskGrantUnavailable()
                asset = self._asset_from_row(row)
                connection.execute(
                    "select platform_attachments.issue_task_grant_v64("
                    "%s,%s,%s,%s,%s,'read',%s,%s,%s)",
                    (
                        grant_id,
                        token_sha256,
                        task_id,
                        attachment_id,
                        agent_id,
                        expires_at,
                        max_reads,
                        asset.size_bytes * max_reads,
                    ),
                )
            return asset
        except TaskGrantError:
            raise
        except Exception:  # noqa: BLE001 - authorization failures are intentionally opaque
            raise TaskGrantUnavailable() from None

    def issue_output(
        self,
        *,
        grant_id: UUID,
        token_sha256: bytes,
        task_id: UUID,
        agent_id: str,
        expires_at: datetime,
        max_files: int,
        max_total_bytes: int,
        max_file_bytes: int,
    ) -> None:
        try:
            with self._connection() as connection:
                connection.execute(
                    "select platform_attachments.issue_task_grant_v64("
                    "%s,%s,%s,null,%s,'write_output',%s,0,%s,%s,%s)",
                    (
                        grant_id,
                        token_sha256,
                        task_id,
                        agent_id,
                        expires_at,
                        max_total_bytes,
                        max_files,
                        max_file_bytes,
                    ),
                )
        except Exception:  # noqa: BLE001 - authorization failures are intentionally opaque
            raise TaskGrantUnavailable() from None

    def consume_read(
        self, *, token_sha256: bytes, attachment_id: UUID
    ) -> DownloadAsset:
        try:
            with self._connection() as connection, connection.transaction():
                row = connection.execute(
                    self._asset_query()
                    + "join platform_attachments.task_grants grant_row "
                    "on grant_row.attachment_id=attachment.attachment_id "
                    "where attachment.attachment_id=%s "
                    "and grant_row.token_sha256=%s and grant_row.scope='read'",
                    (attachment_id, token_sha256),
                ).fetchone()
                if row is None:
                    raise TaskGrantUnavailable()
                asset = self._asset_from_row(row)
                connection.execute(
                    "select platform_attachments.consume_task_grant_gateway_v64("
                    "%s,%s,%s)",
                    (token_sha256, attachment_id, asset.size_bytes),
                )
            return asset
        except TaskGrantError:
            raise
        except Exception:  # noqa: BLE001 - authorization failures are intentionally opaque
            raise TaskGrantUnavailable() from None


class AttachmentGrantService:
    def __init__(
        self,
        repository,
        store,
        *,
        grant_seconds: int = 15 * 60,
        max_reads: int = 3,
        token_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if (
            isinstance(grant_seconds, bool)
            or not isinstance(grant_seconds, int)
            or not 0 < grant_seconds <= _MAX_GRANT_SECONDS
            or isinstance(max_reads, bool)
            or not isinstance(max_reads, int)
            or not 0 < max_reads <= 10
        ):
            raise ValueError("attachment grant limits invalid")
        self._repository = repository
        self._store = store
        self._grant_seconds = grant_seconds
        self._max_reads = max_reads
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(32))
        self._clock = clock or (lambda: datetime.now(UTC))
        self._temporary_root: str | Path | None = None

    def _token(self) -> str:
        token = self._token_factory()
        bearer_token_sha256(token)
        return token

    def _expiry(self, expires_at: datetime | None) -> datetime:
        now = self._clock()
        selected = expires_at or now + timedelta(seconds=self._grant_seconds)
        if (
            not isinstance(selected, datetime)
            or selected.tzinfo is None
            or selected.utcoffset() is None
            or selected <= now
            or selected > now + timedelta(seconds=_MAX_GRANT_SECONDS)
        ):
            raise ValueError("attachment grant expiry invalid")
        return selected.astimezone(UTC)

    @staticmethod
    def _identity(task_id: UUID, agent_id: str) -> None:
        if not isinstance(task_id, UUID) or not isinstance(agent_id, str) or _AGENT_ID.fullmatch(agent_id) is None:
            raise ValueError("attachment grant subject invalid")

    def issue_attachment(
        self,
        task_id: UUID,
        attachment_id: UUID,
        agent_id: str,
        *,
        expires_at: datetime | None = None,
    ) -> TaskAttachmentGrant:
        self._identity(task_id, agent_id)
        if not isinstance(attachment_id, UUID):
            raise TypeError("attachment grant subject invalid")
        token = self._token()
        selected_expiry = self._expiry(expires_at)
        asset = self._repository.issue_read(
            grant_id=uuid4(),
            token_sha256=bearer_token_sha256(token),
            task_id=task_id,
            attachment_id=attachment_id,
            agent_id=agent_id,
            expires_at=selected_expiry,
            max_reads=self._max_reads,
        )
        return TaskAttachmentGrant(
            attachment_id=asset.attachment_id,
            display_name=asset.display_name,
            detected_mime=asset.media_type,
            size_bytes=asset.size_bytes,
            sha256_hex=asset.sha256.hex(),
            download_url=(
                f"/api/v1/execution-worker/attachments/{asset.attachment_id}/content"
            ),
            bearer_token=token,
            expires_at=selected_expiry,
        )

    def issue_output(
        self,
        task_id: UUID,
        agent_id: str,
        *,
        expires_at: datetime | None = None,
        max_files: int = MAX_TASK_OUTPUT_FILES,
        max_total_bytes: int = MAX_TASK_OUTPUT_BYTES,
        max_file_bytes: int = 50 * 1024 * 1024,
    ) -> OutputWriteGrant:
        self._identity(task_id, agent_id)
        if (
            isinstance(max_files, bool)
            or not isinstance(max_files, int)
            or not 0 < max_files <= MAX_TASK_OUTPUT_FILES
            or isinstance(max_total_bytes, bool)
            or not isinstance(max_total_bytes, int)
            or not 0 < max_total_bytes <= MAX_TASK_OUTPUT_BYTES
            or isinstance(max_file_bytes, bool)
            or not isinstance(max_file_bytes, int)
            or not 0 < max_file_bytes <= 50 * 1024 * 1024
            or max_file_bytes > max_total_bytes
        ):
            raise ValueError("output grant limits invalid")
        token = self._token()
        selected_expiry = self._expiry(expires_at)
        self._repository.issue_output(
            grant_id=uuid4(),
            token_sha256=bearer_token_sha256(token),
            task_id=task_id,
            agent_id=agent_id,
            expires_at=selected_expiry,
            max_files=max_files,
            max_total_bytes=max_total_bytes,
            max_file_bytes=max_file_bytes,
        )
        return OutputWriteGrant(
            task_id=task_id,
            agent_id=agent_id,
            upload_url=f"/api/v1/execution-worker/tasks/{task_id}/artifacts",
            bearer_token=token,
            max_files=max_files,
            max_total_bytes=max_total_bytes,
        )

    @staticmethod
    def _stream_file(path: Path, directory: Path) -> Iterator[bytes]:
        try:
            with path.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    yield chunk
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def open_attachment(self, bearer_token: str, attachment_id: UUID) -> OpenedDownload:
        if not isinstance(attachment_id, UUID):
            raise TaskGrantUnavailable()
        token_sha256 = bearer_token_sha256(bearer_token)
        asset = self._repository.consume_read(
            token_sha256=token_sha256, attachment_id=attachment_id
        )
        directory = Path(
            tempfile.mkdtemp(prefix="task-attachment-read-", dir=self._temporary_root)
        )
        try:
            staged = self._store.stage_verified(asset, directory)
        except Exception:  # noqa: BLE001 - storage failures are intentionally opaque
            shutil.rmtree(directory, ignore_errors=True)
            raise TaskGrantUnavailable() from None
        return OpenedDownload(
            stream=self._stream_file(staged, directory),
            status_code=200,
            media_type=asset.media_type,
            headers={
                "Content-Disposition": _content_disposition(asset.display_name, False),
                "Content-Length": str(asset.size_bytes),
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )
