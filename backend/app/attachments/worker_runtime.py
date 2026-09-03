from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import sys
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
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
from .erasure import AttachmentErasureRepository, AttachmentErasureService
from .object_writer import _credential
from .retention import AttachmentRetentionRepository, AttachmentRetentionService
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
                    "attachment.immutable_locator,"
                    "upload.write_attempt_id "
                    "from platform_attachments.attachments attachment "
                    "left join platform_attachments.uploads upload "
                    "on upload.attachment_id=attachment.attachment_id "
                    "where attachment.attachment_id=%s",
                    (row["attachment_id"],),
                ).fetchone()
                if attachment is None or attachment["sha256"] is None:
                    raise AttachmentWorkerRuntimeError()
                if not isinstance(row["attempt_token"], UUID):
                    raise AttachmentWorkerRuntimeError()
                if row["job_kind"] != "validate" and not isinstance(
                    attachment["immutable_locator"], str
                ):
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
                    attachment["immutable_locator"],
                    row["attempt_token"],
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
        if not isinstance(job.attempt_token, UUID):
            raise AttachmentWorkerRuntimeError()
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
                    "%s,%s,%s,%s,%s,%s::jsonb,%s)",
                    (
                        job.processing_job_id,
                        job.attempt_token,
                        state,
                        reason,
                        detected_mime,
                        coverage,
                        validation.immutable_locator if validation else None,
                    ),
                )
        except Exception:  # noqa: BLE001 - DB adapter errors are sanitized
            raise AttachmentWorkerRuntimeError() from None

    def reconcile_result(
        self, job: ProcessingJob, state: str
    ) -> ReconciliationStatus:
        if not isinstance(job.attempt_token, UUID):
            return ReconciliationStatus.UNKNOWN
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
                    "select job.state as job_state,attachment.state as attachment_state,"
                    "job.attempt_token "
                    "from platform_attachments.processing_jobs job "
                    "join platform_attachments.attachments attachment "
                    "on attachment.attachment_id=job.attachment_id "
                    "where job.processing_job_id=%s and job.attachment_id=%s",
                    (job.processing_job_id, job.attachment_id),
                ).fetchone()
            if not row:
                return ReconciliationStatus.UNKNOWN
            if row.get("attempt_token") != job.attempt_token:
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
        if not isinstance(job.attempt_token, UUID):
            raise DerivativeFinalizeError(ambiguous=False)
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
                    "%s,%s,%s,%s,%s,%s,%s,%s,%s,null) as derivative_id",
                    (
                        job.processing_job_id,
                        job.attempt_token,
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
        if not isinstance(job.attempt_token, UUID):
            return ReconciliationStatus.UNKNOWN
        derivative_id = uuid5(
            NAMESPACE_URL,
            f"platform-attachment-derivative-v64:{job.processing_job_id}:{job.derivative_kind}",
        )
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select derivative.derivative_id,derivative.size_bytes,"
                    "derivative.sha256,job.state as job_state,job.attempt_token "
                    "from platform_attachments.processing_jobs job "
                    "left join platform_attachments.derivatives derivative "
                    "on derivative.attachment_id=job.attachment_id "
                    "and derivative.derivative_id=%s "
                    "where job.processing_job_id=%s and job.attachment_id=%s",
                    (derivative_id, job.processing_job_id, job.attachment_id),
                ).fetchone()
            if not row or row["attempt_token"] != job.attempt_token:
                return ReconciliationStatus.UNKNOWN
            if (
                row
                and row["job_state"] == "completed"
                and int(row["size_bytes"]) == stored.size_bytes
                and bytes(row["sha256"]) == stored.sha256
            ):
                return ReconciliationStatus.COMMITTED
            if row["job_state"] == "running":
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

    def open(
        self, object_ref: str, immutable_locator: str | None = None
    ) -> OpenedObject:
        try:
            get_args = {"Bucket": self._bucket, "Key": object_ref}
            if immutable_locator is None:
                head = self._client.head_object(Bucket=self._bucket, Key=object_ref)
                size = int(head["ContentLength"])
                version_id = head.get("VersionId")
                etag = head.get("ETag")
                if isinstance(version_id, str) and version_id and version_id != "null":
                    immutable_locator = f"version:{version_id}"
                    get_args["VersionId"] = version_id
                elif isinstance(etag, str) and etag:
                    immutable_locator = f"etag:{etag}"
                    get_args["IfMatch"] = etag
                else:
                    raise AttachmentWorkerRuntimeError()
            elif immutable_locator.startswith("version:"):
                get_args["VersionId"] = immutable_locator.removeprefix("version:")
                size = None
            elif immutable_locator.startswith("etag:"):
                get_args["IfMatch"] = immutable_locator.removeprefix("etag:")
                size = None
            else:
                raise AttachmentWorkerRuntimeError()
            response = self._client.get_object(**get_args)
            response_size = int(response["ContentLength"])
            if size is not None and response_size != size:
                response["Body"].close()
                raise AttachmentWorkerRuntimeError()
            body = response["Body"]
            timeout_setter = getattr(body, "set_socket_timeout", None)
            return OpenedObject(
                body,
                response_size,
                immutable_locator,
                timeout_setter if callable(timeout_setter) else None,
            )
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


