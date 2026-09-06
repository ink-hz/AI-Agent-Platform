from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from tools.hr_intelligence.analysis_quality import validate_analysis_set
from tools.hr_intelligence.analysis_units import (
    AcceptedAnalysis,
    AnalysisContractError,
    accept_unit_response,
    prepare_company_unit,
)
from tools.hr_intelligence.models import NormalizedJob

NOW = datetime(2026, 9, 6, 8, tzinfo=UTC)


def _accepted(company_key: str, inference: str) -> AcceptedAnalysis:
    job = NormalizedJob(
        job_id=uuid4(),
        source_id=uuid4(),
        company_key=company_key,
        public_job_key=f"{company_key}-job-1",
        title="点云算法工程师",
        location="上海",
        duty_excerpt="负责点云算法",
        requirement_excerpt="熟悉 C++",
        source_url=f"https://example.com/{company_key}/jobs/1",
        evidence_sha256=("a" if company_key == "hesai" else "b") * 64,
        observed_at=NOW,
    )
    unit = prepare_company_unit(
        uuid4(), company_key, (job,), {"directions": {"算法": 1}}
    )
    fact_id = f"F-{company_key}-1"
    inference_id = f"I-{company_key}-1"
    response = {
        "schema_version": 2,
        "facts": [{
            "fact_id": fact_id,
            "text": f"{company_key} 公开招聘点云算法工程师",
            "evidence_sha256": job.evidence_sha256,
            "source_url": job.source_url,
            "observed_at": NOW.isoformat(),
        }],
        "inferences": [{
            "inference_id": inference_id,
            "text": inference,
            "basis_fact_ids": [fact_id],
        }],
        "alternatives": [{
            "alternative_id": f"X-{company_key}-1",
            "text": "常年开放岗位可能放大当期需求",
            "basis_fact_ids": [fact_id],
            "challenged_inference_ids": [inference_id],
        }],
        "unknowns": ["实际 HC 未公开"],
        "recommendations": [{
            "recommendation_id": f"R-{company_key}-1",
            "text": f"跟踪 {company_key} 点云团队人才流动",
            "basis_fact_ids": [fact_id],
            "target_tasks": ["talent_profile", "sourcing_strategy"],
        }],
        "summary": f"{company_key} 公开招聘信号摘要",
        "confidence": "medium",
    }
    usage = {
        "unit_id": str(unit.unit_id),
        "provider": "openai",
        "model": "gpt-5.6-sol",
        "input_tokens": 100,
        "output_tokens": 100,
        "estimated_cost": str(Decimal("0.01")),
        "unavailable_reason": None,
    }
    return accept_unit_response(unit, response, usage)


def test_quality_report_counts_grounded_claims() -> None:
    analyses = (
        _accepted("hesai", "禾赛公开岗位显示点云算法能力需求"),
        _accepted("robosense", "速腾聚创公开岗位显示感知算法能力需求"),
    )

    report = validate_analysis_set(analyses)

    assert report.unit_count == 2
    assert report.factual_claim_count == 2
    assert report.inference_count == 2
    assert report.recommendation_count == 2
    assert report.duplicate_pairs == ()
    assert report.passed is True


def test_rejects_repeated_generic_inferences_across_companies() -> None:
    generic = "多个不同岗位样本共同出现，支持把该信号视为能力组合"
    analyses = (_accepted("hesai", generic), _accepted("robosense", generic))

    with pytest.raises(AnalysisContractError, match="generic duplicate"):
        validate_analysis_set(analyses)


def test_rejects_company_analysis_without_alternative() -> None:
    accepted = _accepted("hesai", "禾赛公开岗位显示点云算法能力需求")
    response = json.loads(accepted.response_json)
    response["alternatives"] = []
    modified = replace(
        accepted,
        response_json=json.dumps(response, ensure_ascii=False, sort_keys=True),
    )

    with pytest.raises(AnalysisContractError, match="alternative"):
        validate_analysis_set((modified,))
