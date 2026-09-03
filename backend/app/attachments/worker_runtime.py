from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

import psycopg
from psycopg.rows import dict_row

from app.control_plane.crypto import IdentityKeyring
from app.control_plane.dsn import validate_control_dsn
from app.execution_relay.content_crypto import ContentCodec, SealedContent
from app.local_secrets import read_secret_file

from .conversation_repository import attachment_object_subject
from .derivatives import BubblewrapPdfSandbox, Derivative, DerivativeBuilder
from .object_writer import _credential
from .scanner import ClamAVScanner
from .validation import AttachmentValidator, OpenedObject, ValidationResult
from .worker import (
    AttachmentProcessor,
    DerivativeFinalizeError,
    ProcessingJob,
    ReconciliationStatus,
    StoredDerivative,
)


class AttachmentWorkerRuntimeError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("attachment worker unavailable")


def derivative_object_subject(attachment_id: UUID, derivative_id: UUID) -> str:
    return f"attachment:{attachment_id}:derivative:{derivative_id}:object-ref"


class AttachmentProcessingRepository:
    def __init__(
        self,
        control_database_url: str,
        *,
        content_codec: ContentCodec,
        connect: Callable[..., Any] = psycopg.connect,
    ) -> None:
        parsed = validate_control_dsn(control_database_url, purpose="brain")
        # Read-only test codecs need only unsealing until derivative recording.
        if not hasattr(content_codec, "unseal_json"):
            raise TypeError("content codec required")
        self.environment = parsed.environment
        self._control_database_url = control_database_url
        self._content_codec = content_codec
        self._connect = connect

    def __repr__(self) -> str:
        return (
            "AttachmentProcessingRepository("
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

    def claim(self, worker_id: str) -> ProcessingJob | None:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select (platform_attachments."
                    "claim_attachment_processing_job_v64(%s)).*",
                    (worker_id,),
                ).fetchone()
                if row is None or row["processing_job_id"] is None:
                    return None
                attachment = connection.execute(
                    "select attachment.object_ref_ciphertext,"
                    "attachment.object_ref_key_version,attachment.size_bytes,"
                    "attachment.sha256,attachment.detected_mime,"
                    "upload.write_attempt_id "
                    "from platform_attachments.attachments attachment "
                    "left join platform_attachments.uploads upload "
                    "on upload.attachment_id=attachment.attachment_id "
                    "where attachment.attachment_id=%s",
                    (row["attachment_id"],),
                ).fetchone()
                if attachment is None or attachment["sha256"] is None:
                    raise AttachmentWorkerRuntimeError()
                subject = attachment_object_subject(
                    row["attachment_id"], attachment["write_attempt_id"]
                )
                value = self._content_codec.unseal_json(
                    subject,
                    SealedContent(
                        bytes(attachment["object_ref_ciphertext"]),
                        int(attachment["object_ref_key_version"]),
                    ),
                )
                if set(value) != {"object_ref"} or not isinstance(
                    value["object_ref"], str
                ):
                    raise AttachmentWorkerRuntimeError()
                return ProcessingJob(
                    row["processing_job_id"],
                    row["attachment_id"],
                    row["job_kind"],
                    row["derivative_kind"],
                    value["object_ref"],
                    int(attachment["size_bytes"]),
                    bytes(attachment["sha256"]),
                    attachment["detected_mime"],
                )
        except AttachmentWorkerRuntimeError:
            raise
        except Exception:  # noqa: BLE001 - DB/codec errors are sanitized
            raise AttachmentWorkerRuntimeError() from None

    def record_result(
        self,
        job: ProcessingJob,
        state: str,
        reason: str | None,
        *,
        validation: ValidationResult | None = None,
    ) -> None:
        detected_mime = validation.detected_mime if validation else None
        coverage = (
            json.dumps(
                dict(validation.coverage),
                sort_keys=True,
                separators=(",", ":"),
            )
            if validation
            else None
        )
        try:
            with self._connection() as connection:
                connection.execute(
                    "select platform_attachments."
                    "record_attachment_processing_result_v64("
                    "%s,%s,%s,%s,%s::jsonb)",
                    (
                        job.processing_job_id,
                        state,
                        reason,
                        detected_mime,
                        coverage,
                    ),
                )
        except Exception:  # noqa: BLE001 - DB adapter errors are sanitized
            raise AttachmentWorkerRuntimeError() from None

    def reconcile_result(
        self, job: ProcessingJob, state: str
    ) -> ReconciliationStatus:
        expected_job = "queued" if state == "retry" else (
            "completed" if state in {"scanning", "ready"} else "failed"
        )
        expected_attachment = {
            "scanning": "scanning",
            "ready": "ready",
            "quarantined": "quarantined",
            "rejected": "rejected",
        }.get(state)
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select job.state as job_state,attachment.state as attachment_state "
                    "from platform_attachments.processing_jobs job "
                    "join platform_attachments.attachments attachment "
                    "on attachment.attachment_id=job.attachment_id "
                    "where job.processing_job_id=%s and job.attachment_id=%s",
                    (job.processing_job_id, job.attachment_id),
                ).fetchone()
            if not row:
                return ReconciliationStatus.UNKNOWN
            if state == "retry":
                predecessor = {
                    "validate": "validating",
                    "scan": "scanning",
                    "derive": "ready",
                }.get(job.job_kind)
                committed = (
                    row["job_state"] == expected_job
                    and row["attachment_state"] == predecessor
                ) or (
                    row["job_state"] == "failed"
                    and row["attachment_state"]
                    == ("ready" if job.job_kind == "derive" else "rejected")
                )
                if committed:
                    return ReconciliationStatus.COMMITTED
            elif (
                row["job_state"] == expected_job
                and (
                    expected_attachment is None
                    or row["attachment_state"] == expected_attachment
                )
            ):
                return ReconciliationStatus.COMMITTED
            if row["job_state"] == "running":
                return ReconciliationStatus.RUNNING
            return ReconciliationStatus.UNKNOWN
        except Exception:  # noqa: BLE001 - uncertainty must not cause mutation
            return ReconciliationStatus.UNKNOWN

    def record_derivative(
        self,
        job: ProcessingJob,
        derivative: Derivative,
        stored: StoredDerivative,
    ) -> None:
        derivative_id = uuid5(
            NAMESPACE_URL,
            f"platform-attachment-derivative-v64:{job.processing_job_id}:{job.derivative_kind or derivative.kind}",
        )
        if not hasattr(self._content_codec, "seal_json"):
            raise DerivativeFinalizeError(ambiguous=False)
        executed = False
        try:
            sealed = self._content_codec.seal_json(
                derivative_object_subject(job.attachment_id, derivative_id),
                {"object_ref": stored.object_ref},
            )
            with self._connection() as connection:
                executed = True
                row = connection.execute(
                    "select platform_attachments."
                    "record_attachment_derivative_v64("
                    "%s,%s,%s,%s,%s,%s,%s,%s,null) as derivative_id",
                    (
                        job.processing_job_id,
                        derivative_id,
                        job.derivative_kind or derivative.kind,
                        sealed.ciphertext,
                        sealed.key_version,
                        derivative.detected_mime,
                        stored.size_bytes,
                        stored.sha256,
                    ),
                ).fetchone()
            if row is None or row["derivative_id"] != derivative_id:
                raise DerivativeFinalizeError(ambiguous=True)
        except DerivativeFinalizeError:
            raise
        except Exception:  # noqa: BLE001 - DB/codec errors are sanitized
            raise DerivativeFinalizeError(ambiguous=executed) from None

    def reconcile_derivative(
        self, job: ProcessingJob, stored: StoredDerivative
    ) -> ReconciliationStatus:
        derivative_id = uuid5(
            NAMESPACE_URL,
            f"platform-attachment-derivative-v64:{job.processing_job_id}:{job.derivative_kind}",
        )
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select derivative.derivative_id,derivative.size_bytes,"
                    "derivative.sha256,job.state as job_state "
                    "from platform_attachments.derivatives derivative "
                    "join platform_attachments.processing_jobs job "
                    "on job.attachment_id=derivative.attachment_id "
                    "where derivative.derivative_id=%s "
                    "and job.processing_job_id=%s",
                    (derivative_id, job.processing_job_id),
                ).fetchone()
            if (
                row
                and row["job_state"] == "completed"
                and int(row["size_bytes"]) == stored.size_bytes
                and bytes(row["sha256"]) == stored.sha256
            ):
                return ReconciliationStatus.COMMITTED
            if row and row["job_state"] == "running":
                return ReconciliationStatus.RUNNING
            return ReconciliationStatus.UNKNOWN
        except Exception:  # noqa: BLE001 - uncertainty must not cause mutation
            return ReconciliationStatus.UNKNOWN


