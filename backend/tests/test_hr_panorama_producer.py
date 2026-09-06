from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from app.hr.panorama_analysis import PanoramaAnalyzer
from app.hr.panorama_collection import (
    CollectionError,
    CollectionResult,
    NormalizedPublicJob,
    SourceTarget,
)
from app.hr.panorama_evidence import EvidenceRecord
from app.hr.panorama_models import (
    ProductionBatch,
    PublicJobSnapshot,
    SourceCollectionAttempt,
)
from app.hr.panorama_producer import PanoramaProducer, PanoramaProductionPipeline

NOW = datetime(2026, 9, 6, 8, tzinfo=timezone.utc)


def target(name: str) -> SourceTarget:
    return SourceTarget(
        source_id=uuid4(),
        company_name=name,
        source_url=f"https://{name.lower()}.example/jobs",
        approved_urls=(f"https://{name.lower()}.example/jobs",),
    )


def result(selected: SourceTarget) -> CollectionResult:
    return CollectionResult(
        target=selected,
        jobs=(),
        evidence=EvidenceRecord(
            sha256="a" * 64,
            locator="sha256/aa/" + "a" * 64,
            mime="text/html",
            size_bytes=10,
            metadata_json="{}",
        ),
        observed_at=NOW,
    )


class Collector:
    def __init__(self, failures: dict[UUID, list[str]]) -> None:
        self.failures = failures
        self.calls: list[UUID] = []

    async def collect(self, selected: SourceTarget) -> CollectionResult:
        self.calls.append(selected.source_id)
        failures = self.failures.get(selected.source_id, [])
        index = self.calls.count(selected.source_id) - 1
        if index < len(failures):
            raise CollectionError(
                failures[index], retryable=failures[index] == "source_timeout"
            )
        return result(selected)


@dataclass
class Recorder:
    attempts: list[object]

    def record_source_attempt(self, command):
        self.attempts.append(command)
        return command


@pytest.mark.asyncio
async def test_one_source_failure_does_not_cancel_successful_sources() -> None:
    a, b = target("A"), target("B")
    collector = Collector({b.source_id: ["source_timeout"] * 3})
    recorder = Recorder([])
    delays: list[float] = []

    async def backoff(delay: float) -> None:
        delays.append(delay)

    producer = PanoramaProducer(
        owner_id=uuid4(),
        batch_id=uuid4(),
        collector=collector,
        attempt_repository=recorder,
        targets=(a, b),
        backoff=backoff,
    )
    summary = await producer.run()

    assert summary.successful_sources == (a.source_id,)
    assert summary.failed_sources == {b.source_id: "source_timeout"}
    assert collector.calls.count(b.source_id) == 3
    assert len(recorder.attempts) == 4
    assert delays == [0.25, 0.5]


@pytest.mark.asyncio
async def test_schema_failures_are_not_retried() -> None:
    selected = target("A")
    collector = Collector({selected.source_id: ["unsupported_schema"]})
    recorder = Recorder([])
    producer = PanoramaProducer(
        owner_id=uuid4(),
        batch_id=uuid4(),
        collector=collector,
        attempt_repository=recorder,
        targets=(selected,),
    )

    summary = await producer.run()

    assert summary.failed_sources == {selected.source_id: "unsupported_schema"}
    assert collector.calls == [selected.source_id]
    assert len(recorder.attempts) == 1


@pytest.mark.asyncio
async def test_company_channels_are_isolated_and_one_success_keeps_the_company() -> (
    None
):
    source_id = uuid4()
    social = SourceTarget(
        source_id=source_id,
        company_name="A",
        source_url="https://a.example/jobs/social",
        approved_urls=("https://a.example/jobs",),
    )
    campus = SourceTarget(
        source_id=source_id,
        company_name="A",
        source_url="https://a.example/jobs/campus",
        approved_urls=("https://a.example/jobs",),
    )

    class ChannelCollector:
        async def collect(self, selected):
            if selected.source_url.endswith("campus"):
                raise CollectionError("source_rejected")
            return result(selected)

    summary = await PanoramaProducer(
        owner_id=uuid4(),
        batch_id=uuid4(),
        collector=ChannelCollector(),
        attempt_repository=Recorder([]),
        targets=(social, campus),
    ).run()

    assert summary.successful_sources == (source_id,)
    assert summary.failed_sources == {}
    assert summary.failed_channels == {campus.source_url: "source_rejected"}


