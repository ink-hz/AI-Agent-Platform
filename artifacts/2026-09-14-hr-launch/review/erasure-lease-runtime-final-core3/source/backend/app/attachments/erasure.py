from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from app.control_plane.dsn import validate_control_dsn
from app.execution_relay.content_crypto import ContentCodec, SealedContent

from .conversation_repository import attachment_object_subject
from .object_keys import derivative_object_key


class AttachmentErasureError(RuntimeError):
    pass


@dataclass(frozen=True)
class ErasureJob:
    erasure_job_id: UUID
    attachment_id: UUID
    attempt_token: UUID = field(repr=False)
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

    def _load_object_refs(self, attachment_id: UUID) -> tuple[str, ...]:
        with self._connection() as connection:
            base = connection.execute(
                "select attachment.object_ref_ciphertext,attachment.object_ref_key_version,"
                "upload.write_attempt_id from platform_attachments.attachments attachment "
                "left join platform_attachments.uploads upload using (attachment_id) "
                "where attachment.attachment_id=%s",
                (attachment_id,),
            ).fetchone()
            attempts = connection.execute(
                "select attempt_id,object_ref_ciphertext,object_ref_key_version from "
                "platform_attachments.upload_write_attempts where attachment_id=%s",
                (attachment_id,),
            ).fetchall()
            derivatives = connection.execute(
                "select derivative_id,object_ref_ciphertext,object_ref_key_version from "
                "platform_attachments.derivatives where attachment_id=%s",
                (attachment_id,),
            ).fetchall()
            derive_jobs = connection.execute(
                "select processing_job_id,derivative_kind from "
                "platform_attachments.processing_jobs where attachment_id=%s "
                "and job_kind='derive' order by processing_job_id",
                (attachment_id,),
            ).fetchall()
            if base is None:
                raise AttachmentErasureError()
            refs = [
                self._object_ref(
                    attachment_object_subject(attachment_id, base["write_attempt_id"]),
                    base["object_ref_ciphertext"],
                    base["object_ref_key_version"],
                )
            ]
            refs.extend(
                self._object_ref(
                    attachment_object_subject(attachment_id, row["attempt_id"]),
                    row["object_ref_ciphertext"],
                    row["object_ref_key_version"],
                )
                for row in attempts
            )
            refs.extend(
                self._object_ref(
                    f"attachment:{attachment_id}:derivative:"
                    f"{row['derivative_id']}:object-ref",
                    row["object_ref_ciphertext"],
                    row["object_ref_key_version"],
                )
                for row in derivatives
            )
            refs.extend(
                derivative_object_key(row["processing_job_id"], row["derivative_kind"])
                for row in derive_jobs
            )
            return tuple(dict.fromkeys(refs))

    def claim(self, worker_id: str) -> ErasureJob | None:
        if not isinstance(worker_id, str) or not 1 <= len(worker_id) <= 128:
            raise ValueError("erasure worker invalid")
        try:
            # Commit the claim before reading encrypted references. A process exit or
            # bad keyring therefore consumes a bounded attempt and remains recoverable.
            with self._connection() as connection:
                row = connection.execute(
                    "select * from platform_attachments."
                    "claim_attachment_erasure_job_v107(%s)",
                    (worker_id,),
                ).fetchone()
            if row is None or row["erasure_job_id"] is None:
                return None
            claimed = ErasureJob(
                row["erasure_job_id"], row["attachment_id"], row["attempt_token"], ()
            )
            try:
                refs = self._load_object_refs(claimed.attachment_id)
            except Exception as error:
                self._record_state(
                    claimed,
                    state="partial",
                    reason="reference_load_failed",
                    status={
                        "object_count": 0,
                        "deleted_count": 0,
                        "failed_count": 1,
                        "reference_load_failed": True,
                    },
                )
                raise AttachmentErasureError() from error
            return ErasureJob(
                claimed.erasure_job_id,
                claimed.attachment_id,
                claimed.attempt_token,
                refs,
            )
        except AttachmentErasureError:
            raise
        except Exception as error:
            raise AttachmentErasureError() from error

    def renew(self, job: ErasureJob) -> bool:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select platform_attachments."
                    "renew_attachment_erasure_lease_v107(%s,%s) as renewed",
                    (job.erasure_job_id, job.attempt_token),
                ).fetchone()
            return bool(row and row["renewed"] is True)
        except Exception as error:
            raise AttachmentErasureError() from error

    def _record_state(
        self,
        job: ErasureJob,
        *,
        state: str,
        reason: str,
        status: dict[str, Any],
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                "select platform_attachments.record_attachment_erasure_result_v107("
                "%s,%s,%s,%s,%s::jsonb)",
                (
                    job.erasure_job_id,
                    job.attempt_token,
                    state,
                    reason,
                    json.dumps(status, sort_keys=True, separators=(",", ":")),
                ),
            )

    def record(self, job: ErasureJob, *, failed: int) -> None:
        state = "partial" if failed else "completed"
        status = {
            "object_count": len(job.object_refs),
            "deleted_count": len(job.object_refs) - failed,
            "failed_count": failed,
        }
        try:
            self._record_state(
                job,
                state=state,
                reason="object_delete_incomplete" if failed else "erased",
                status=status,
            )
        except Exception as error:
            raise AttachmentErasureError() from error


class _LeaseHeartbeat:
    def __init__(self, repository, job: ErasureJob, interval: float) -> None:
        if not isinstance(interval, (int, float)) or not 0 < interval <= 60:
            raise ValueError("erasure heartbeat interval invalid")
        self._repository = repository
        self._job = job
        self._interval = float(interval)
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"attachment-erasure-lease-{job.erasure_job_id}",
            daemon=True,
        )

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                if not self._repository.renew(self._job):
                    self._lost.set()
                    return
            except Exception:  # noqa: BLE001 - lease loss is reported to owner thread
                self._lost.set()
                return

    def start(self) -> None:
        self._thread.start()

    def require_current(self) -> None:
        if self._lost.is_set():
            raise AttachmentErasureError("attachment erasure lease lost")

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=15)
        if self._thread.is_alive():
            self._lost.set()
        self.require_current()


class AttachmentErasureService:
    def __init__(
        self,
        repository: AttachmentErasureRepository,
        object_store,
        *,
        heartbeat_interval: float = 30.0,
    ) -> None:
        self._repository = repository
        self._object_store = object_store
        self._heartbeat_interval = heartbeat_interval

    def process_next(self, worker_id: str) -> bool:
        job = self._repository.claim(worker_id)
        if job is None:
            return False
        heartbeat = _LeaseHeartbeat(
            self._repository, job, self._heartbeat_interval
        )
        heartbeat.start()
        failed = 0
        try:
            for object_ref in job.object_refs:
                heartbeat.require_current()
                try:
                    self._object_store.delete(object_ref)
                except Exception:  # noqa: BLE001 - failed object stays retryable
                    failed += 1
                heartbeat.require_current()
        finally:
            heartbeat.stop()
        # The final synchronous renewal closes the gap between heartbeat shutdown
        # and the token-checked record transaction.
        if not self._repository.renew(job):
            raise AttachmentErasureError("attachment erasure lease lost")
        self._repository.record(job, failed=failed)
        return True
