from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.hr.panorama_models import (
    PanoramaReport,
    PublicJobSnapshot,
    PublishedPanorama,
    SourceCollectionAttempt,
    TalentInsightVersion,
    TalentSource,
)
from app.hr.panorama_service import PanoramaService

NOW = datetime(2026, 9, 6, 8, tzinfo=UTC)


class Repository:
    def __init__(self, *, published: bool = True) -> None:
        owner_id, batch_id, source_id, snapshot_id, observation_id = (
            uuid4(),
            uuid4(),
            uuid4(),
            uuid4(),
            uuid4(),
        )
        source = TalentSource(
            source_id,
            owner_id,
            uuid4(),
            "company",
            "company-union-optech",
            "联合光电",
            (),
            ("https://example.com/jobs",),
            True,
            NOW,
            NOW,
        )
        snapshot = PublicJobSnapshot(
            snapshot_id,
            owner_id,
            observation_id,
            None,
            source_id,
            "job-1",
            "结构工程师",
            "中山",
            "负责结构研发",
            "五年以上经验",
            "https://example.com/jobs/1",
            NOW,
            "a" * 64,
            "open",
            NOW,
            batch_id,
            observation_id,
        )
        insight = TalentInsightVersion(
            uuid4(),
            owner_id,
            uuid4(),
            None,
            1,
            (source_id,),
            (snapshot_id,),
            (
                {
                    "fact_id": "f1",
                    "text": "公开招聘结构工程师",
                    "snapshot_id": str(snapshot_id),
                    "observation_id": str(observation_id),
                    "source_url": snapshot.source_url,
                    "observed_at": NOW.isoformat(),
                },
            ),
            ({"text": "结构投入明确", "basis_fact_ids": ("f1",)},),
            ({"text": "实际 HC 未公开"},),
            {"结构": 1},
            "结构研发招聘持续",
            None,
            None,
            "hr-intelligence-producer",
            "configured-model-v1",
            NOW,
            batch_id,
        )
        self.publication_value = PublishedPanorama(
            uuid4(),
            uuid4(),
            batch_id,
            owner_id,
            insight.insight_version_id,
            "hr",
            "complete",
            (
                {
                    "source_id": str(source_id),
                    "state": "succeeded",
                    "observed_at": NOW.isoformat(),
                    "source_urls": ("https://example.com/jobs",),
                    "job_count": 1,
                },
            ),
            NOW,
        )
        self.base_report = PanoramaReport(insight, (source,), (snapshot,))
        self.published = published
        self.calls: list[tuple] = []
        self.attempts = ()

    def current_publication(self):
        self.calls.append(("current",))
        return self.publication_value if self.published else None

    def list_publications(self, *, limit=100):
        self.calls.append(("list", limit))
        return (self.publication_value,)

    def publication(self, publication_id):
        self.calls.append(("publication", publication_id))
        return self.publication_value

    def report(self, owner_id, insight_version_id):
        self.calls.append(("report", owner_id, insight_version_id))
        return self.base_report

    def source_attempts_for_production_batch(self, owner_id, batch_id):
        self.calls.append(("attempts", owner_id, batch_id))
        return self.attempts


def test_service_returns_none_without_a_quality_gated_publication() -> None:
    service = PanoramaService(Repository(published=False))
    assert service.current_report() is None


def test_service_attaches_publication_metadata_to_current_report() -> None:
    repository = Repository()
    report = PanoramaService(repository).current_report()

    assert report is not None
    assert report.publication == repository.publication_value
    assert report.snapshots == repository.base_report.snapshots
    assert repository.calls == [
        ("current",),
        (
            "report",
            repository.publication_value.owner_id,
            repository.publication_value.insight_version_id,
        ),
        (
            "attempts",
            repository.publication_value.owner_id,
            repository.publication_value.batch_id,
        ),
    ]


def test_service_history_and_detail_are_publication_scoped() -> None:
    repository = Repository()
    service = PanoramaService(repository)

    history = service.list_reports(limit=20)
    selected = service.report(repository.publication_value.publication_id)

    assert history[0].publication == selected.publication
    assert repository.calls[0] == ("list", 20)
    assert repository.calls[3] == (
        "publication",
        repository.publication_value.publication_id,
    )


def test_service_has_no_collection_or_source_mutation_surface() -> None:
    service = PanoramaService(Repository())
    for forbidden in ("add_company", "start_run", "run_status", "list_companies"):
        assert not hasattr(service, forbidden)


def test_service_downloads_only_evidence_bound_to_the_selected_publication() -> None:
    repository = Repository()
    body = b'{"jobs":[]}'
    repository.attempts = (
        SourceCollectionAttempt(
            uuid4(),
            repository.publication_value.batch_id,
            repository.publication_value.owner_id,
            repository.base_report.sources[0].source_id,
            "https://example.com/jobs",
            1,
            "succeeded",
            None,
            "a" * 64,
            "sha256/aa/" + "a" * 64,
            "application/json; charset=utf-8",
            len(body),
            1,
            NOW,
            NOW,
        ),
    )

    class Archive:
        def read(self, sha256):
            assert sha256 == "a" * 64
            return body

    selected = PanoramaService(
        repository, evidence_archive=Archive()
    ).evidence_file(repository.publication_value.publication_id, "a" * 64)

    assert selected.body == body
    assert selected.mime == "application/json"


@pytest.mark.parametrize("sha256", (None, 7, "A" * 64, "a" * 63))
def test_evidence_download_rejects_noncanonical_hashes(sha256) -> None:
    repository = Repository()
    service = PanoramaService(repository)

    with pytest.raises(TypeError, match="evidence identifier invalid"):
        service.evidence_file(repository.publication_value.publication_id, sha256)
