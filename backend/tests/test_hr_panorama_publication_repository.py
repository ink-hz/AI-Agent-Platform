from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.hr.panorama_models import (
    CreateProductionBatch,
    CreateSourceCollectionAttempt,
    PublishPanoramaReport,
    TransitionProductionBatch,
)
from app.hr.panorama_repository import PanoramaRepository

NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)


class Result:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.row


class Connection:
    def __init__(self, rows):
        self.rows = list(rows)
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, sql, parameters):
        self.calls.append((sql, parameters))
        return Result(self.rows.pop(0))


class Factory:
    def __init__(self, rows):
        self.connection = Connection(rows)

    def __call__(self, *_args, **_kwargs):
        return self.connection


def batch_row(batch_id, owner_id, request_id, source_id):
    return {
        "batch_id": batch_id,
        "producer_owner_internal_user_id": owner_id,
        "client_request_id": request_id,
        "selected_source_ids": [source_id],
        "trigger_kind": "schedule",
        "state": "queued",
        "analyzer_version": "configured-model-v1",
        "source_failures": {},
        "error_code": None,
        "row_version": 1,
        "started_at": None,
        "finished_at": None,
        "created_at": NOW,
        "updated_at": NOW,
    }


def publication_row(publication_id, request_id, batch_id, owner_id, insight_id):
    return {
        "publication_id": publication_id,
        "workspace_key": "hr",
        "client_request_id": request_id,
        "batch_id": batch_id,
        "producer_owner_internal_user_id": owner_id,
        "insight_version_id": insight_id,
        "coverage_state": "complete",
        "source_coverage": [
            {
                "source_id": str(uuid4()),
                "state": "succeeded",
                "observed_at": NOW.isoformat(),
                "source_urls": ["https://example.com/jobs"],
                "job_count": 1,
            }
        ],
        "published_at": NOW,
    }


def test_repository_creates_a_conversation_independent_batch() -> None:
    batch_id, owner_id, request_id, source_id = uuid4(), uuid4(), uuid4(), uuid4()
    factory = Factory([batch_row(batch_id, owner_id, request_id, source_id)])
    repository = PanoramaRepository("postgresql://test", connect=factory)

    result = repository.create_production_batch(
        CreateProductionBatch(
            batch_id=batch_id,
            owner_id=owner_id,
            client_request_id=request_id,
            selected_source_ids=(source_id,),
            trigger_kind="schedule",
            analyzer_version="configured-model-v1",
        )
    )

    assert result.batch_id == batch_id
    assert "create_panorama_production_batch_v80" in factory.connection.calls[0][0]
    assert str(factory.connection.calls[0][1][0]) == str(batch_id)


def test_repository_records_sanitized_source_attempt_metadata() -> None:
    attempt_id, batch_id, owner_id, source_id = uuid4(), uuid4(), uuid4(), uuid4()
    row = {
        "attempt_id": attempt_id,
        "batch_id": batch_id,
        "producer_owner_internal_user_id": owner_id,
        "source_id": source_id,
        "source_url": "https://example.com/jobs",
        "attempt_number": 1,
        "state": "succeeded",
        "error_code": None,
        "evidence_sha256": "a" * 64,
        "evidence_locator": "sha256/aa/" + "a" * 64,
        "evidence_mime": "text/html",
        "evidence_size_bytes": 256,
        "normalized_job_count": 1,
        "observed_at": NOW,
        "created_at": NOW,
    }
    factory = Factory([row])
    repository = PanoramaRepository("postgresql://test", connect=factory)

    result = repository.record_source_attempt(
        CreateSourceCollectionAttempt(
            attempt_id=attempt_id,
            batch_id=batch_id,
            owner_id=owner_id,
            source_id=source_id,
            source_url="https://example.com/jobs",
            attempt_number=1,
            state="succeeded",
            error_code=None,
            evidence_sha256="a" * 64,
            evidence_locator="sha256/aa/" + "a" * 64,
            evidence_mime="text/html",
            evidence_size_bytes=256,
            normalized_job_count=1,
            observed_at=NOW,
        )
    )

    assert result.evidence_locator.startswith("sha256/")
    assert "record_panorama_source_attempt_v80" in factory.connection.calls[0][0]


