from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from app.control_plane.dsn import validate_control_dsn
from app.execution_relay.content_crypto import ContentCodec, SealedContent

from .conversation_repository import attachment_object_subject


class AttachmentErasureError(RuntimeError):
    pass


@dataclass(frozen=True)
class ErasureJob:
    erasure_job_id: UUID
    attachment_id: UUID
    object_refs: tuple[str, ...] = field(repr=False)


class AttachmentErasureRepository:
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

    def _object_ref(self, subject: str, ciphertext, key_version) -> str:
        document = self._content_codec.unseal_json(
            subject, SealedContent(bytes(ciphertext), int(key_version))
        )
        if set(document) != {"object_ref"} or not isinstance(document["object_ref"], str):
            raise AttachmentErasureError()
        return document["object_ref"]

    def claim(self, worker_id: str) -> ErasureJob | None:
        if not isinstance(worker_id, str) or not 1 <= len(worker_id) <= 128:
            raise ValueError("erasure worker invalid")
        try:
            with self._connection() as connection:
                job = connection.execute(
                    "select (platform_attachments.claim_attachment_erasure_job_v64(%s)).*",
                    (worker_id,),
                ).fetchone()
                if job is None or job["erasure_job_id"] is None:
                    return None
                base = connection.execute(
                    "select attachment.object_ref_ciphertext,attachment.object_ref_key_version,"
                    "upload.write_attempt_id from platform_attachments.attachments attachment "
                    "left join platform_attachments.uploads upload using (attachment_id) "
                    "where attachment.attachment_id=%s",
                    (job["attachment_id"],),
                ).fetchone()
                attempts = connection.execute(
                    "select attempt_id,object_ref_ciphertext,object_ref_key_version from "
                    "platform_attachments.upload_write_attempts where attachment_id=%s",
                    (job["attachment_id"],),
                ).fetchall()
                derivatives = connection.execute(
                    "select derivative_id,object_ref_ciphertext,object_ref_key_version from "
                    "platform_attachments.derivatives where attachment_id=%s",
                    (job["attachment_id"],),
                ).fetchall()
            if base is None:
                raise AttachmentErasureError()
            refs = [self._object_ref(
                attachment_object_subject(job["attachment_id"], base["write_attempt_id"]),
                base["object_ref_ciphertext"], base["object_ref_key_version"],
            )]
            refs.extend(self._object_ref(
                attachment_object_subject(job["attachment_id"], row["attempt_id"]),
                row["object_ref_ciphertext"], row["object_ref_key_version"],
            ) for row in attempts)
            refs.extend(self._object_ref(
                f"attachment:{job['attachment_id']}:derivative:{row['derivative_id']}:object-ref",
                row["object_ref_ciphertext"], row["object_ref_key_version"],
            ) for row in derivatives)
            return ErasureJob(
                job["erasure_job_id"], job["attachment_id"], tuple(dict.fromkeys(refs))
            )
        except AttachmentErasureError:
            raise
        except Exception as error:
            raise AttachmentErasureError() from error

    def record(self, job: ErasureJob, *, failed: int) -> None:
        state = "partial" if failed else "completed"
        status = {
            "object_count": len(job.object_refs),
            "deleted_count": len(job.object_refs) - failed,
            "failed_count": failed,
        }
        try:
            with self._connection() as connection:
                connection.execute(
                    "select platform_attachments.record_attachment_erasure_result_v64("
                    "%s,%s,%s,%s::jsonb)",
                    (
                        job.erasure_job_id,
                        state,
                        "object_delete_incomplete" if failed else "erased",
                        json.dumps(status, sort_keys=True, separators=(",", ":")),
                    ),
                )
        except Exception as error:
            raise AttachmentErasureError() from error


class AttachmentErasureService:
    def __init__(self, repository: AttachmentErasureRepository, object_store) -> None:
        self._repository = repository
        self._object_store = object_store

    def process_next(self, worker_id: str) -> bool:
        job = self._repository.claim(worker_id)
        if job is None:
            return False
        failed = 0
        for object_ref in job.object_refs:
            try:
                self._object_store.delete(object_ref)
            except Exception:  # noqa: BLE001 - every failed object stays retryable
                failed += 1
        self._repository.record(job, failed=failed)
        return True
