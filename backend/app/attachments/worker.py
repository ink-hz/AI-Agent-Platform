from __future__ import annotations

from dataclasses import dataclass, field
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


@dataclass(frozen=True)
class StoredDerivative:
    object_ref: str = field(repr=False)
    size_bytes: int
    sha256: bytes = field(repr=False)


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


class ProcessingObjectStore(Protocol):
    def open(self, object_ref: str) -> OpenedObject: ...

    def put_derivative(self, data: bytes) -> StoredDerivative: ...

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
            self._repository.record_result(job, "retry", "invalid_job")
        return True

    def _open(self, job: ProcessingJob) -> OpenedObject:
        source = self._object_store.open(job.object_ref)
        if not isinstance(source, OpenedObject):
            raise TypeError("attachment storage unavailable")
        return source

    def _validate(self, job: ProcessingJob) -> None:
        if self._validator is None:
            self._repository.record_result(job, "retry", "validator_unavailable")
            return
        source = None
        try:
            source = self._open(job)
            result = self._validator.validate(
                source,
                expected_size=job.size_bytes,
                expected_sha256=job.sha256,
            )
            self._repository.record_result(job, "scanning", None, validation=result)
        except AttachmentValidationError as error:
            self._repository.record_result(job, "rejected", error.reason)
        except Exception:  # noqa: BLE001 - object and DB errors are sanitized
            self._repository.record_result(job, "retry", "validation_unavailable")
        finally:
            if source is not None:
                source.close()

    def _scan(self, job: ProcessingJob) -> None:
        source = None
        try:
            source = self._open(job)
            result = self._scanner.scan_stream(
                source.iter_chunks(), size=job.size_bytes
            )
            if result.disposition is ScanDisposition.INFECTED:
                self._repository.record_result(job, "quarantined", "malware_detected")
            elif result.disposition is ScanDisposition.CLEAN:
                self._repository.record_result(job, "ready", None)
            else:
                self._repository.record_result(job, "retry", "scanner_unavailable")
        except ScannerUnavailable:
            self._repository.record_result(job, "retry", "scanner_unavailable")
        except Exception:  # noqa: BLE001 - object and DB errors are sanitized
            self._repository.record_result(job, "retry", "scan_unavailable")
        finally:
            if source is not None:
                source.close()

    def _derive(self, job: ProcessingJob) -> None:
        if job.detected_mime is None:
            self._repository.record_result(job, "retry", "metadata_unavailable")
            return
        source = None
        try:
            source = self._open(job)
            derivatives = self._derivatives.build(source, job.detected_mime)
            if len(derivatives) != 1:
                raise DerivativeError()
            derivative = derivatives[0]
            stored = self._object_store.put_derivative(derivative.data)
            self._repository.record_derivative(job, derivative, stored)
        except DerivativeError:
            self._repository.record_result(job, "retry", "derivative_unavailable")
        except Exception:  # noqa: BLE001 - object and DB errors are sanitized
            self._repository.record_result(job, "retry", "derivative_unavailable")
        finally:
            if source is not None:
                source.close()
