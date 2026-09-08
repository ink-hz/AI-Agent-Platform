from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from app.hr.panorama_repository import PanoramaNotFound, PanoramaRepository
from app.hr.panorama_routes import build_panorama_router
from app.hr.panorama_service import PanoramaService

NOW = datetime(2026, 9, 6, 14, 37, tzinfo=UTC)
FIXTURES = Path(__file__).parent / "fixtures" / "hr_intelligence_company"


def _record() -> dict[str, object]:
    bundle_id = uuid4()
    return {
        "bundle_id": bundle_id,
        "generated_at": NOW,
        "source_catalog": {"schema_version": 1, "companies": [
            {"company_key": "alpha", "canonical_name": "甲公司", "aliases": ["Alpha"]},
            {"company_key": "beta", "canonical_name": "乙公司", "aliases": []},
        ]},
        "source_coverage": {"schema_version": 1, "companies": [
            {"company_key": "alpha", "state": "partial", "observed_at": NOW.isoformat(), "job_count": 2,
             "limitations": ["招聘渠道部分失败"], "document_limitations": []},
            {"company_key": "beta", "state": "not_observed", "observed_at": None, "job_count": None,
             "limitations": [], "document_limitations": ["官网未采集"]},
        ]},
        "analysis": [
            {"unit_id": "unit-alpha", "kind": "company", "scope_key": "alpha",
             "request": {"evidence": ["must stay server-side"]}, "usage": {"tokens": 1}, "response": {
                "summary": "甲公司独立摘要", "confidence": "medium",
                "facts": [
                    {"fact_id": "fact-job", "text": "岗位事实", "evidence_sha256": "a" * 64,
                     "source_url": "https://jobs.example/a", "observed_at": NOW.isoformat()},
                    {"fact_id": "fact-doc", "text": "官网产品事实", "evidence_sha256": "b" * 64,
                     "source_url": "https://www.example/a", "observed_at": NOW.isoformat()},
                ],
                "inferences": [{"inference_id": "inf-alpha", "text": "甲推断", "claim_type": "product_route",
                                "basis_fact_ids": ["fact-doc"]}],
                "recommendations": [{"recommendation_id": "rec-alpha", "text": "甲建议",
                                     "target_tasks": ["sourcing"], "basis_fact_ids": ["fact-job"]}],
                "alternatives": [{"alternative_id": "alt-alpha", "text": "甲替代解释",
                                  "challenged_inference_ids": ["inf-alpha"], "basis_fact_ids": ["fact-doc"]}],
                "unknowns": ["未知甲"],
            }},
            {"unit_id": "unit-beta", "kind": "company", "scope_key": "beta", "response": {
                "summary": "乙公司独立摘要", "confidence": "low", "facts": [], "inferences": [],
                "recommendations": [], "alternatives": [], "unknowns": ["未知乙"],
            }},
            {"unit_id": "unit-topic", "kind": "topic", "scope_key": "alpha", "response": {"summary": "不能混入"}},
        ],
        "aggregates": {"schema_version": 3, "company_matrix": {"alpha": {
            "job_count": 2, "directions": {"算法": 2}, "secondary_directions": {"算法/感知": 1},
            "locations": {"深圳": 2}, "tracks": {"social": 2}, "seniority": {"senior": 1},
            "job_families": {"research_development": 2}, "skills": {"Python": 1},
            "sample_snapshot_ids": ["job-a"], "ignored": "do not expose",
        }}},
    }


class Repository:
    def __init__(self, *, published: bool = True) -> None:
        self.record = _record()
        self.published = published
        self.calls: list[tuple] = []

    def current_bundle(self):
        self.calls.append(("current_bundle",))
        return self.record if self.published else None

    def list_bundles(self, *, limit=100):
        return (self.record,)

    def bundle(self, bundle_id):
        self.calls.append(("bundle", bundle_id))
        if bundle_id != self.record["bundle_id"]:
            raise PanoramaNotFound()
        return self.record

    def bundle_jobs(self, bundle_id):
        raise AssertionError("company APIs must not bulk load jobs")

    def bundle_company_jobs(self, bundle_id, company_key, *, offset, limit, location=None, status=None):
        self.calls.append(("company_jobs", bundle_id, company_key, offset, limit, location, status))
        return ({"job_id": "job-a", "company_key": company_key, "title": "算法工程师",
                 "location": location or "深圳", "status": status or "open"},), 2


class Documents:
    def read_document(self, *_):
        raise AssertionError

    def read_evidence(self, *_):
        raise AssertionError


def _service(repository: Repository) -> PanoramaService:
    return PanoramaService(repository, documents=Documents())


