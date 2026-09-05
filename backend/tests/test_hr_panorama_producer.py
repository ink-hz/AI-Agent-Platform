from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from app.hr.panorama_collection import CollectionError, CollectionResult, SourceTarget
from app.hr.panorama_evidence import EvidenceRecord
from app.hr.panorama_producer import PanoramaProducer

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
