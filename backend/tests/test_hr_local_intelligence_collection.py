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