class AnalysisModel:
    version = "gpt-research-v1"

    async def generate_json(self, stage, payload):
        job = payload["jobs"][0]
        return {
            "facts": [
                {
                    "fact_id": f"{stage}-fact-1",
                    "text": f"公开招聘{job['title']}",
                    "snapshot_id": job["snapshot_id"],
                    "observation_id": job["observation_id"],
                    "source_url": job["source_url"],
                    "observed_at": job["observed_at"],
                }
            ],
            "inferences": [
                {
                    "text": "结构研发投入明确",
                    "basis_fact_ids": [f"{stage}-fact-1"],
                }
            ],
            "unknowns": [{"text": "实际 HC 未公开"}],
            "direction_clusters": {"结构": 1},
            "summary": "结构研发投入明确。",
        }


class PipelineCollector:
    async def collect(self, selected):
        return CollectionResult(
            target=selected,
            jobs=(
                NormalizedPublicJob(
                    public_job_key="structure-1",
                    title="高级结构工程师",
                    location="深圳",
                    duty_excerpt="负责精密结构研发",
                    requirement_excerpt="五年以上量产经验",
                    source_url=f"{selected.source_url}/structure-1",
                ),
            ),
            evidence=EvidenceRecord(
                sha256="c" * 64,
                locator="sha256/cc/" + "c" * 64,
                mime="text/html",
                size_bytes=1024,
                metadata_json="{}",
            ),
            observed_at=NOW,
        )


class PipelineRepository:
    def __init__(self, batch):
        self.batch = batch
        self.events = []
        self.snapshots = []
        self.insight = None
        self.publication = None

    def transition_production_batch(self, command):
        self.events.append(command.state)
        self.batch = ProductionBatch(
            batch_id=self.batch.batch_id,
            owner_id=self.batch.owner_id,
            client_request_id=self.batch.client_request_id,
            selected_source_ids=self.batch.selected_source_ids,
            trigger_kind=self.batch.trigger_kind,
            state=command.state,
            analyzer_version=self.batch.analyzer_version,
            source_failures=command.source_failures,
            error_code=command.error_code,
            row_version=self.batch.row_version + 1,
            started_at=NOW,
            finished_at=NOW if command.state == "failed" else None,
            created_at=NOW,
            updated_at=NOW,
        )
        return self.batch

    def record_source_attempt(self, command):
        self.events.append("attempt")
        return command

    def create_snapshot(self, command):
        self.events.append("raw-job")
        value = PublicJobSnapshot(
            snapshot_id=command.snapshot_id,
            owner_id=command.owner_id,
            origin_request_id=command.client_request_id,
            run_id=None,
            source_id=command.source_id,
            public_job_key=command.public_job_key,
            title=command.title,
            location=command.location,
            duty_excerpt=command.duty_excerpt,
            requirement_excerpt=command.requirement_excerpt,
            source_url=command.source_url,
            observed_at=command.observed_at,
            content_sha256=command.content_sha256,
            status=command.status,
            created_at=NOW,
            production_batch_id=command.production_batch_id,
            observation_id=command.client_request_id,
        )
        self.snapshots.append(value)
        return value

    def snapshots_for_production_batch(self, owner_id, batch_id):
        assert owner_id == self.batch.owner_id
        assert batch_id == self.batch.batch_id
        return tuple(self.snapshots)

    def source_attempts_for_production_batch(self, owner_id, batch_id):
        assert owner_id == self.batch.owner_id
        assert batch_id == self.batch.batch_id
        return ()

    def create_insight(self, command):
        self.events.append("ai-analysis")
        self.insight = command.as_version(version_number=1, created_at=NOW)
        return self.insight

    def publish_production_report(self, command):
        self.events.append("published")
        self.publication = command
        return command


def test_persisted_coverage_keeps_a_verified_zero_job_channel_successful() -> None:
    with_jobs, verified_empty = target("A"), target("B")
    batch = ProductionBatch(
        uuid4(),
        uuid4(),
        uuid4(),
        (with_jobs.source_id, verified_empty.source_id),
        "operator",
        "analyzing",
        "gpt-research-v1",
        {},
        None,
        2,
        NOW,
        None,
        NOW,
        NOW,
    )
    snapshot = PublicJobSnapshot(
        uuid4(), batch.owner_id, uuid4(), None, with_jobs.source_id, "job-1",
        "算法工程师", "深圳", "负责算法", "熟悉 Python", with_jobs.source_url,
        NOW, "a" * 64, "open", NOW, batch.batch_id, uuid4(),
    )
    empty_attempt = SourceCollectionAttempt(
        uuid4(), batch.batch_id, batch.owner_id, verified_empty.source_id,
        verified_empty.source_url, 1, "succeeded", None, "b" * 64,
        "sha256/bb/" + "b" * 64, "application/json", 24, 0, NOW, NOW,
    )
    pipeline = PanoramaProductionPipeline(
        batch=batch,
        targets=(with_jobs, verified_empty),
        collector=PipelineCollector(),
        analyzer=PanoramaAnalyzer(AnalysisModel()),
        repository=PipelineRepository(batch),
    )

    coverage = pipeline._coverage_from_persisted(
        batch, (snapshot,), (empty_attempt,)
    )

    assert coverage[1]["state"] == "succeeded"
    assert coverage[1]["job_count"] == 0
    assert "error_code" not in coverage[1]


