from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from tools.hr_intelligence.models import NormalizedJob
from tools.hr_intelligence.taxonomy import (
    normalize_location,
    secondary_directions,
)


NOW = datetime(2026, 9, 6, 8, tzinfo=UTC)


def _job(**changes) -> NormalizedJob:
    values = {
        "job_id": uuid4(),
        "source_id": uuid4(),
        "company_key": "hesai",
        "public_job_key": "point-cloud-1",
        "title": "算法工程师",
        "location": "深圳",
        "duty_excerpt": "负责算法开发",
        "requirement_excerpt": "本科，熟悉 C++",
        "source_url": "https://example.com/jobs/1",
        "evidence_sha256": "a" * 64,
        "observed_at": NOW,
    }
    return NormalizedJob(**(values | changes))


@pytest.mark.parametrize(
    ("raw", "normalized", "parts", "valid"),
    [
        ("广东省·深圳市", "深圳", ("深圳",), True),
        ("深圳市/东莞市", "深圳、东莞", ("深圳", "东莞"), True),
        ("上海市·浦东新区", "上海", ("上海",), True),
        ("Central Singapore·Singapore", "新加坡", ("新加坡",), True),
        ("北揽", "未规范", (), False),
    ],
)
def test_normalize_location_preserves_raw_without_inventing_a_city(
    raw: str, normalized: str, parts: tuple[str, ...], valid: bool
) -> None:
    result = normalize_location(raw)

    assert (result.raw, result.normalized, result.parts, result.valid) == (
        raw,
        normalized,
        parts,
        valid,
    )


def test_secondary_taxonomy_distinguishes_point_cloud_and_slam() -> None:
    job = replace(
        _job(),
        title="点云 SLAM 算法工程师",
        duty_excerpt="负责激光雷达定位、建图和点云配准",
    )

    assert secondary_directions(job) == ("算法/点云", "算法/SLAM")


def test_secondary_taxonomy_keeps_optical_hardware_and_quality_multilabel() -> None:
    job = replace(
        _job(),
        title="光学硬件 DQE 工程师",
        duty_excerpt="负责镜头、PCB、可靠性和失效分析",
    )

    assert secondary_directions(job) == (
        "光学/镜头",
        "硬件/PCB",
        "质量/DQE",
        "质量/可靠性",
        "质量/失效分析",
    )


def test_normalized_job_keeps_original_location_for_audit() -> None:
    job = _job(location="深圳", raw_location="广东省·深圳市")

    assert job.location == "深圳"
    assert job.raw_location == "广东省·深圳市"