def _build_s3_client():
    import boto3
    from botocore.config import Config as BotoConfig

    access_file = _required_absolute_path("PLATFORM_ATTACHMENT_S3_ACCESS_KEY_FILE")
    secret_file = _required_absolute_path("PLATFORM_ATTACHMENT_S3_SECRET_KEY_FILE")
    return boto3.client(
        "s3",
        endpoint_url=os.getenv("PLATFORM_ATTACHMENT_S3_ENDPOINT", "").strip(),
        region_name="us-east-1",
        aws_access_key_id=_credential(str(access_file)),
        aws_secret_access_key=_credential(str(secret_file)),
        config=BotoConfig(
            connect_timeout=5,
            read_timeout=5,
            s3={"addressing_style": "path"},
            retries={"max_attempts": 2},
        ),
    )


def _build_content_codec() -> ContentCodec:
    keyring_file = _required_absolute_path("PLATFORM_CONTENT_ENCRYPTION_KEYRING_FILE")
    keyring = IdentityKeyring.from_file(
        str(keyring_file),
        expected_purpose="platform-content-encryption",
        expected_key_length=32,
    )
    return ContentCodec(keyring)


def _scanner() -> ClamAVScanner:
    return ClamAVScanner(
        host=os.getenv("PLATFORM_ATTACHMENT_CLAMAV_HOST", "127.0.0.1").strip(),
        port=int(os.getenv("PLATFORM_ATTACHMENT_CLAMAV_PORT", "3310")),
    )


def build_processor(
    *, content_codec: ContentCodec | None = None, client=None
) -> AttachmentProcessor:
    database_file = _required_absolute_path(
        "PLATFORM_ATTACHMENT_WORKER_DATABASE_URL_FILE"
    )
    database_url = read_secret_file(str(database_file))
    codec = content_codec or _build_content_codec()
    client = client or _build_s3_client()
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
        scanner=_scanner(),
        derivatives=DerivativeBuilder(
            sandbox_runner=BubblewrapPdfSandbox(
                bubblewrap_path=bubblewrap,
                pdftoppm_path=renderer,
            )
        ),
        worker_id=worker_id,
    )


def build_maintenance_services(*, content_codec: ContentCodec, object_store):
    database_file = _required_absolute_path(
        "PLATFORM_ATTACHMENT_MAINTENANCE_DATABASE_URL_FILE"
    )
    database_url = read_secret_file(str(database_file))
    return (
        AttachmentRetentionService(
            AttachmentRetentionRepository(database_url, content_codec=content_codec),
            clock=lambda: datetime.now(UTC),
        ),
        AttachmentErasureService(
            AttachmentErasureRepository(database_url, content_codec=content_codec),
            object_store,
        ),
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


async def run_all(
    *,
    sleep: Callable[[float], Any] = asyncio.sleep,
    max_iterations: int | None = None,
) -> None:
    codec = _build_content_codec()
    client = _build_s3_client()
    object_store = S3ProcessingObjectStore(
        client, os.getenv("PLATFORM_ATTACHMENT_S3_BUCKET", "orbbec-agent-attachments")
    )
    processor = build_processor(content_codec=codec, client=client)
    retention, erasure = build_maintenance_services(
        content_codec=codec, object_store=object_store
    )
    worker_id = os.getenv(
        "PLATFORM_ATTACHMENT_WORKER_ID", f"platform-attachments.{secrets.token_hex(4)}"
    ).strip()
    failures = 0
    iterations = 0
    next_retention = 0.0
    try:
        while max_iterations is None or iterations < max_iterations:
            iterations += 1
            changed = False
            try:
                changed = await processor.process_next()
                now = asyncio.get_running_loop().time()
                if now >= next_retention:
                    changed = bool(retention.run_once()) or changed
                    next_retention = now + 60.0
                changed = erasure.process_next(worker_id) or changed
                failures = 0
            except Exception:  # noqa: BLE001 - jobs remain retryable after failures
                failures += 1
                await sleep(min(30.0, float(2 ** min(failures - 1, 5))))
                continue
            await sleep(0.05 if changed else 1.0)
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()


def healthcheck() -> int:
    client = None
    try:
        for name, purpose in (
            ("PLATFORM_ATTACHMENT_WORKER_DATABASE_URL_FILE", "brain"),
            ("PLATFORM_ATTACHMENT_MAINTENANCE_DATABASE_URL_FILE", "maintenance"),
        ):
            database_url = read_secret_file(str(_required_absolute_path(name)))
            validate_control_dsn(database_url, purpose=purpose)
            with psycopg.connect(
                database_url,
                connect_timeout=3,
                options="-c statement_timeout=3000 -c timezone=UTC",
            ) as connection:
                connection.execute("select 1").fetchone()
        client = _build_s3_client()
        client.head_bucket(
            Bucket=os.getenv(
                "PLATFORM_ATTACHMENT_S3_BUCKET", "orbbec-agent-attachments"
            )
        )
        _scanner().database_version()
        return 0
    except Exception:  # noqa: BLE001 - healthcheck is intentionally fail closed
        return 1
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()


def main(argv: list[str] | None = None) -> int:
    selected = list(sys.argv[1:] if argv is None else argv)
    if selected == ["healthcheck"]:
        return healthcheck()
    if selected != ["all"]:
        return 1
    try:
        asyncio.run(run_all())
    except KeyboardInterrupt:
        return 0
    except Exception:  # noqa: BLE001 - CLI exposes only a fail-closed status
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
