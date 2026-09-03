from __future__ import annotations

import hashlib
import tempfile
from contextlib import suppress
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol
from uuid import UUID

from .derivatives import Derivative, DerivativeBuilder, DerivativeError
from .scanner import MalwareScanner, ScanDisposition, ScannerUnavailable
from .validation import (
    AttachmentValidationError,
    AttachmentValidator,
    OpenedObject,
    ValidationResult,
)


@dataclass(frozen=True)
class ProcessingJob:
    processing_job_id: UUID
    attachment_id: UUID
    job_kind: str
    derivative_kind: str | None
    object_ref: str = field(repr=False)
    size_bytes: int
    sha256: bytes = field(repr=False)
    detected_mime: str | None
    immutable_locator: str | None = field(default=None, repr=False)
    attempt_token: UUID | None = field(default=None, repr=False)


@dataclass(frozen=True)
class StoredDerivative:
    object_ref: str = field(repr=False)
    size_bytes: int
    sha256: bytes = field(repr=False)


class DerivativeFinalizeError(RuntimeError):
    def __init__(self, *, ambiguous: bool) -> None:
        self.ambiguous = ambiguous
        super().__init__("attachment derivative finalization unavailable")


class ProcessingTransitionError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("attachment processing transition unavailable")


class ReconciliationStatus(str, Enum):
    COMMITTED = "committed"
    RUNNING = "running"
    UNKNOWN = "unknown"


class ProcessingRepository(Protocol):
    def claim(self, worker_id: str) -> ProcessingJob | None: ...

    def record_result(
        self,
        job: ProcessingJob,
        state: str,
        reason: str | None,
        *,
        validation: ValidationResult | None = None,
    ) -> None: ...

    def record_derivative(
        self,
        job: ProcessingJob,
        derivative: Derivative,
        stored: StoredDerivative,
    ) -> None: ...

    def reconcile_result(
        self, job: ProcessingJob, state: str
    ) -> ReconciliationStatus: ...

    def reconcile_derivative(
        self, job: ProcessingJob, stored: StoredDerivative
    ) -> ReconciliationStatus: ...


class ProcessingObjectStore(Protocol):
    def open(
        self, object_ref: str, immutable_locator: str | None = None
    ) -> OpenedObject: ...

    def put_derivative(self, data: bytes, *, object_key: str) -> StoredDerivative: ...

    def delete(self, object_ref: str) -> None: ...


