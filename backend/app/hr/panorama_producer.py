from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from .panorama_analysis import PanoramaAnalyzer
from .panorama_collection import CollectionError, CollectionResult, SourceTarget
from .panorama_models import (
    CreatePublicJobSnapshot,
    CreateSourceCollectionAttempt,
    CreateTalentInsightVersion,
    ProductionBatch,
    PublicJobSnapshot,
    PublishPanoramaReport,
    SourceCollectionAttempt,
    TransitionProductionBatch,
)


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
    failed_channels: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.batch_id, UUID):
            raise TypeError("production summary invalid")
        object.__setattr__(
            self, "failed_sources", MappingProxyType(dict(self.failed_sources))
        )
        object.__setattr__(
            self, "failed_channels", MappingProxyType(dict(self.failed_channels))
        )


@dataclass(frozen=True, slots=True)
class ProductionDelivery:
    batch_id: UUID
    insight_version_id: UUID
    snapshot_count: int
    successful_source_count: int
    failed_source_count: int
    coverage_state: str
    publication: object


class ProductionRepository(AttemptRepository, Protocol):
    def transition_production_batch(
        self, command: TransitionProductionBatch
    ) -> ProductionBatch: ...

    def create_snapshot(
        self, command: CreatePublicJobSnapshot
    ) -> PublicJobSnapshot: ...

    def create_insight(self, command: CreateTalentInsightVersion) -> object: ...

    def snapshots_for_production_batch(
        self, owner_id: UUID, batch_id: UUID
    ) -> tuple[PublicJobSnapshot, ...]: ...

    def source_attempts_for_production_batch(
        self, owner_id: UUID, batch_id: UUID
    ) -> tuple[SourceCollectionAttempt, ...]: ...

    def publish_production_report(self, command: PublishPanoramaReport) -> object: ...


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
            or len({(target.source_id, target.source_url) for target in targets})
            != len(targets)
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
        failed_channels: dict[str, str] = {}
        results: list[CollectionResult] = []
        for target, outcome in zip(self._targets, outcomes, strict=True):
            if isinstance(outcome, CollectionResult):
                if target.source_id not in successful:
                    successful.append(target.source_id)
                results.append(outcome)
            else:
                failed_channels[target.source_url] = outcome
        failed: dict[UUID, str] = {}
        successful_set = set(successful)
        for target in self._targets:
            if target.source_id not in successful_set:
                failed[target.source_id] = failed_channels[target.source_url]
        return ProductionSummary(
            batch_id=self._batch_id,
            successful_sources=tuple(successful),
            failed_sources=failed,
            results=tuple(results),
            failed_channels=failed_channels,
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


class PanoramaProductionPipeline:
    """Persist raw collection first, then version and publish independent AI analysis."""

    def __init__(
        self,
        *,
        batch: ProductionBatch,
        targets: tuple[SourceTarget, ...],
        collector: CollectionAdapter,
        analyzer: PanoramaAnalyzer,
        repository: ProductionRepository,
    ) -> None:
        if (
            not isinstance(batch, ProductionBatch)
            or batch.state not in {"queued", "running", "analyzing"}
            or tuple(dict.fromkeys(target.source_id for target in targets))
            != batch.selected_source_ids
            or not isinstance(analyzer, PanoramaAnalyzer)
        ):
            raise ValueError("production pipeline configuration invalid")
        self._batch = batch
        self._targets = targets
        self._collector = collector
        self._analyzer = analyzer
        self._repository = repository

    async def run(self) -> ProductionDelivery:
        batch = self._batch
        if batch.state == "analyzing":
            snapshots = self._repository.snapshots_for_production_batch(
                batch.owner_id, batch.batch_id
            )
            if not snapshots:
                raise CollectionError("no_public_jobs")
            attempts = self._repository.source_attempts_for_production_batch(
                batch.owner_id, batch.batch_id
            )
            coverage = self._coverage_from_persisted(batch, snapshots, attempts)
            successful_source_count = len({item.source_id for item in snapshots})
            failed_source_count = len(batch.source_failures)
            return await self._analyze_and_publish(
                batch,
                snapshots,
                coverage,
                successful_source_count=successful_source_count,
                failed_source_count=failed_source_count,
            )
        if batch.state == "queued":
            batch = self._repository.transition_production_batch(
                TransitionProductionBatch(
                    owner_id=batch.owner_id,
                    batch_id=batch.batch_id,
                    expected_row_version=batch.row_version,
                    state="running",
                    error_code=None,
                    source_failures={},
                )
            )
        collection = await PanoramaProducer(
            owner_id=batch.owner_id,
            batch_id=batch.batch_id,
            collector=self._collector,
            attempt_repository=self._repository,
            targets=self._targets,
        ).run()
        failures = {
            str(source_id): reason
            for source_id, reason in collection.failed_sources.items()
        }
        if not collection.results:
            self._repository.transition_production_batch(
                TransitionProductionBatch(
                    owner_id=batch.owner_id,
                    batch_id=batch.batch_id,
                    expected_row_version=batch.row_version,
                    state="failed",
                    error_code="no_usable_sources",
                    source_failures=failures,
                )
            )
            raise CollectionError("no_usable_sources")
        try:
            snapshots = self._persist_snapshots(batch, collection.results)
        except CollectionError as error:
            self._repository.transition_production_batch(
                TransitionProductionBatch(
                    owner_id=batch.owner_id,
                    batch_id=batch.batch_id,
                    expected_row_version=batch.row_version,
                    state="failed",
                    error_code=error.code,
                    source_failures=failures,
                )
            )
            raise
        batch = self._repository.transition_production_batch(
            TransitionProductionBatch(
                owner_id=batch.owner_id,
                batch_id=batch.batch_id,
                expected_row_version=batch.row_version,
                state="analyzing",
                error_code=None,
                source_failures=failures,
            )
        )
        return await self._analyze_and_publish(
            batch,
            snapshots,
            self._coverage(collection),
            successful_source_count=len(collection.successful_sources),
            failed_source_count=len(collection.failed_sources),
        )

    async def _analyze_and_publish(
        self,
        batch: ProductionBatch,
        snapshots: tuple[PublicJobSnapshot, ...],
        coverage: tuple[dict[str, object], ...],
        *,
        successful_source_count: int,
        failed_source_count: int,
    ) -> ProductionDelivery:
        try:
            analysis = await self._analyze(snapshots)
            insight_id = uuid5(
                NAMESPACE_URL,
                f"orbbec:panorama:{batch.batch_id}:analysis:"
                f"{self._analyzer.model_version}",
            )
            insight = self._repository.create_insight(
                CreateTalentInsightVersion(
                    insight_version_id=insight_id,
                    owner_id=batch.owner_id,
                    client_request_id=uuid5(
                        NAMESPACE_URL,
                        f"orbbec:panorama:{batch.batch_id}:analysis-request:"
                        f"{self._analyzer.model_version}",
                    ),
                    run_id=None,
                    selected_source_ids=analysis.source_ids,
                    snapshot_ids=analysis.snapshot_ids,
                    facts=analysis.facts,
                    inferences=analysis.inferences,
                    unknowns=analysis.unknowns,
                    direction_clusters=analysis.direction_clusters,
                    summary=analysis.summary,
                    source_conversation_id=None,
                    source_turn_id=None,
                    agent_id="hr-intelligence-producer",
                    model_version=analysis.model_version,
                    production_batch_id=batch.batch_id,
                )
            )
        except Exception:
            self._repository.transition_production_batch(
                TransitionProductionBatch(
                    owner_id=batch.owner_id,
                    batch_id=batch.batch_id,
                    expected_row_version=batch.row_version,
                    state="failed",
                    error_code="analysis_failed",
                    source_failures=batch.source_failures,
                )
            )
            raise
        coverage_state = (
            "partial"
            if any(
                item["state"] == "failed" or item.get("channel_failures")
                for item in coverage
            )
            else "complete"
        )
        publication = self._repository.publish_production_report(
            PublishPanoramaReport(
                publication_id=uuid5(
                    NAMESPACE_URL,
                    f"orbbec:panorama:{batch.batch_id}:publication:"
                    f"{self._analyzer.model_version}",
                ),
                client_request_id=uuid5(
                    NAMESPACE_URL,
                    f"orbbec:panorama:{batch.batch_id}:publication-request:"
                    f"{self._analyzer.model_version}",
                ),
                batch_id=batch.batch_id,
                insight_version_id=insight.insight_version_id,
                coverage_state=coverage_state,
                source_coverage=coverage,
            )
        )
        return ProductionDelivery(
            batch_id=batch.batch_id,
            insight_version_id=insight.insight_version_id,
            snapshot_count=len(snapshots),
            successful_source_count=successful_source_count,
            failed_source_count=failed_source_count,
            coverage_state=coverage_state,
            publication=publication,
        )

    def _persist_snapshots(
        self,
        batch: ProductionBatch,
        results: tuple[CollectionResult, ...],
    ) -> tuple[PublicJobSnapshot, ...]:
        snapshots: list[PublicJobSnapshot] = []
        for result in results:
            for job in result.jobs:
                identity = (
                    f"{batch.batch_id}:{result.target.source_id}:"
                    f"{job.public_job_key}:{result.evidence.sha256}"
                )
                snapshots.append(
                    self._repository.create_snapshot(
                        CreatePublicJobSnapshot(
                            snapshot_id=uuid5(
                                NAMESPACE_URL, f"orbbec:panorama:snapshot:{identity}"
                            ),
                            owner_id=batch.owner_id,
                            client_request_id=uuid5(
                                NAMESPACE_URL,
                                f"orbbec:panorama:observation:{identity}",
                            ),
                            run_id=None,
                            source_id=result.target.source_id,
                            public_job_key=job.public_job_key,
                            title=job.title,
                            location=job.location,
                            duty_excerpt=job.duty_excerpt,
                            requirement_excerpt=job.requirement_excerpt,
                            source_url=job.source_url,
                            observed_at=result.observed_at,
                            content_sha256=result.evidence.sha256,
                            status=job.status,  # type: ignore[arg-type]
                            production_batch_id=batch.batch_id,
                        )
                    )
                )
        if not snapshots:
            raise CollectionError("no_public_jobs")
        return tuple(snapshots)

    async def _analyze(self, snapshots: tuple[PublicJobSnapshot, ...]):
        targets = {target.source_id: target for target in self._targets}
        company_analyses = []
        for source_id in dict.fromkeys(item.source_id for item in snapshots):
            selected = tuple(item for item in snapshots if item.source_id == source_id)
            company_analyses.append(
                await self._analyzer.analyze_company(
                    targets[source_id].company_name, selected
                )
            )
        topic_analyses = []
        campus = tuple(
            item
            for item in snapshots
            if any(
                marker in f"{item.title}{item.duty_excerpt}{item.requirement_excerpt}"
                for marker in ("校招", "校园", "应届", "实习", "届毕业")
            )
        )
        social = tuple(item for item in snapshots if item not in campus)
        for topic, selected in (("社会招聘", social), ("校园招聘", campus)):
            if selected:
                relevant = tuple(
                    analysis
                    for analysis in company_analyses
                    if set(analysis.snapshot_ids)
                    & {item.snapshot_id for item in selected}
                )
                topic_analyses.append(
                    await self._analyzer.analyze_topic(topic, selected, relevant)
                )
        return await self._analyzer.compile_report(
            snapshots,
            company_analyses=tuple(company_analyses),
            topic_analyses=tuple(topic_analyses),
        )

    def _coverage(self, summary: ProductionSummary) -> tuple[dict[str, object], ...]:
        records = []
        for source_id in dict.fromkeys(target.source_id for target in self._targets):
            targets = tuple(
                target for target in self._targets if target.source_id == source_id
            )
            results = tuple(
                result
                for result in summary.results
                if result.target.source_id == source_id
            )
            channel_failures = {
                target.source_url: summary.failed_channels[target.source_url]
                for target in targets
                if target.source_url in summary.failed_channels
            }
            if not results:
                records.append(
                    {
                        "source_id": str(source_id),
                        "state": "failed",
                        "observed_at": datetime.now(timezone.utc).isoformat(),
                        "source_urls": [target.source_url for target in targets],
                        "job_count": 0,
                        "error_code": summary.failed_sources[source_id],
                        "channel_failures": channel_failures,
                    }
                )
            else:
                record: dict[str, object] = {
                    "source_id": str(source_id),
                    "state": "succeeded",
                    "observed_at": max(
                        result.observed_at for result in results
                    ).isoformat(),
                    "source_urls": [target.source_url for target in targets],
                    "job_count": sum(len(result.jobs) for result in results),
                }
                if channel_failures:
                    record["channel_failures"] = channel_failures
                records.append(record)
        return tuple(records)

    def _coverage_from_persisted(
        self,
        batch: ProductionBatch,
        snapshots: tuple[PublicJobSnapshot, ...],
        attempts: tuple[SourceCollectionAttempt, ...],
    ) -> tuple[dict[str, object], ...]:
        latest: dict[tuple[UUID, str], SourceCollectionAttempt] = {}
        for attempt in attempts:
            if attempt.batch_id != batch.batch_id or attempt.owner_id != batch.owner_id:
                raise ValueError("production attempt scope invalid")
            key = (attempt.source_id, attempt.source_url)
            previous = latest.get(key)
            if previous is None or attempt.attempt_number > previous.attempt_number:
                latest[key] = attempt
        records: list[dict[str, object]] = []
        for source_id in batch.selected_source_ids:
            targets = tuple(
                target for target in self._targets if target.source_id == source_id
            )
            selected_snapshots = tuple(
                snapshot for snapshot in snapshots if snapshot.source_id == source_id
            )
            channel_failures = {
                target.source_url: attempt.error_code or "source_unavailable"
                for target in targets
                if (attempt := latest.get((source_id, target.source_url))) is not None
                and attempt.state == "failed"
            }
            successful_attempts = tuple(
                attempt
                for target in targets
                if (attempt := latest.get((source_id, target.source_url))) is not None
                and attempt.state == "succeeded"
            )
            observed_values = [item.observed_at for item in selected_snapshots]
            observed_values.extend(item.observed_at for item in successful_attempts)
            record: dict[str, object] = {
                "source_id": str(source_id),
                "state": "succeeded" if selected_snapshots else "failed",
                "observed_at": max(observed_values, default=batch.updated_at).isoformat(),
                "source_urls": [target.source_url for target in targets],
                "job_count": len(selected_snapshots),
            }
            if selected_snapshots:
                if channel_failures:
                    record["channel_failures"] = channel_failures
            else:
                record["error_code"] = batch.source_failures.get(
                    str(source_id), "source_unavailable"
                )
                record["channel_failures"] = channel_failures
            records.append(record)
        return tuple(records)


__all__ = [
    "PanoramaProducer",
    "PanoramaProductionPipeline",
    "ProductionDelivery",
    "ProductionSummary",
]
