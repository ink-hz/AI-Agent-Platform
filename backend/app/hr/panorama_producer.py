from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from .panorama_collection import CollectionError, CollectionResult, SourceTarget
from .panorama_models import CreateSourceCollectionAttempt


class CollectionAdapter(Protocol):
    async def collect(self, target: SourceTarget) -> CollectionResult: ...


class AttemptRepository(Protocol):
    def record_source_attempt(
        self, command: CreateSourceCollectionAttempt
    ) -> object: ...


@dataclass(frozen=True, slots=True)
class ProductionSummary:
    batch_id: UUID
    successful_sources: tuple[UUID, ...]
    failed_sources: dict[UUID, str]
    results: tuple[CollectionResult, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.batch_id, UUID):
            raise TypeError("production summary invalid")
        object.__setattr__(
            self, "failed_sources", MappingProxyType(dict(self.failed_sources))
        )


class PanoramaProducer:
    def __init__(
        self,
        *,
        owner_id: UUID,
        batch_id: UUID,
        collector: CollectionAdapter,
        attempt_repository: AttemptRepository,
        targets: tuple[SourceTarget, ...],
        maximum_concurrency: int = 4,
        maximum_attempts: int = 3,
        backoff: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if (
            not isinstance(owner_id, UUID)
            or not isinstance(batch_id, UUID)
            or not hasattr(collector, "collect")
            or not hasattr(attempt_repository, "record_source_attempt")
            or not isinstance(targets, tuple)
            or not targets
            or any(not isinstance(target, SourceTarget) for target in targets)
            or len({target.source_id for target in targets}) != len(targets)
            or isinstance(maximum_concurrency, bool)
            or not 1 <= maximum_concurrency <= 4
            or isinstance(maximum_attempts, bool)
            or not 1 <= maximum_attempts <= 3
            or not callable(backoff)
        ):
            raise ValueError("producer configuration invalid")
        self._owner_id = owner_id
        self._batch_id = batch_id
        self._collector = collector
        self._repository = attempt_repository
        self._targets = targets
        self._semaphore = asyncio.Semaphore(maximum_concurrency)
        self._maximum_attempts = maximum_attempts
        self._backoff = backoff

    async def run(self) -> ProductionSummary:
        outcomes = await asyncio.gather(
            *(self._guarded_collect(target) for target in self._targets)
        )
        successful: list[UUID] = []
        failed: dict[UUID, str] = {}
        results: list[CollectionResult] = []
        for target, outcome in zip(self._targets, outcomes, strict=True):
            if isinstance(outcome, CollectionResult):
                successful.append(target.source_id)
                results.append(outcome)
            else:
                failed[target.source_id] = outcome
        return ProductionSummary(
            batch_id=self._batch_id,
            successful_sources=tuple(successful),
            failed_sources=failed,
            results=tuple(results),
        )

    async def _guarded_collect(self, target: SourceTarget) -> CollectionResult | str:
        async with self._semaphore:
            for attempt_number in range(1, self._maximum_attempts + 1):
                try:
                    result = await self._collector.collect(target)
                except CollectionError as error:
                    self._record_failure(target, attempt_number, error)
                    if not error.retryable or attempt_number == self._maximum_attempts:
                        return error.code
                    await self._backoff(0.25 * (2 ** (attempt_number - 1)))
                except Exception:  # noqa: BLE001 - one adapter must not cancel siblings
                    self._record_failure(
                        target,
                        attempt_number,
                        CollectionError("source_unavailable"),
                    )
                    return "source_unavailable"
                else:
                    self._record_success(target, attempt_number, result)
                    return result
        return "source_unavailable"

    def _record_success(
        self,
        target: SourceTarget,
        attempt_number: int,
        result: CollectionResult,
    ) -> None:
        self._repository.record_source_attempt(
            CreateSourceCollectionAttempt(
                attempt_id=self._attempt_id(target, attempt_number),
                batch_id=self._batch_id,
                owner_id=self._owner_id,
                source_id=target.source_id,
                source_url=target.source_url,
                attempt_number=attempt_number,
                state="succeeded",
                error_code=None,
                evidence_sha256=result.evidence.sha256,
                evidence_locator=result.evidence.locator,
                evidence_mime=result.evidence.mime,
                evidence_size_bytes=result.evidence.size_bytes,
                normalized_job_count=len(result.jobs),
                observed_at=result.observed_at,
            )
        )

    def _record_failure(
        self, target: SourceTarget, attempt_number: int, error: CollectionError
    ) -> None:
        evidence = error.evidence
        self._repository.record_source_attempt(
            CreateSourceCollectionAttempt(
                attempt_id=self._attempt_id(target, attempt_number),
                batch_id=self._batch_id,
                owner_id=self._owner_id,
                source_id=target.source_id,
                source_url=target.source_url,
                attempt_number=attempt_number,
                state="failed",
                error_code=error.code,
                evidence_sha256=None if evidence is None else evidence.sha256,
                evidence_locator=None if evidence is None else evidence.locator,
                evidence_mime=None if evidence is None else evidence.mime,
                evidence_size_bytes=None if evidence is None else evidence.size_bytes,
                normalized_job_count=0,
                observed_at=error.observed_at or datetime.now(timezone.utc),
            )
        )

    def _attempt_id(self, target: SourceTarget, attempt_number: int) -> UUID:
        return uuid5(
            NAMESPACE_URL,
            f"orbbec:panorama:{self._batch_id}:{target.source_id}:"
            f"{target.source_url}:{attempt_number}",
        )


__all__ = ["PanoramaProducer", "ProductionSummary"]
