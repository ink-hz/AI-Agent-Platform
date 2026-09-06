import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from tools.hr_intelligence.analysis_units import (
    AnalysisContractError,
    accept_unit_response,
    analysis_cache_hit,
    load_accepted,
    prepare_company_unit,
    prepare_units,
    save_accepted,
)
from tools.hr_intelligence.models import NormalizedJob

NOW = datetime(2026, 9, 6, 8, tzinfo=timezone.utc)


def _job() -> NormalizedJob:
    return NormalizedJob(
        job_id=uuid4(),
        source_id=uuid4(),
        company_key="hesai",
        public_job_key="algorithm-1",
        title="高级点云算法工程师",
        location="上海",
        duty_excerpt="负责激光雷达点云算法",
        requirement_excerpt="硕士，熟悉 C++ 和 Python",
        source_url="https://example.com/jobs/algorithm-1",
        evidence_sha256="a" * 64,
        observed_at=NOW,
    )


def _unit():
    selected = _job()
    return prepare_company_unit(
        uuid4(),
        "hesai",
        (selected,),
        {"tracks": {"social": 1}},
    )


def _response(unit):
    evidence = unit.evidence[0]
    return {
        "schema_version": 2,
        "facts": [
            {
                "fact_id": "fact-1",
                "text": "公开招聘高级点云算法工程师",
                "evidence_sha256": evidence.sha256,
                "source_url": evidence.source_url,
                "observed_at": evidence.observed_at.isoformat(),
            }
        ],
        "inferences": [
            {
                "inference_id": "inference-1",
                "claim_type": "recruiting_signal",
                "text": "点云算法是当前公开人才需求信号之一",
                "basis_fact_ids": ["fact-1"],
            }
        ],
        "unknowns": ["实际 HC、预算与录用数量未公开"],
        "alternatives": [{
            "alternative_id": "alternative-1",
            "text": "单个公开岗位也可能是常规补员",
            "basis_fact_ids": ["fact-1"],
            "challenged_inference_ids": ["inference-1"],
        }],
        "recommendations": [{
            "recommendation_id": "recommendation-1",
            "text": "建立点云算法人才池",
            "basis_fact_ids": ["fact-1"],
            "target_tasks": ["talent_profile", "sourcing_strategy"],
        }],
        "summary": "当前公开信号集中于点云算法能力。",
        "confidence": "medium",
    }


def _usage(unit):
    return {
        "unit_id": str(unit.unit_id),
        "provider": "openai",
        "model": "gpt-5.6-sol",
        "input_tokens": 1200,
        "output_tokens": 450,
        "estimated_cost": str(Decimal("0.0123")),
        "unavailable_reason": None,
    }


def test_completed_unit_reuses_only_the_same_input_hash(tmp_path) -> None:
    unit = _unit()
    accepted = accept_unit_response(unit, _response(unit), _usage(unit))
    save_accepted(tmp_path, accepted)

    assert analysis_cache_hit(tmp_path, unit) is True
    assert load_accepted(tmp_path, unit) == accepted
    assert (
        analysis_cache_hit(
            tmp_path,
            replace(unit, input_sha256="b" * 64),
        )
        is False
    )


def test_inference_requires_an_existing_basis_fact() -> None:
    unit = _unit()
    response = _response(unit)
    response["inferences"][0]["basis_fact_ids"] = ["missing"]

    with pytest.raises(AnalysisContractError, match="basis"):
        accept_unit_response(unit, response, _usage(unit))


def test_fact_requires_evidence_present_in_the_immutable_request() -> None:
    unit = _unit()
    response = _response(unit)
    response["facts"][0]["evidence_sha256"] = "f" * 64

    with pytest.raises(AnalysisContractError, match="evidence"):
        accept_unit_response(unit, response, _usage(unit))


def test_unavailable_usage_is_explicit_instead_of_fabricated() -> None:
    unit = _unit()
    usage = _usage(unit) | {
        "input_tokens": "unavailable",
        "output_tokens": "unavailable",
        "estimated_cost": "unavailable",
        "unavailable_reason": "Codex session has no per-call usage telemetry",
    }

    accepted = accept_unit_response(unit, _response(unit), usage)

    assert accepted.usage.input_tokens == "unavailable"
    assert accepted.usage.unavailable_reason == (
        "Codex session has no per-call usage telemetry"
    )