class AttachmentProcessor:
    def __init__(
        self,
        *,
        repository: ProcessingRepository,
        object_store: ProcessingObjectStore,
        validator: AttachmentValidator | None,
        scanner: MalwareScanner,
        derivatives: DerivativeBuilder,
        worker_id: str,
    ) -> None:
        if not isinstance(worker_id, str) or not 1 <= len(worker_id) <= 128:
            raise ValueError("attachment worker identity invalid")
        self._repository = repository
        self._object_store = object_store
        self._validator = validator
        self._scanner = scanner
        self._derivatives = derivatives
        self._worker_id = worker_id

    def __repr__(self) -> str:
        return "AttachmentProcessor(worker_id=<redacted>)"

    async def process_next(self) -> bool:
        job = self._repository.claim(self._worker_id)
        if job is None:
            return False
        if job.job_kind == "validate":
            self._validate(job)
        elif job.job_kind == "scan":
            self._scan(job)
        elif job.job_kind == "derive":
            self._derive(job)
        else:
            self._finish(job, "retry", "invalid_job")
        return True

    def _finish(
        self,
        job: ProcessingJob,
        state: str,
        reason: str | None,
        *,
        validation: ValidationResult | None = None,
    ) -> None:
        try:
            self._repository.record_result(
                job, state, reason, validation=validation
            )
        except Exception:  # noqa: BLE001 - transition adapter failures are sanitized
            reconcile = getattr(self._repository, "reconcile_result", None)
            try:
                status = (
                    reconcile(job, state)
                    if callable(reconcile)
                    else ReconciliationStatus.UNKNOWN
                )
            except Exception:  # noqa: BLE001 - reconciliation fails closed
                status = ReconciliationStatus.UNKNOWN
            if status is ReconciliationStatus.COMMITTED:
                return
            if status is ReconciliationStatus.RUNNING:
                retry_state = state == "retry"
                try:
                    self._repository.record_result(
                        job,
                        "retry",
                        reason if retry_state else "transition_unavailable",
                    )
                    return
                except Exception:  # noqa: BLE001 - one confirmed retry only
                    try:
                        if (
                            callable(reconcile)
                            and reconcile(job, "retry")
                            is ReconciliationStatus.COMMITTED
                        ):
                            return
                    except Exception:  # noqa: BLE001 - uncertainty fails closed
                        status = ReconciliationStatus.UNKNOWN
            raise ProcessingTransitionError() from None

    def _open(self, job: ProcessingJob) -> OpenedObject:
        source = (
            self._object_store.open(job.object_ref, job.immutable_locator)
            if job.immutable_locator is not None
            else self._object_store.open(job.object_ref)
        )
        if not isinstance(source, OpenedObject):
            raise TypeError("attachment storage unavailable")
        return source

    def _validate(self, job: ProcessingJob) -> None:
        if self._validator is None:
            self._finish(job, "retry", "validator_unavailable")
            return
        source = None
        state, reason, validation = "retry", "validation_unavailable", None
        try:
            source = self._open(job)
            validation = self._validator.validate(
                source,
                expected_size=job.size_bytes,
                expected_sha256=job.sha256,
            )
            state, reason = "scanning", None
        except AttachmentValidationError as error:
            state, reason = "rejected", error.reason
        except Exception:  # noqa: BLE001 - object errors are sanitized
            state, reason, validation = "retry", "validation_unavailable", None
        finally:
            if source is not None:
                with suppress(Exception):
                    source.close()
        self._finish(job, state, reason, validation=validation)

    def _scan(self, job: ProcessingJob) -> None:
        source = None
        state, reason = "retry", "scan_unavailable"
        try:
            source = self._open(job)
            digest = hashlib.sha256()
            streamed = 0

            class VerifiedChunks:
                def iter_chunks_until(_self, deadline, monotonic):
                    nonlocal streamed
                    for chunk in source.iter_chunks_until(deadline, monotonic):
                        streamed += len(chunk)
                        digest.update(chunk)
                        yield chunk

            result = self._scanner.scan_stream(
                VerifiedChunks(), size=job.size_bytes
            )
            if (
                streamed != source.size
                or streamed != job.size_bytes
                or digest.digest() != job.sha256
            ):
                state, reason = "rejected", "integrity_mismatch"
            elif result.disposition is ScanDisposition.INFECTED:
                state, reason = "quarantined", "malware_detected"
            elif result.disposition is ScanDisposition.CLEAN:
                if reason != "integrity_mismatch":
                    state, reason = "ready", None
            elif reason != "integrity_mismatch":
                state, reason = "retry", "scanner_unavailable"
        except ScannerUnavailable:
            state, reason = "retry", "scanner_unavailable"
        except Exception:  # noqa: BLE001 - object errors are sanitized
            state, reason = "retry", "scan_unavailable"
        finally:
            if source is not None:
                with suppress(Exception):
                    source.close()
        self._finish(job, state, reason)

    def _derive(self, job: ProcessingJob) -> None:
        if job.detected_mime is None:
            self._finish(job, "retry", "metadata_unavailable")
            return
        source = None
        stored = None
        outcome: tuple[str, str] | None = None
        try:
            source = self._open(job)
            digest = hashlib.sha256()
            size = 0
            with tempfile.SpooledTemporaryFile(max_size=1024 * 1024) as staged:
                for chunk in source.iter_chunks():
                    size += len(chunk)
                    if size > job.size_bytes:
                        raise AttachmentValidationError("integrity_mismatch")
                    digest.update(chunk)
                    staged.write(chunk)
                if (
                    size != source.size
                    or size != job.size_bytes
                    or digest.digest() != job.sha256
                ):
                    raise AttachmentValidationError("integrity_mismatch")
                staged.seek(0)
                derivatives = self._derivatives.build(
                    OpenedObject(staged, size), job.detected_mime
                )
            if len(derivatives) != 1:
                raise DerivativeError()
            derivative = derivatives[0]
            if job.derivative_kind != derivative.kind:
                raise DerivativeError()
            object_key = hashlib.sha256(
                b"attachment-derivative-v1\0"
                + job.processing_job_id.bytes
                + derivative.kind.encode("ascii")
            ).hexdigest()
            stored = self._object_store.put_derivative(
                derivative.data, object_key=object_key
            )
        except AttachmentValidationError:
            outcome = ("rejected", "integrity_mismatch")
        except DerivativeError:
            outcome = ("retry", "derivative_unavailable")
        except Exception:  # noqa: BLE001 - object and DB errors are sanitized
            outcome = ("retry", "derivative_unavailable")
        finally:
            if source is not None:
                with suppress(Exception):
                    source.close()
        if outcome is not None:
            self._finish(job, *outcome)
            return
        try:
            self._repository.record_derivative(job, derivative, stored)
        except Exception as error:  # noqa: BLE001 - finalize failures are reconciled
            reconcile = getattr(self._repository, "reconcile_derivative", None)
            try:
                status = (
                    reconcile(job, stored)
                    if callable(reconcile)
                    else ReconciliationStatus.UNKNOWN
                )
            except Exception:  # noqa: BLE001 - reconciliation fails closed
                status = ReconciliationStatus.UNKNOWN
            if status is ReconciliationStatus.COMMITTED:
                return
            if status is ReconciliationStatus.UNKNOWN:
                raise ProcessingTransitionError() from None
            if isinstance(error, DerivativeFinalizeError) and not error.ambiguous:
                with suppress(Exception):
                    self._object_store.delete(stored.object_ref)
            self._finish(job, "retry", "derivative_unavailable")
