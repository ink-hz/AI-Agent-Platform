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
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row

from app.control_plane.crypto import IdentityKeyring
from app.control_plane.dsn import validate_control_dsn
from app.execution_relay.content_crypto import ContentCodec, SealedContent
from app.local_secrets import read_secret_file

from .conversation_repository import attachment_object_subject
from .derivatives import Derivative, DerivativeBuilder
from .object_writer import _credential
from .scanner import ClamAVScanner
from .validation import AttachmentValidator, OpenedObject, ValidationResult
from .worker import AttachmentProcessor, ProcessingJob, StoredDerivative


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

    def record_derivative(
        self,
        job: ProcessingJob,
        derivative: Derivative,
        stored: StoredDerivative,
    ) -> None:
        derivative_id = uuid4()
        if not hasattr(self._content_codec, "seal_json"):
            raise AttachmentWorkerRuntimeError()
        try:
            sealed = self._content_codec.seal_json(
                derivative_object_subject(job.attachment_id, derivative_id),
                {"object_ref": stored.object_ref},
            )
            with self._connection() as connection:
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
                raise AttachmentWorkerRuntimeError()
        except AttachmentWorkerRuntimeError:
            raise
        except Exception:  # noqa: BLE001 - DB/codec errors are sanitized
            raise AttachmentWorkerRuntimeError() from None


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
            response = self._client.get_object(Bucket=self._bucket, Key=object_ref)
            if int(response["ContentLength"]) != size:
                response["Body"].close()
                raise AttachmentWorkerRuntimeError()
            return OpenedObject(response["Body"], size)
        except AttachmentWorkerRuntimeError:
            raise
        except Exception:  # noqa: BLE001 - storage adapter errors are sanitized
            raise AttachmentWorkerRuntimeError() from None

    def put_derivative(self, data: bytes) -> StoredDerivative:
        if not isinstance(data, bytes) or not data:
            raise AttachmentWorkerRuntimeError()
        object_ref = secrets.token_hex(32)
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
    return AttachmentProcessor(
        repository=AttachmentProcessingRepository(database_url, content_codec=codec),
        object_store=S3ProcessingObjectStore(
            client,
            os.getenv("PLATFORM_ATTACHMENT_S3_BUCKET", "orbbec-agent-attachments"),
        ),
        validator=AttachmentValidator(),
        scanner=ClamAVScanner(),
        derivatives=DerivativeBuilder(pdftoppm_path=renderer),
        worker_id=worker_id,
    )


async def run() -> None:
    processor = build_processor()
    while True:
        changed = await processor.process_next()
        if not changed:
            await asyncio.sleep(1.0)


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()
