from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.hr.panorama_models import (
    CreateProductionBatch,
    CreateSourceCollectionAttempt,
    PublishPanoramaReport,
)
from app.hr.panorama_repository import PanoramaRepository

NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)


class Result:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
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
        "source_coverage": [{
            "source_id": str(uuid4()),
            "state": "succeeded",
            "observed_at": NOW.isoformat(),
            "source_urls": ["https://example.com/jobs"],
            "job_count": 1,
        }],
        "published_at": NOW,
    }


def test_repository_creates_a_conversation_independent_batch() -> None:
    batch_id, owner_id, request_id, source_id = uuid4(), uuid4(), uuid4(), uuid4()
    factory = Factory([batch_row(batch_id, owner_id, request_id, source_id)])
    repository = PanoramaRepository("postgresql://test", connect=factory)

    result = repository.create_production_batch(CreateProductionBatch(
        batch_id=batch_id,
        owner_id=owner_id,
        client_request_id=request_id,
        selected_source_ids=(source_id,),
        trigger_kind="schedule",
        analyzer_version="configured-model-v1",
    ))

    assert result.batch_id == batch_id
    assert "create_panorama_production_batch_v80" in factory.connection.calls[0][0]
    assert str(factory.connection.calls[0][1][0]) == str(batch_id)


def test_repository_records_sanitized_source_attempt_metadata() -> None:
    attempt_id, batch_id, owner_id, source_id = uuid4(), uuid4(), uuid4(), uuid4()
    row = {
        "attempt_id": attempt_id, "batch_id": batch_id,
        "producer_owner_internal_user_id": owner_id, "source_id": source_id,
        "source_url": "https://example.com/jobs", "attempt_number": 1,
        "state": "succeeded", "error_code": None, "evidence_sha256": "a" * 64,
        "evidence_locator": "sha256/aa/" + "a" * 64, "evidence_mime": "text/html",
        "evidence_size_bytes": 256, "normalized_job_count": 1,
        "observed_at": NOW, "created_at": NOW,
    }
    factory = Factory([row])
    repository = PanoramaRepository("postgresql://test", connect=factory)

    result = repository.record_source_attempt(CreateSourceCollectionAttempt(
        attempt_id=attempt_id, batch_id=batch_id, owner_id=owner_id,
        source_id=source_id, source_url="https://example.com/jobs",
        attempt_number=1, state="succeeded", error_code=None,
        evidence_sha256="a" * 64, evidence_locator="sha256/aa/" + "a" * 64,
        evidence_mime="text/html", evidence_size_bytes=256,
        normalized_job_count=1, observed_at=NOW,
    ))

    assert result.evidence_locator.startswith("sha256/")
    assert "record_panorama_source_attempt_v80" in factory.connection.calls[0][0]


def test_repository_reads_and_publishes_the_atomic_current_pointer() -> None:
    publication_id, request_id, batch_id, owner_id, insight_id = (
        uuid4(), uuid4(), uuid4(), uuid4(), uuid4()
    )
    row = publication_row(publication_id, request_id, batch_id, owner_id, insight_id)
    factory = Factory([row, row])
    repository = PanoramaRepository("postgresql://test", connect=factory)

    current = repository.current_publication()
    published = repository.publish_production_report(PublishPanoramaReport(
        publication_id=publication_id,
        client_request_id=request_id,
        batch_id=batch_id,
        insight_version_id=insight_id,
        coverage_state="complete",
        source_coverage=tuple(row["source_coverage"]),
    ))

    assert current is not None and current.publication_id == publication_id
    assert published.publication_id == publication_id
    assert "read_current_panorama_publication_v80" in factory.connection.calls[0][0]
    assert "publish_panorama_version_v80" in factory.connection.calls[1][0]


def test_repository_returns_none_before_the_first_publication() -> None:
    repository = PanoramaRepository("postgresql://test", connect=Factory([None]))
    assert repository.current_publication() is None
