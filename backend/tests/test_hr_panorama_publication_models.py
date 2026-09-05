from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from app.hr.panorama_models import (
    CreateProductionBatch,
    CreateSourceCollectionAttempt,
    ProductionBatch,
    PublishedPanorama,
    PublishPanoramaReport,
    SourceCollectionAttempt,
)

NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)


def test_production_batch_is_not_a_conversation_run() -> None:
    source_id = uuid4()
    batch = ProductionBatch(
        batch_id=uuid4(),
        owner_id=uuid4(),
        client_request_id=uuid4(),
        selected_source_ids=(source_id,),
        trigger_kind="schedule",
        state="queued",
        analyzer_version="configured-model-v1",
        source_failures={},
        error_code=None,
        row_version=1,
        started_at=None,
        finished_at=None,
        created_at=NOW,
        updated_at=NOW,
    )

    assert batch.selected_source_ids == (source_id,)
    assert not hasattr(batch, "conversation_id")


def test_source_attempt_keeps_only_sanitized_evidence_metadata() -> None:
    attempt = SourceCollectionAttempt(
        attempt_id=uuid4(),
        batch_id=uuid4(),
        owner_id=uuid4(),
        source_id=uuid4(),
        source_url="https://example.com/jobs",
        attempt_number=1,
        state="succeeded",
        error_code=None,
        evidence_sha256="a" * 64,
        evidence_locator="sha256/aa/" + "a" * 64,
        evidence_mime="text/html",
        evidence_size_bytes=1024,
        normalized_job_count=5,
        observed_at=NOW,
        created_at=NOW,
    )

    assert attempt.normalized_job_count == 5
    assert not hasattr(attempt, "request_headers")


def test_failed_attempt_rejects_evidence_metadata() -> None:
    with pytest.raises(ValueError, match="attempt lifecycle"):
        CreateSourceCollectionAttempt(
            attempt_id=uuid4(),
            batch_id=uuid4(),
            owner_id=uuid4(),
            source_id=uuid4(),
            source_url="https://example.com/jobs",
            attempt_number=1,
            state="failed",
            error_code="source_timeout",
            evidence_sha256="a" * 64,
            evidence_locator="sha256/aa/" + "a" * 64,
            evidence_mime="text/html",
            evidence_size_bytes=1024,
            normalized_job_count=0,
            observed_at=NOW,
        )


def test_publication_requires_grounded_coverage_records() -> None:
    source_id = uuid4()
    coverage = ({
        "source_id": str(source_id),
        "state": "succeeded",
        "observed_at": NOW.isoformat(),
        "source_urls": ["https://example.com/jobs"],
        "job_count": 5,
    },)
    publication = PublishedPanorama(
        publication_id=uuid4(),
        client_request_id=uuid4(),
        batch_id=uuid4(),
        owner_id=uuid4(),
        insight_version_id=uuid4(),
        workspace_key="hr",
        coverage_state="complete",
        source_coverage=coverage,
        published_at=NOW,
    )

    assert publication.source_coverage[0]["source_id"] == str(source_id)
    with pytest.raises(ValueError, match="coverage"):
        PublishPanoramaReport(
            publication_id=uuid4(),
            client_request_id=uuid4(),
            batch_id=uuid4(),
            insight_version_id=uuid4(),
            coverage_state="partial",
            source_coverage=(),
        )


def test_create_batch_rejects_duplicate_sources() -> None:
    source_id = uuid4()
    with pytest.raises(ValueError, match="source selection"):
        CreateProductionBatch(
            batch_id=uuid4(),
            owner_id=uuid4(),
            client_request_id=uuid4(),
            selected_source_ids=(source_id, source_id),
            trigger_kind="operator",
            analyzer_version="configured-model-v1",
        )