def test_unit_identity_and_request_bytes_are_deterministic() -> None:
    bundle_id = uuid4()
    selected = _job()
    first = prepare_company_unit(
        bundle_id,
        "hesai",
        (selected,),
        {"tracks": {"social": 1}},
    )
    second = prepare_company_unit(
        bundle_id,
        "hesai",
        (selected,),
        {"tracks": {"social": 1}},
    )

    assert first == second
    assert first.canonical_request_bytes() == second.canonical_request_bytes()


def test_requested_analysis_layers_are_prepared_in_stable_order() -> None:
    selected = replace(
        _job(),
        source_url="https://example.com/social/jobs/algorithm-1",
    )

    units = prepare_units(
        uuid4(),
        (selected,),
        {
            "tracks": {"social": 1},
            "directions": {"算法": 1},
        },
        kinds=("company", "track", "direction", "comparison", "executive-summary"),
    )

    assert [(unit.kind, unit.scope_key) for unit in units] == [
        ("company", "hesai"),
        ("track", "social"),
        ("direction", "算法"),
        ("direction", "软件"),
        ("comparison", "all-companies"),
        ("executive-summary", "all-companies"),
    ]


def test_company_units_include_catalog_company_without_normalized_jobs() -> None:
    units = prepare_units(
        uuid4(),
        (_job(),),
        {"tracks": {"social": 1}},
        kinds=("company",),
        company_keys=("hesai", "scantech"),
    )

    assert [(unit.scope_key, len(unit.evidence)) for unit in units] == [
        ("hesai", 1),
        ("scantech", 0),
    ]


def test_v2_analysis_requires_grounded_alternative_and_task_recommendation() -> None:
    unit = _unit()

    accepted = accept_unit_response(unit, _response(unit), _usage(unit))

    assert accepted.response_sha256


def test_default_analysis_layers_include_topics_and_five_task_playbooks() -> None:
    selected = replace(
        _job(),
        source_url="https://example.com/social/jobs/algorithm-1",
        duty_excerpt="负责激光雷达点云 SLAM 算法",
    )

    units = prepare_units(
        uuid4(),
        (selected,),
        {"tracks": {"social": 1}, "directions": {"算法": 1}},
    )

    assert [(unit.kind, unit.scope_key) for unit in units] == [
        ("company", "hesai"),
        ("track", "social"),
        ("direction", "算法"),
        ("direction", "软件"),
        ("secondary-direction", "算法/点云"),
        ("secondary-direction", "算法/SLAM"),
        ("topic", "product-routes"),
        ("topic", "talent-competition"),
        ("topic", "geography"),
        ("topic", "trends"),
        ("executive-summary", "all-companies"),
        ("task", "jd-jr"),
        ("task", "talent-profile"),
        ("task", "sourcing"),
        ("task", "resume-review"),
        ("task", "interview"),
    ]


def test_v2_request_demands_company_specific_contrary_and_orbbec_analysis() -> None:
    request = json.loads(_unit().request_json)

    assert request["schema_version"] == 2
    assert request["instructions"]["company_specific_signals"] is True
    assert request["instructions"]["orbbec_implications"] is True
    assert request["instructions"]["contrary_evidence_and_uncertainty"] is True


def test_accepted_v1_analysis_remains_readable(tmp_path) -> None:
    unit = _unit()
    request = json.loads(unit.request_json)
    request["schema_version"] = 1
    request_json = json.dumps(
        request,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    legacy_unit = replace(
        unit,
        request_json=request_json,
        input_sha256=hashlib.sha256(request_json.encode("utf-8")).hexdigest(),
    )
    evidence = legacy_unit.evidence[0]
    response = {
        "facts": [{
            "fact_id": "fact-1",
            "text": "公开招聘高级点云算法工程师",
            "evidence_sha256": evidence.sha256,
            "source_url": evidence.source_url,
            "observed_at": evidence.observed_at.isoformat(),
        }],
        "inferences": [{
            "text": "点云算法是公开人才需求信号之一",
            "basis_fact_ids": ["fact-1"],
        }],
        "unknowns": ["实际 HC 未公开"],
        "alternatives": ["可能是常规补员"],
        "summary": "历史 v1 分析",
        "confidence": "medium",
    }

    accepted = accept_unit_response(legacy_unit, response, _usage(legacy_unit))
    path = save_accepted(tmp_path, accepted)
    saved = json.loads(path.read_text("utf-8"))
    saved["evidence"] = [
        {
            "job_id": item["job_id"],
            "sha256": item["sha256"],
            "source_url": item["source_url"],
            "observed_at": item["observed_at"],
        }
        for item in saved["evidence"]
    ]
    path.write_text(
        json.dumps(
            saved,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )

    assert load_accepted(tmp_path, legacy_unit).response_sha256