def test_company_list_has_independent_summaries_and_no_job_bulk_load() -> None:
    repository = Repository()
    result = _service(repository).companies()
    assert result == {
        "bundle_id": str(repository.record["bundle_id"]), "generated_at": NOW.isoformat(),
        "items": [
            {"company_key": "alpha", "canonical_name": "甲公司", "aliases": ["Alpha"],
             "summary": "甲公司独立摘要", "coverage": {"state": "partial", "observed_at": NOW.isoformat(),
             "job_count": 2, "limitations": ["招聘渠道部分失败"], "document_limitations": []}},
            {"company_key": "beta", "canonical_name": "乙公司", "aliases": [],
             "summary": "乙公司独立摘要", "coverage": {"state": "not_observed", "observed_at": None,
             "job_count": None, "limitations": [], "document_limitations": ["官网未采集"]}},
        ], "topics": {"state": "blocked"},
    }
    assert repository.calls == [("current_bundle",)]


def test_company_detail_preserves_ids_links_and_non_job_facts() -> None:
    repository = Repository()
    result = _service(repository).company("alpha", bundle_id=repository.record["bundle_id"])
    unit = result["units"][0]
    assert set(unit) == {"unit_id", "kind", "scope_key", "response"}
    assert unit["unit_id"] == "unit-alpha" and unit["kind"] == "company" and unit["scope_key"] == "alpha"
    assert [fact["fact_id"] for fact in unit["response"]["facts"]] == ["fact-job", "fact-doc"]
    assert unit["response"]["facts"][1]["source_url"] == "https://www.example/a"
    assert unit["response"]["inferences"][0] == {
        "inference_id": "inf-alpha", "text": "甲推断", "claim_type": "product_route", "basis_fact_ids": ["fact-doc"]}
    assert unit["response"]["recommendations"][0]["recommendation_id"] == "rec-alpha"
    assert unit["response"]["alternatives"][0]["alternative_id"] == "alt-alpha"
    assert result["metrics"] == {key: repository.record["aggregates"]["company_matrix"]["alpha"][key] for key in (
        "job_count", "directions", "secondary_directions", "locations", "tracks", "seniority",
        "job_families", "skills", "sample_snapshot_ids")}
    assert repository.calls == [("bundle", repository.record["bundle_id"])]


def test_missing_company_metrics_are_null_and_unknown_company_is_not_found() -> None:
    repository = Repository()
    assert _service(repository).company("beta")["metrics"] is None
    with pytest.raises(PanoramaNotFound):
        _service(repository).company("missing")


def test_company_jobs_are_pinned_and_repository_paginated() -> None:
    repository = Repository()
    result = _service(repository).company_jobs(
        "alpha", bundle_id=repository.record["bundle_id"], offset=25, limit=25, location="深圳", status="open")
    assert result == {"bundle_id": str(repository.record["bundle_id"]), "company_key": "alpha",
                      "items": [{"job_id": "job-a", "company_key": "alpha", "title": "算法工程师",
                                 "location": "深圳", "status": "open"}], "total": 2, "offset": 25, "limit": 25}
    assert repository.calls == [
        ("bundle", repository.record["bundle_id"]),
        ("company_jobs", repository.record["bundle_id"], "alpha", 25, 25, "深圳", "open"),
    ]


def test_company_api_empty_unknown_validation_and_auth_order() -> None:
    repository = Repository(published=False)
    service = _service(repository)
    app = FastAPI()
    allowed = True

    async def authorize(_request: Request, *, writable=False):
        assert writable is False
        if not allowed:
            raise HTTPException(403, "denied")
        return uuid4()

    app.include_router(build_panorama_router(service, authorize))
    client = TestClient(app)
    assert client.get("/api/hr/panorama/companies").status_code == 204
    repository.published = True
    assert client.get("/api/hr/panorama/companies/missing").status_code == 404
    assert client.get("/api/hr/panorama/companies/alpha/jobs?offset=-1").status_code == 422
    assert client.get("/api/hr/panorama/companies/alpha/jobs?limit=101").status_code == 422
    assert client.get("/api/hr/panorama/companies/alpha/jobs?offset=100001").status_code == 422
    assert client.get("/api/hr/panorama/companies/alpha/jobs?status=deleted").status_code == 422
    calls = list(repository.calls)
    allowed = False
    assert client.get("/api/hr/panorama/companies/alpha/jobs").status_code == 403
    assert repository.calls == calls


def test_router_rejects_service_missing_company_contract_at_startup() -> None:
    class LegacyOnlyService:
        def current_report(self): pass
        def list_reports(self): pass
        def report(self): pass
        def document(self): pass
        def evidence_file(self): pass

    with pytest.raises(ValueError, match="panorama service required"):
        build_panorama_router(LegacyOnlyService(), lambda *_args, **_kwargs: uuid4())


