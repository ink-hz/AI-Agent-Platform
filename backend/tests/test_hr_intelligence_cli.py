import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

import pytest

from tools.hr_intelligence.cli import (
    _company_source_id,
    _empty_dimensions,
    _job_dict,
    _job_from_dict,
    _parser,
    main,
)
from tools.hr_intelligence.collectors import (
    CollectionResult,
    NormalizedPublicJob,
)
from tools.hr_intelligence.evidence import EvidenceArchive, EvidencePayload
from tools.hr_intelligence.models import NormalizedJob


def test_cli_job_serialization_preserves_raw_location() -> None:
    job = NormalizedJob(
        job_id=UUID("00000000-0000-4000-8000-000000000011"),
        source_id=UUID("00000000-0000-4000-8000-000000000012"),
        company_key="example",
        public_job_key="job-1",
        title="算法工程师",
        location="深圳",
        raw_location="广东省·深圳市",
        duty_excerpt="负责算法开发",
        requirement_excerpt="本科",
        source_url="https://example.com/jobs/1",
        evidence_sha256="a" * 64,
        observed_at=datetime(2026, 9, 6, 8, tzinfo=UTC),
    )

    encoded = _job_dict(job)

    assert encoded["raw_location"] == "广东省·深圳市"
    assert _job_from_dict(encoded).raw_location == "广东省·深圳市"


def test_cli_empty_dimensions_match_v3_shape() -> None:
    dimensions = _empty_dimensions()

    assert dimensions["schema_version"] == 3
    assert dimensions["secondary_directions"] == {}
    assert dimensions["company_comparison"] == {}
    assert dimensions["data_quality"] == {"invalid_locations": {}}


def test_prepare_analysis_defaults_to_complete_v2_unit_set() -> None:
    args = _parser().parse_args(
        ["prepare-analysis", "--bundle-id", "00000000-0000-4000-8000-000000000001"]
    )

    assert args.units == (
        "company,track,direction,secondary-direction,topic,executive-summary,task"
    )


def test_company_source_identity_is_stable_across_recruiting_channels() -> None:
    assert _company_source_id("hesai") == _company_source_id("hesai")
    assert _company_source_id("hesai") != _company_source_id("robosense")


