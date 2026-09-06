from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.hr.panorama_service import PanoramaService

NOW = datetime(2026, 9, 6, 8, tzinfo=UTC)


class Repository:
    def __init__(self, *, published: bool = True) -> None:
        self.bundle_id = uuid4()
        self.source_id = uuid4()
        self.job_id = uuid4()
        self.published = published
        self.calls: list[tuple] = []
        self.record = {
            "bundle_id": self.bundle_id,
            "manifest_sha256": "b" * 64,
            "bundle_locator": f"bundles/{self.bundle_id}",
            "schema_version": 1,
            "generated_at": NOW,
            "company_count": 2,
            "job_count": 1,
            "analysis_count": 1,
            "evidence_count": 1,
            "manifest": {"schema_version": 1},
            "source_catalog": {"schema_version": 1, "companies": [
                {"company_key": "hesai", "canonical_name": "禾赛科技", "aliases": ["禾赛"], "approved_urls": ["https://example.com/jobs"]},
                {"company_key": "empty", "canonical_name": "空样本公司", "aliases": [], "approved_urls": ["https://empty.example.com/jobs"]},
            ]},
            "source_coverage": {"schema_version": 1, "companies": [
                {"company_key": "hesai", "state": "succeeded", "observed_at": NOW.isoformat(), "job_count": 1},
                {"company_key": "empty", "state": "not_observed", "observed_at": None, "job_count": None},
            ]},
            "aggregates": {"schema_version": 2, "tracks": {"social": 1}},
            "analysis": [{
                "unit_id": str(uuid4()), "kind": "company", "scope_key": "hesai",
                "response": {
                    "facts": [{"fact_id": "f1", "text": "禾赛公开招聘算法工程师", "evidence_sha256": "a" * 64, "source_url": "https://example.com/jobs/1", "observed_at": NOW.isoformat()}],
                    "inferences": [{"text": "算法人才投入明确", "basis_fact_ids": ["f1"]}],
                    "unknowns": ["实际 HC 未公开"], "alternatives": [], "summary": "算法岗位有公开证据", "confidence": "high",
                },
                "usage": {"model": "gpt-5.6", "provider": "openai"},
            }],
            "analysis_usage": [{"provider": "openai", "model": "gpt-5.6"}],
            "evidence_index": [{"sha256": "a" * 64, "source_url": "https://example.com/jobs/1", "observed_at": NOW.isoformat(), "mime": "application/json", "size_bytes": 20, "locator": "evidence/sha256/aa/" + "a" * 64}],
            "document_index": {"report.pdf": {"sha256": hashlib.sha256(b"pdf").hexdigest(), "size_bytes": 3, "mime": "application/pdf"}},
            "imported_at": NOW,
        }
        self.jobs = ({
            "job_id": str(self.job_id), "source_id": str(self.source_id), "company_key": "hesai", "public_job_key": "job-1",
            "title": "算法工程师", "location": "上海", "duty_excerpt": "负责点云算法", "requirement_excerpt": "熟悉 Python",
            "source_url": "https://example.com/jobs/1", "evidence_sha256": "a" * 64, "observed_at": NOW.isoformat(), "status": "open",
        },)

    def current_bundle(self):
        self.calls.append(("current",))
        return self.record if self.published else None

    def list_bundles(self, *, limit=100):
        self.calls.append(("list", limit))
        return (self.record,)

    def bundle(self, bundle_id):
        self.calls.append(("bundle", bundle_id))
        return self.record

    def bundle_jobs(self, bundle_id):
        self.calls.append(("jobs", bundle_id))
        return self.jobs


class Documents:
    def __init__(self) -> None:
        self.calls = []

    def read_document(self, bundle_id, name):
        self.calls.append(("document", bundle_id, name))
        return {"name": name, "mime": "application/pdf", "sha256": "c" * 64, "body": b"prebuilt"}

    def read_evidence(self, bundle_id, sha256):
        self.calls.append(("evidence", bundle_id, sha256))
        return {"name": sha256, "mime": "application/json", "sha256": sha256, "body": b"source"}


def test_service_returns_none_without_a_published_bundle() -> None:
    assert PanoramaService(Repository(published=False), documents=Documents()).current_report() is None


def test_service_projects_bundle_jobs_analysis_and_five_state_coverage() -> None:
    repository = Repository()
    report = PanoramaService(repository, documents=Documents()).current_report()
    assert report is not None
    assert report["publication"]["bundle_id"] == str(repository.bundle_id)
    assert report["publication"]["manifest_sha256"] == "b" * 64
    assert report["publication"]["source_coverage"][1]["state"] == "not_observed"
    assert report["snapshots"][0]["title"] == "算法工程师"
    assert report["insight"]["inferences"][0]["text"] == "算法人才投入明确"
    assert report["analysis_usage"][0]["model"] == "gpt-5.6"
    assert repository.calls == [("current",), ("jobs", repository.bundle_id)]


def test_service_reads_prebuilt_documents_without_an_exporter() -> None:
    repository = Repository()
    documents = Documents()
    service = PanoramaService(repository, documents=documents)
    selected = service.document(repository.bundle_id, "pdf")
    assert selected["body"] == b"prebuilt"
    assert documents.calls == [("document", repository.bundle_id, "report.pdf")]
    assert not hasattr(service, "start_run")


@pytest.mark.parametrize("format", ["docx", "", None])
def test_service_rejects_unknown_document_formats(format) -> None:
    with pytest.raises(TypeError, match="format"):
        PanoramaService(Repository(), documents=Documents()).document(uuid4(), format)
