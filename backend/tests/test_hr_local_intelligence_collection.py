import importlib.util
from datetime import datetime, timezone
from uuid import uuid4

import pytest

NOW = datetime(2026, 9, 6, 8, tzinfo=timezone.utc)


def _result():
    from tools.hr_intelligence.collectors import (
        CollectionResult,
        NormalizedPublicJob,
        SourceTarget,
    )
    from tools.hr_intelligence.evidence import EvidenceRecord

    source_id = uuid4()
    target = SourceTarget(
        source_id,
        "禾赛科技",
        "https://example.com/jobs",
        ("https://example.com/jobs",),
    )
    evidence = EvidenceRecord(
        sha256="a" * 64,
        locator=f"sha256/aa/{'a' * 64}",
        mime="application/json",
        size_bytes=42,
        metadata_json=(
            '{"mime":"application/json","response_headers":{},'
            f'"sha256":"{"a" * 64}","size_bytes":42,'
            '"source_url":"https://example.com/jobs"}'
        ),
    )
    return CollectionResult(
        target,
        (
            NormalizedPublicJob(
                "algorithm-1",
                "高级算法工程师",
                "上海",
                "负责点云算法研发",
                "硕士，5 年以上，熟悉 Python",
                "https://example.com/jobs/algorithm-1",
            ),
        ),
        evidence,
        NOW,
    )


def test_normalized_job_requires_source_evidence() -> None:
    assert importlib.util.find_spec("tools.hr_intelligence.models") is not None
    from tools.hr_intelligence.models import NormalizedJob

    source_id = uuid4()
    with pytest.raises(ValueError, match="evidence"):
        NormalizedJob(
            job_id=uuid4(),
            source_id=source_id,
            company_key="hesai",
            public_job_key="algorithm-1",
            title="算法工程师",
            location="上海",
            duty_excerpt="负责点云算法",
            requirement_excerpt="熟悉 Python",
            source_url="https://example.com/jobs/algorithm-1",
            evidence_sha256="",
            observed_at=NOW,
        )


def test_normalization_is_stable_and_preserves_provenance() -> None:
    from tools.hr_intelligence.normalize import normalize_jobs

    result = _result()

    first = normalize_jobs(result, company_key="hesai")
    second = normalize_jobs(result, company_key="hesai")

    assert first == second
    assert first[0].source_id == result.target.source_id
    assert first[0].evidence_sha256 == result.evidence.sha256
    assert first[0].source_url == "https://example.com/jobs/algorithm-1"
    assert first[0].observed_at == NOW


def test_verified_campus_board_embedded_jobs_are_parsed_with_full_details() -> None:
    from tools.hr_intelligence.collectors import SourceTarget, parse_public_jobs

    source_url = "https://example.bysjy.com.cn/detail/career?id=708375"
    body = br'''<html><script>
    var data = JSON.parse(JSON.stringify({"data":{"jobs":[{
      "publish_id":3227283,"job_name":"\u6a21\u5177\u7f16\u7a0b\u5de5\u7a0b\u5e08",
      "city_name":"\u4e2d\u5c71\u5e02",
      "job_descript":"<p>\u8d1f\u8d23\u6ce8\u5851\u6a21\u5177\u6570\u63a7\u94e3\u7a0b\u5e8f\u7f16\u5236</p>",
      "job_require":"<p>\u672c\u79d1\uff0c\u6a21\u5177\u8bbe\u8ba1\u76f8\u5173\u4e13\u4e1a</p>"
    }]}}));
    </script></html>'''
    target = SourceTarget(uuid4(), "联合光电", source_url, (source_url,))

    jobs = parse_public_jobs(body, "text/html; charset=utf-8", target)

    assert len(jobs) == 1
    assert jobs[0].public_job_key == "3227283"
    assert jobs[0].title == "模具编程工程师"
    assert jobs[0].location == "中山市"
    assert jobs[0].duty_excerpt == "负责注塑模具数控铣程序编制"
    assert jobs[0].requirement_excerpt == "本科，模具设计相关专业"