def test_cli_initializes_only_the_explicit_external_local_root(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    bundle_id = UUID("00000000-0000-4000-8000-000000000001")
    catalog = tmp_path / "catalog.json"
    catalog.write_text('{"schema_version":1,"companies":[]}', encoding="utf-8")
    local_root = tmp_path / "factory"
    monkeypatch.setenv("HR_INTELLIGENCE_LOCAL_ROOT", str(local_root))

    assert (
        main(
            [
                "init",
                "--bundle-id",
                str(bundle_id),
                "--catalog",
                str(catalog),
            ]
        )
        == 0
    )

    assert (local_root / "work" / str(bundle_id) / "source-catalog.json").is_file()
    assert str(bundle_id) in capsys.readouterr().out


def test_cli_rejects_relative_catalog_path(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HR_INTELLIGENCE_LOCAL_ROOT", str(tmp_path / "factory"))

    with pytest.raises(ValueError, match="absolute"):
        main(
            [
                "init",
                "--bundle-id",
                "00000000-0000-4000-8000-000000000001",
                "--catalog",
                "relative.json",
            ]
        )


def test_collect_resume_skips_successful_channels_and_retries_only_failures(
    tmp_path, monkeypatch,
) -> None:
    bundle_id = UUID("00000000-0000-4000-8000-000000000003")
    local_root = tmp_path / "factory"
    monkeypatch.setenv("HR_INTELLIGENCE_LOCAL_ROOT", str(local_root))
    catalog = tmp_path / "catalog.json"
    urls = ["https://example.com/social", "https://example.com/campus"]
    catalog.write_text(json.dumps({
        "schema_version": 1,
        "companies": [{
            "company_key": "example",
            "canonical_name": "示例公司",
            "approved_urls": urls,
        }],
    }), encoding="utf-8")
    assert main([
        "init", "--bundle-id", str(bundle_id), "--catalog", str(catalog),
    ]) == 0
    work = local_root / "work" / str(bundle_id)
    existing_body = b"existing social evidence"
    existing_record = EvidenceArchive(work / "evidence").store(EvidencePayload(
        urls[0], "application/json", existing_body,
    ))
    existing_job = {
        "job_id": "00000000-0000-4000-8000-000000000030",
        "source_id": str(_company_source_id("example")),
        "company_key": "example",
        "public_job_key": "social-1",
        "title": "结构工程师",
        "location": "深圳",
        "duty_excerpt": "负责结构设计",
        "requirement_excerpt": "三年以上经验",
        "source_url": urls[0] + "/1",
        "evidence_sha256": existing_record.sha256,
        "observed_at": "2026-09-06T08:00:00+00:00",
        "status": "open",
    }
    (work / "normalized-jobs.jsonl").write_text(
        json.dumps(existing_job, ensure_ascii=False) + "\n", encoding="utf-8",
    )
    (work / "source-coverage.json").write_text(json.dumps({
        "schema_version": 1,
        "companies": [{
            "company_key": "example", "state": "partial",
            "observed_at": "2026-09-06T08:00:00+00:00", "job_count": 1,
            "channels": [
                {"ordinal": 0, "source_url": urls[0], "state": "succeeded",
                 "observed_at": "2026-09-06T08:00:00+00:00", "job_count": 1,
                 "evidence_sha256": existing_record.sha256, "error_code": None},
                {"ordinal": 1, "source_url": urls[1], "state": "failed",
                 "observed_at": "2026-09-06T08:00:00+00:00", "job_count": None,
                 "evidence_sha256": None, "error_code": "source_timeout"},
            ],
            "limitations": ["一个或多个公开招聘渠道未能完成采集"],
        }],
    }), encoding="utf-8")
    calls: list[str] = []

    async def collect_only_failed(self, target):
        calls.append(target.source_url)
        assert target.source_url == urls[1]
        record = self._archive.store(EvidencePayload(
            target.source_url, "application/json", b"new campus evidence",
        ))
        return CollectionResult(target, (NormalizedPublicJob(
            "campus-1", "算法工程师", "上海", "负责算法开发", "硕士",
            urls[1] + "/1",
        ),), record, datetime(2026, 9, 6, 9, tzinfo=UTC))

    monkeypatch.setattr(
        "tools.hr_intelligence.cli.PublicSourceCollector.collect",
        collect_only_failed,
    )
    assert main(["collect", "--bundle-id", str(bundle_id), "--resume"]) == 0

    assert calls == [urls[1]]
    jobs = [json.loads(line) for line in (
        work / "normalized-jobs.jsonl"
    ).read_text("utf-8").splitlines()]
    assert {job["public_job_key"] for job in jobs} == {"social-1", "campus-1"}
    coverage = json.loads((work / "source-coverage.json").read_text("utf-8"))
    assert coverage["companies"][0]["state"] == "succeeded"
    assert [channel["state"] for channel in coverage["companies"][0]["channels"]] == [
        "succeeded", "succeeded",
    ]


def test_cli_prepares_accepts_builds_and_verifies_one_bundle(
    tmp_path,
    monkeypatch,
) -> None:
    bundle_id = UUID("00000000-0000-4000-8000-000000000002")
    local_root = tmp_path / "factory"
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "companies": [
                    {
                        "company_key": "hesai",
                        "canonical_name": "禾赛科技",
                        "approved_urls": ["https://example.com/jobs"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HR_INTELLIGENCE_LOCAL_ROOT", str(local_root))
    assert main(["init", "--bundle-id", str(bundle_id), "--catalog", str(catalog)]) == 0
    work = local_root / "work" / str(bundle_id)
    evidence_body = b"one immutable public source"
    evidence_sha256 = hashlib.sha256(evidence_body).hexdigest()
    evidence = work / "evidence" / "sha256" / evidence_sha256[:2] / evidence_sha256
    evidence.parent.mkdir(parents=True)
    evidence.write_bytes(evidence_body)
    job = {
        "job_id": "00000000-0000-4000-8000-000000000010",
        "source_id": "00000000-0000-4000-8000-000000000011",
        "company_key": "hesai",
        "public_job_key": "algorithm-1",
        "title": "算法工程师",
        "location": "上海",
        "duty_excerpt": "负责点云算法",
        "requirement_excerpt": "熟悉 Python",
        "source_url": "https://example.com/social/jobs/1",
        "evidence_sha256": evidence_sha256,
        "observed_at": "2026-09-06T08:00:00+00:00",
        "status": "open",
    }
    (work / "normalized-jobs.jsonl").write_text(
        json.dumps(job, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (work / "source-coverage.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "companies": [
                    {
                        "company_key": "hesai",
                        "state": "succeeded",
                        "observed_at": "2026-09-06T08:00:00+00:00",
                        "job_count": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (work / "aggregates.json").write_text(
        json.dumps({"schema_version": 2, "tracks": {"social": 1}}),
        encoding="utf-8",
    )
    assert (
        main(
            [
                "prepare-analysis",
                "--bundle-id",
                str(bundle_id),
                "--units",
                "company,comparison,executive-summary",
            ]
        )
        == 0
    )
    for request_path in sorted((work / "analysis" / "requests").glob("*.json")):
        request = json.loads(request_path.read_text("utf-8"))
        unit_id = request["unit_id"]
        evidence_ref = request["evidence"][0]
        response = {
            "schema_version": 2,
            "facts": [
                {
                    "fact_id": "fact-1",
                    "text": "公开岗位需要点云算法能力",
                    "evidence_sha256": evidence_ref["sha256"],
                    "source_url": evidence_ref["source_url"],
                    "observed_at": evidence_ref["observed_at"],
                }
            ],
            "inferences": [
                {
                    "inference_id": "inference-1",
                    "text": "存在点云算法人才需求信号",
                    "basis_fact_ids": ["fact-1"],
                }
            ],
            "unknowns": ["实际 HC 未公开"],
            "alternatives": [
                {
                    "alternative_id": "alternative-1",
                    "text": "可能是常规补员",
                    "basis_fact_ids": ["fact-1"],
                    "challenged_inference_ids": ["inference-1"],
                }
            ],
            "recommendations": [
                {
                    "recommendation_id": "recommendation-1",
                    "text": "建立点云算法人才池",
                    "basis_fact_ids": ["fact-1"],
                    "target_tasks": ["talent_profile", "sourcing_strategy"],
                }
            ],
            "summary": "公开招聘信号指向点云算法能力。",
            "confidence": "medium",
        }
        usage = {
            "unit_id": unit_id,
            "provider": "openai",
            "model": "gpt-5.6-sol",
            "input_tokens": "unavailable",
            "output_tokens": "unavailable",
            "estimated_cost": "unavailable",
            "unavailable_reason": "test fixture has no provider telemetry",
        }
        response_path = work / "analysis" / "responses" / f"{unit_id}.json"
        usage_path = work / "analysis" / "usage" / f"{unit_id}.json"
        response_path.parent.mkdir(parents=True, exist_ok=True)
        usage_path.parent.mkdir(parents=True, exist_ok=True)
        response_path.write_text(json.dumps(response), encoding="utf-8")
        usage_path.write_text(json.dumps(usage), encoding="utf-8")
    assert main(["accept-analysis", "--bundle-id", str(bundle_id), "--all-ready"]) == 0
    assert (
        main(["analysis-status", "--bundle-id", str(bundle_id), "--require-complete"])
        == 0
    )
    assert main(["build", "--bundle-id", str(bundle_id)]) == 0
    built = local_root / "bundles" / str(bundle_id)
    assert main(["verify", "--bundle", str(built), "--strict"]) == 0


def test_cli_provenance_validation_covers_channel_evidence_without_jobs(
    tmp_path,
    monkeypatch,
) -> None:
    bundle_id = UUID("00000000-0000-4000-8000-000000000099")
    local_root = tmp_path / "factory"
    work = local_root / "work" / str(bundle_id)
    work.mkdir(parents=True)
    monkeypatch.setenv("HR_INTELLIGENCE_LOCAL_ROOT", str(local_root))
    (work / "source-catalog.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "companies": [
                    {
                        "company_key": "scantech",
                        "canonical_name": "思看科技",
                        "approved_urls": ["https://example.com/jobs"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    missing_sha256 = hashlib.sha256(b"missing channel evidence").hexdigest()
    (work / "source-coverage.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "companies": [
                    {
                        "company_key": "scantech",
                        "state": "empty_confirmed",
                        "job_count": 0,
                        "channels": [
                            {
                                "source_url": "https://example.com/jobs",
                                "state": "succeeded",
                                "job_count": 0,
                                "evidence_sha256": missing_sha256,
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (work / "normalized-jobs.jsonl").write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="source coverage evidence invalid"):
        main(
            [
                "validate",
                "--bundle-id",
                str(bundle_id),
                "--require-company-count",
                "1",
                "--require-provenance",
            ]
        )