@pytest.mark.asyncio
async def test_pipeline_persists_raw_jobs_before_separate_ai_analysis_and_publication() -> (
    None
):
    selected = target("A")
    batch = ProductionBatch(
        batch_id=uuid4(),
        owner_id=uuid4(),
        client_request_id=uuid4(),
        selected_source_ids=(selected.source_id,),
        trigger_kind="schedule",
        state="queued",
        analyzer_version="gpt-research-v1",
        source_failures={},
        error_code=None,
        row_version=1,
        started_at=None,
        finished_at=None,
        created_at=NOW,
        updated_at=NOW,
    )
    repository = PipelineRepository(batch)
    pipeline = PanoramaProductionPipeline(
        batch=batch,
        targets=(selected,),
        collector=PipelineCollector(),
        analyzer=PanoramaAnalyzer(AnalysisModel()),
        repository=repository,
    )

    delivery = await pipeline.run()

    assert delivery.snapshot_count == 1
    assert delivery.coverage_state == "complete"
    assert repository.events.index("raw-job") < repository.events.index("ai-analysis")
    assert repository.events[-1] == "published"
    assert repository.insight.production_batch_id == batch.batch_id
    assert not hasattr(repository.insight, "conversation_id")


@pytest.mark.asyncio
async def test_pipeline_marks_batch_failed_when_sources_have_no_public_jobs() -> None:
    selected = target("A")
    batch = ProductionBatch(
        batch_id=uuid4(),
        owner_id=uuid4(),
        client_request_id=uuid4(),
        selected_source_ids=(selected.source_id,),
        trigger_kind="schedule",
        state="queued",
        analyzer_version="gpt-research-v1",
        source_failures={},
        error_code=None,
        row_version=1,
        started_at=None,
        finished_at=None,
        created_at=NOW,
        updated_at=NOW,
    )
    repository = PipelineRepository(batch)

    class EmptyCollector:
        async def collect(self, target):
            return result(target)

    pipeline = PanoramaProductionPipeline(
        batch=batch,
        targets=(selected,),
        collector=EmptyCollector(),
        analyzer=PanoramaAnalyzer(AnalysisModel()),
        repository=repository,
    )

    with pytest.raises(CollectionError, match="no_public_jobs"):
        await pipeline.run()

    assert repository.batch.state == "failed"
    assert repository.batch.error_code == "no_public_jobs"
    assert repository.events[-1] == "failed"


@pytest.mark.asyncio
async def test_pipeline_resumes_analysis_from_persisted_raw_jobs_without_recollecting() -> (
    None
):
    selected = target("A")
    batch = ProductionBatch(
        batch_id=uuid4(),
        owner_id=uuid4(),
        client_request_id=uuid4(),
        selected_source_ids=(selected.source_id,),
        trigger_kind="operator",
        state="analyzing",
        analyzer_version="gpt-research-v1",
        source_failures={},
        error_code=None,
        row_version=3,
        started_at=NOW,
        finished_at=None,
        created_at=NOW,
        updated_at=NOW,
    )
    repository = PipelineRepository(batch)
    repository.snapshots.append(
        PublicJobSnapshot(
            snapshot_id=uuid4(),
            owner_id=batch.owner_id,
            origin_request_id=uuid4(),
            run_id=None,
            source_id=selected.source_id,
            public_job_key="structure-1",
            title="高级结构工程师",
            location="深圳",
            duty_excerpt="负责精密结构研发",
            requirement_excerpt="五年以上量产经验",
            source_url=f"{selected.source_url}/structure-1",
            observed_at=NOW,
            content_sha256="c" * 64,
            status="open",
            created_at=NOW,
            production_batch_id=batch.batch_id,
            observation_id=uuid4(),
        )
    )

    class MustNotCollect:
        async def collect(self, target):  # pragma: no cover - assertion is the body
            raise AssertionError("analyzing resume must not recollect")

    delivery = await PanoramaProductionPipeline(
        batch=batch,
        targets=(selected,),
        collector=MustNotCollect(),
        analyzer=PanoramaAnalyzer(AnalysisModel()),
        repository=repository,
    ).run()

    assert delivery.snapshot_count == 1
    assert repository.events == ["ai-analysis", "published"]