def test_repository_reads_and_publishes_the_atomic_current_pointer() -> None:
    publication_id, request_id, batch_id, owner_id, insight_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    row = publication_row(publication_id, request_id, batch_id, owner_id, insight_id)
    factory = Factory([row, row])
    repository = PanoramaRepository("postgresql://test", connect=factory)

    current = repository.current_publication()
    published = repository.publish_production_report(
        PublishPanoramaReport(
            publication_id=publication_id,
            client_request_id=request_id,
            batch_id=batch_id,
            insight_version_id=insight_id,
            coverage_state="complete",
            source_coverage=tuple(row["source_coverage"]),
        )
    )

    assert current is not None and current.publication_id == publication_id
    assert published.publication_id == publication_id
    assert "read_current_panorama_publication_v80" in factory.connection.calls[0][0]
    assert "publish_panorama_version_v80" in factory.connection.calls[1][0]


def test_repository_returns_none_before_the_first_publication() -> None:
    repository = PanoramaRepository("postgresql://test", connect=Factory([None]))
    assert repository.current_publication() is None


def test_repository_reads_shared_publication_history_and_detail() -> None:
    publication_id, request_id, batch_id, owner_id, insight_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    row = publication_row(publication_id, request_id, batch_id, owner_id, insight_id)
    factory = Factory([[row], row])
    repository = PanoramaRepository("postgresql://test", connect=factory)

    history = repository.list_publications(limit=20)
    selected = repository.publication(publication_id)

    assert history == (selected,)
    assert "list_panorama_publications_v80" in factory.connection.calls[0][0]
    assert "read_panorama_publication_v80" in factory.connection.calls[1][0]


def test_repository_transitions_and_reads_a_production_batch() -> None:
    batch_id, owner_id, request_id, source_id = uuid4(), uuid4(), uuid4(), uuid4()
    queued = batch_row(batch_id, owner_id, request_id, source_id)
    running = queued | {
        "state": "running",
        "row_version": 2,
        "started_at": NOW,
    }
    factory = Factory([running, running])
    repository = PanoramaRepository("postgresql://test", connect=factory)

    transitioned = repository.transition_production_batch(
        TransitionProductionBatch(
            owner_id=owner_id,
            batch_id=batch_id,
            expected_row_version=1,
            state="running",
            error_code=None,
            source_failures={},
        )
    )
    loaded = repository.production_batch(owner_id, batch_id)

    assert transitioned == loaded
    assert "transition_panorama_production_batch_v80" in factory.connection.calls[0][0]
    assert "read_panorama_production_batch_v80" in factory.connection.calls[1][0]


def test_repository_reads_persisted_jobs_and_attempts_for_batch_resume() -> None:
    batch_id, owner_id, source_id, snapshot_id, observation_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    snapshot = {
        "snapshot_id": snapshot_id,
        "owner_internal_user_id": owner_id,
        "origin_client_request_id": observation_id,
        "run_id": None,
        "source_id": source_id,
        "public_job_key": "job-1",
        "title": "高级结构工程师",
        "location": "深圳",
        "duty_excerpt": "负责结构研发",
        "requirement_excerpt": "五年以上经验",
        "source_url": "https://example.com/jobs/1",
        "observed_at": NOW,
        "content_sha256": "a" * 64,
        "status": "open",
        "created_at": NOW,
        "production_batch_id": batch_id,
        "observation_id": observation_id,
    }
    attempt = {
        "attempt_id": uuid4(),
        "batch_id": batch_id,
        "producer_owner_internal_user_id": owner_id,
        "source_id": source_id,
        "source_url": "https://example.com/jobs",
        "attempt_number": 1,
        "state": "succeeded",
        "error_code": None,
        "evidence_sha256": "a" * 64,
        "evidence_locator": "sha256/aa/" + "a" * 64,
        "evidence_mime": "text/html",
        "evidence_size_bytes": 512,
        "normalized_job_count": 1,
        "observed_at": NOW,
        "created_at": NOW,
    }
    factory = Factory([[snapshot], [attempt]])
    repository = PanoramaRepository("postgresql://test", connect=factory)

    snapshots = repository.snapshots_for_production_batch(owner_id, batch_id)
    attempts = repository.source_attempts_for_production_batch(owner_id, batch_id)

    assert snapshots[0].observation_id == observation_id
    assert attempts[0].evidence_locator.startswith("sha256/")
    assert "read_panorama_production_snapshots_v80" in factory.connection.calls[0][0]
    assert "read_panorama_source_attempts_v80" in factory.connection.calls[1][0]