class S3ProcessingObjectStore:
    def __init__(self, client, bucket: str) -> None:
        if not isinstance(bucket, str) or not bucket.strip():
            raise ValueError("attachment bucket invalid")
        self._client = client
        self._bucket = bucket

    def __repr__(self) -> str:
        return "S3ProcessingObjectStore(client=<redacted>, bucket=<redacted>)"

    def open(self, object_ref: str) -> OpenedObject:
        try:
            head = self._client.head_object(Bucket=self._bucket, Key=object_ref)
            size = int(head["ContentLength"])
            get_args = {"Bucket": self._bucket, "Key": object_ref}
            version_id = head.get("VersionId")
            etag = head.get("ETag")
            if isinstance(version_id, str) and version_id and version_id != "null":
                get_args["VersionId"] = version_id
            elif isinstance(etag, str) and etag:
                get_args["IfMatch"] = etag
            response = self._client.get_object(**get_args)
            if int(response["ContentLength"]) != size:
                response["Body"].close()
                raise AttachmentWorkerRuntimeError()
            return OpenedObject(response["Body"], size)
        except AttachmentWorkerRuntimeError:
            raise
        except Exception:  # noqa: BLE001 - storage adapter errors are sanitized
            raise AttachmentWorkerRuntimeError() from None

    def put_derivative(self, data: bytes, *, object_key: str) -> StoredDerivative:
        if (
            not isinstance(data, bytes)
            or not data
            or not isinstance(object_key, str)
            or len(object_key) != 64
            or any(character not in "0123456789abcdef" for character in object_key)
        ):
            raise AttachmentWorkerRuntimeError()
        object_ref = object_key
        digest = hashlib.sha256(data).digest()
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=object_ref,
                Body=data,
                ContentLength=len(data),
                ContentType="application/octet-stream",
            )
            return StoredDerivative(object_ref, len(data), digest)
        except Exception:  # noqa: BLE001 - storage adapter errors are sanitized
            with suppress(Exception):
                self._client.delete_object(Bucket=self._bucket, Key=object_ref)
            raise AttachmentWorkerRuntimeError() from None

    def delete(self, object_ref: str) -> None:
        try:
            self._client.delete_object(Bucket=self._bucket, Key=object_ref)
        except Exception:  # noqa: BLE001 - storage adapter errors are sanitized
            raise AttachmentWorkerRuntimeError() from None