class Result:
    def __init__(self, row): self.row = row
    def fetchone(self): return self.row


class Connection:
    def __init__(self, row=None): self.calls = []; self.row = row
    def execute(self, query, params):
        self.calls.append((query, params))
        return Result(self.row or {"items": [{"job_id": "a", "company_key": "alpha"}], "total": 3})


def test_repository_filters_and_paginates_through_security_definer_function() -> None:
    connection = Connection()

    @contextmanager
    def connect(): yield connection

    bundle_id = uuid4()
    items, total = PanoramaRepository(connection=connect).bundle_company_jobs(
        bundle_id, "alpha", offset=1, limit=2, location="深圳", status="open")
    query, params = connection.calls[0]
    normalized = " ".join(query.split())
    assert "read_intelligence_bundle_jobs_v85" in normalized
    assert "intelligence_bundle_jobs" not in normalized.replace("read_intelligence_bundle_jobs_v85", "")
    assert "company_key=%s" in normalized and "job->>'location'=%s" in normalized and "job->>'status'=%s" in normalized
    assert "offset %s limit %s" in normalized
    assert params == (bundle_id, "alpha", "深圳", "open", 1, 2)
    assert items == ({"job_id": "a", "company_key": "alpha"},) and total == 3


def test_service_and_repository_reject_excessive_offset_before_database() -> None:
    repository = Repository()
    with pytest.raises(ValueError, match="offset"):
        _service(repository).company_jobs("alpha", offset=100_001)
    assert repository.calls == []

    connection = Connection()

    @contextmanager
    def connect(): yield connection

    with pytest.raises(ValueError, match="offset"):
        PanoramaRepository(connection=connect).bundle_company_jobs(
            uuid4(), "alpha", offset=100_001, limit=25)
    assert connection.calls == []


def test_real_derived_fixture_preserves_contract_and_provenance() -> None:
    companies = json.loads((FIXTURES / "companies.json").read_text("utf-8"))
    detail = json.loads((FIXTURES / "company-insta360.json").read_text("utf-8"))
    missing = json.loads((FIXTURES / "company-scantech-missing-metrics.json").read_text("utf-8"))
    jobs = json.loads((FIXTURES / "company-insta360-jobs-page.json").read_text("utf-8"))
    provenance = json.loads((FIXTURES / "provenance.json").read_text("utf-8"))
    assert len(companies["items"]) == 12 and companies["topics"] == {"state": "blocked"}
    assert detail["company"]["company_key"] == "insta360"
    assert any(fact["source_url"] == "https://www.insta360.com/cn/" for fact in detail["units"][0]["response"]["facts"])
    assert missing["company"]["company_key"] == "scantech" and missing["metrics"] is None
    assert jobs["total"] == 375 and len(jobs["items"]) == jobs["limit"] == 25
    assert provenance == {
        "extraction_script": "extract_fixture.py",
        "source_bundle_id": "2b49ecc4-42fe-45ae-80ac-26891f42ac6c",
        "source_generated_at": "2026-09-06T14:37:42.467393+00:00",
        "source_manifest_sha256": "5d446fe136a3fe2d3d1bb06873f9584e4a357f9546e9f66e686e14950dab98a3",
    }


def test_repository_company_reads_project_before_crossing_process_boundary() -> None:
    bundle_id = uuid4()
    row = {"bundle_id": bundle_id, "generated_at": NOW, "source_catalog": {},
           "source_coverage": {}, "analysis": [], "aggregates": {}}
    connection = Connection(row)

    @contextmanager
    def connect(): yield connection

    repository = PanoramaRepository(connection=connect)
    assert repository.current_company_directory() == row
    assert repository.company_bundle("alpha", bundle_id=bundle_id) == row
    assert repository.company_identity("alpha", bundle_id=bundle_id) == bundle_id
    directory, detail, identity = connection.calls
    for query, _ in connection.calls:
        normalized = " ".join(query.split()).casefold()
        assert "select *" not in normalized
        assert "intelligence_bundles" not in normalized.replace("read_current_intelligence_bundle_v85", "").replace("read_intelligence_bundle_v85", "")
    assert "jsonb_build_object('summary'" in directory[0]
    assert "unit->'request'" not in directory[0] and "unit->'evidence'" not in directory[0]
    assert "unit->'response'" in detail[0] and "unit->'usage'" not in detail[0]
    assert "source_catalog->'companies'" in identity[0] and "analysis" not in identity[0]
    assert directory[1] == () and detail[1] == ("alpha", bundle_id) and identity[1] == ("alpha", bundle_id)
