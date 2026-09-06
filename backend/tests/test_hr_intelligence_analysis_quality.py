from __future__ import annotations

import hashlib
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
from tools.hr_intelligence.public_documents import PublicIntelligenceDocument

NOW = datetime(2026, 9, 6, 8, tzinfo=UTC)


def _accepted(
    company_key: str,
    inference: str,
    *,
    claim_type: str = "recruiting_signal",
    confidence: str = "medium",
    include_primary_document: bool = False,
) -> AcceptedAnalysis:
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
    documents = ()
    if include_primary_document:
        excerpt = f"{company_key} lidar product portfolio"
        documents = (
            PublicIntelligenceDocument(
                document_id=uuid4(),
                company_key=company_key,
                source_type="official_product",
                source_url=(
                    "https://www.hesaitech.com"
                    if company_key == "hesai"
                    else "https://www.robosense.ai"
                ),
                title=f"{company_key} products",
                text_excerpt=excerpt,
                evidence_sha256="c" * 64,
                text_sha256=hashlib.sha256(excerpt.encode()).hexdigest(),
                observed_at=NOW,
                trust_tier="primary",
            ),
        )
    unit = prepare_company_unit(
        uuid4(),
        company_key,
        (job,),
        {"directions": {"算法": 1}},
        public_documents=documents,
    )
    fact_id = f"F-{company_key}-1"
    inference_id = f"I-{company_key}-1"
    facts = [{
        "fact_id": fact_id,
        "text": f"{company_key} 公开招聘点云算法工程师",
        "evidence_sha256": job.evidence_sha256,
        "source_url": job.source_url,
        "observed_at": NOW.isoformat(),
    }]
    basis_fact_ids = [fact_id]
    if documents:
        document_fact_id = f"F-{company_key}-document-1"
        facts.append({
            "fact_id": document_fact_id,
            "text": f"{company_key} 官网展示激光雷达产品组合",
            "evidence_sha256": documents[0].evidence_sha256,
            "source_url": documents[0].source_url,
            "observed_at": NOW.isoformat(),
        })
        basis_fact_ids.append(document_fact_id)
    response = {
        "schema_version": 2,
        "facts": facts,
        "inferences": [{
            "inference_id": inference_id,
            "claim_type": claim_type,
            "text": inference,
            "basis_fact_ids": basis_fact_ids,
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
        "confidence": confidence,
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


def test_high_confidence_product_route_requires_recruiting_and_primary_source() -> None:
    analysis = _accepted(
        "hesai",
        "禾赛正在强化车载激光雷达产品路线",
        claim_type="product_route",
        confidence="high",
    )

    with pytest.raises(AnalysisContractError, match="cross-source"):
        validate_analysis_set((analysis,))


def test_high_confidence_product_route_accepts_cross_source_grounding() -> None:
    analysis = _accepted(
        "hesai",
        "禾赛招聘与官网产品证据共同支持车载激光雷达路线判断",
        claim_type="product_route",
        confidence="high",
        include_primary_document=True,
    )

    assert validate_analysis_set((analysis,)).passed is True
