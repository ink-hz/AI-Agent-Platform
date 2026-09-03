from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row

from app.control_plane.dsn import validate_control_dsn
from app.execution_relay.content_crypto import ContentCodec


class AttachmentRetentionError(RuntimeError):
    pass


@dataclass(frozen=True)
class RetentionCandidate:
    attachment_id: UUID
    retained_until: datetime
    upload_expires_at: datetime | None
    upload_state: str | None

    def reason(self, now: datetime) -> str | None:
        if self.retained_until <= now:
            return "retention_expired"
        if (
            self.upload_state == "uploading"
            and self.upload_expires_at is not None
            and self.upload_expires_at <= now
        ):
            return "orphan_upload_expired"
        return None


class AttachmentRetentionRepository:
    def __init__(
        self,
        database_url: str,
        *,
        content_codec: ContentCodec,
        connect: Callable[..., Any] = psycopg.connect,
    ) -> None:
        validate_control_dsn(database_url, purpose="maintenance")
        if not isinstance(content_codec, ContentCodec):
            raise TypeError("content codec required")
        self._database_url = database_url
        self._content_codec = content_codec
        self._connect = connect

    def _connection(self):
        return self._connect(
            self._database_url,
            connect_timeout=3,
            options="-c statement_timeout=10000 -c timezone=UTC",
            row_factory=dict_row,
        )

    def due(self, *, limit: int = 100) -> tuple[RetentionCandidate, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("retention batch invalid")
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "select attachment.attachment_id,attachment.retained_until,"
                    "upload.expires_at as upload_expires_at,upload.state as upload_state "
                    "from platform_attachments.attachments attachment left join "
                    "platform_attachments.uploads upload using (attachment_id) where "
                    "attachment.state<>'deleted' and (attachment.retained_until<=now() or "
                    "(upload.state='uploading' and upload.expires_at<=now())) and not exists "
                    "(select 1 from platform_attachments.erasure_jobs erasure where "
                    "erasure.attachment_id=attachment.attachment_id and "
                    "erasure.state in ('queued','running','partial')) order by "
                    "least(attachment.retained_until,coalesce(upload.expires_at,"
                    "attachment.retained_until)),attachment.attachment_id limit %s",
                    (limit,),
                ).fetchall()
            return tuple(RetentionCandidate(**row) for row in rows)
        except Exception as error:
            raise AttachmentRetentionError() from error

    def expire_grants(self, *, limit: int = 100) -> int:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("retention batch invalid")
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select platform_attachments.expire_task_grants_v64(%s) as count",
                    (limit,),
                ).fetchone()
            return int(row["count"])
        except Exception as error:
            raise AttachmentRetentionError() from error

    def schedule(self, candidate: RetentionCandidate, reason: str) -> None:
        job_id = uuid4()
        sealed = self._content_codec.seal_json(
            f"attachment:{candidate.attachment_id}:erasure:{job_id}:reason",
            {"reason": reason},
        )
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select platform_attachments.schedule_attachment_retention_v64("
                    "%s,%s,%s,%s,%s) as attachment_id",
                    (
                        job_id,
                        candidate.attachment_id,
                        sealed.ciphertext,
                        sealed.key_version,
                        hashlib.sha256(reason.encode("ascii")).digest(),
                    ),
                ).fetchone()
            if row is None or row["attachment_id"] != candidate.attachment_id:
                raise AttachmentRetentionError()
        except AttachmentRetentionError:
            raise
        except Exception as error:
            raise AttachmentRetentionError() from error


class AttachmentRetentionService:
    def __init__(
        self,
        repository: AttachmentRetentionRepository,
        *,
        clock: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._clock = clock

    def run_once(self, *, limit: int = 100) -> int:
        now = self._clock()
        scheduled = self._repository.expire_grants(limit=limit)
        for candidate in self._repository.due(limit=limit):
            reason = candidate.reason(now)
            if reason is None:
                continue
            self._repository.schedule(candidate, reason)
            scheduled += 1
        return scheduled