def _required_absolute_path(name: str) -> Path:
    value = os.getenv(name, "")
    path = Path(value)
    if not value or not path.is_absolute():
        raise AttachmentWorkerRuntimeError()
    return path


def build_processor() -> AttachmentProcessor:
    import boto3
    from botocore.config import Config as BotoConfig

    database_file = _required_absolute_path(
        "PLATFORM_ATTACHMENT_WORKER_DATABASE_URL_FILE"
    )
    keyring_file = _required_absolute_path("PLATFORM_CONTENT_ENCRYPTION_KEYRING_FILE")
    access_file = _required_absolute_path("PLATFORM_ATTACHMENT_S3_ACCESS_KEY_FILE")
    secret_file = _required_absolute_path("PLATFORM_ATTACHMENT_S3_SECRET_KEY_FILE")
    database_url = read_secret_file(str(database_file))
    keyring = IdentityKeyring.from_file(
        str(keyring_file),
        expected_purpose="platform-content-encryption",
        expected_key_length=32,
    )
    codec = ContentCodec(keyring)
    client = boto3.client(
        "s3",
        endpoint_url=os.getenv("PLATFORM_ATTACHMENT_S3_ENDPOINT", "").strip(),
        region_name="us-east-1",
        aws_access_key_id=_credential(str(access_file)),
        aws_secret_access_key=_credential(str(secret_file)),
        config=BotoConfig(s3={"addressing_style": "path"}, retries={"max_attempts": 2}),
    )
    worker_id = os.getenv(
        "PLATFORM_ATTACHMENT_WORKER_ID",
        f"platform-attachments.{secrets.token_hex(4)}",
    ).strip()
    renderer = os.getenv("PLATFORM_ATTACHMENT_PDFTOPPM_PATH", "/usr/bin/pdftoppm")
    bubblewrap = os.getenv("PLATFORM_ATTACHMENT_BWRAP_PATH", "/usr/bin/bwrap")
    return AttachmentProcessor(
        repository=AttachmentProcessingRepository(database_url, content_codec=codec),
        object_store=S3ProcessingObjectStore(
            client,
            os.getenv("PLATFORM_ATTACHMENT_S3_BUCKET", "orbbec-agent-attachments"),
        ),
        validator=AttachmentValidator(),
        scanner=ClamAVScanner(),
        derivatives=DerivativeBuilder(
            sandbox_runner=BubblewrapPdfSandbox(
                bubblewrap_path=bubblewrap,
                pdftoppm_path=renderer,
            )
        ),
        worker_id=worker_id,
    )


async def run(
    processor: AttachmentProcessor | None = None,
    *,
    sleep: Callable[[float], Any] = asyncio.sleep,
    max_iterations: int | None = None,
) -> None:
    processor = processor or build_processor()
    failures = 0
    iterations = 0
    while True:
        if max_iterations is not None and iterations >= max_iterations:
            return
        iterations += 1
        try:
            changed = await processor.process_next()
            failures = 0
        except Exception:  # noqa: BLE001 - one malformed job must not kill worker
            failures += 1
            await sleep(min(30.0, float(2 ** min(failures - 1, 5))))
            continue
        if not changed:
            await sleep(1.0)


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()
