import importlib.util
from datetime import datetime, timezone
from uuid import uuid4

NOW = datetime(2026, 9, 6, 8, tzinfo=timezone.utc)


def _job(title: str, key: str, *, url: str, duty: str, requirement: str):
    from tools.hr_intelligence.models import NormalizedJob

    return NormalizedJob(
        job_id=uuid4(),
        source_id=uuid4(),
        company_key="robosense",
        public_job_key=key,
        title=title,
        location="深圳",
        duty_excerpt=duty,
        requirement_excerpt=requirement,
        source_url=url,
        evidence_sha256="b" * 64,
        observed_at=NOW,
    )


def test_dimensions_keep_track_direction_and_evidence_layers_separate() -> None:
    assert importlib.util.find_spec("tools.hr_intelligence.dimensions") is not None
    from tools.hr_intelligence.dimensions import compile_dimensions

    social = _job(
        "高级光学算法工程师",
        "social-1",
        url="https://example.com/social/jobs/1",
        duty="负责光学成像、点云算法和 Zemax 仿真",
        requirement="硕士，5 年以上，熟悉 C++ 和 Python",
    )
    campus = _job(
        "27届结构工程师",
        "campus-1",
        url="https://example.com/campus/jobs/1",
        duty="负责机械结构和量产导入",
        requirement="本科应届生，熟悉 CAD",
    )

    dimensions = compile_dimensions((social, campus))

    assert dimensions["tracks"] == {
        "social": 1,
        "campus": 1,
        "intern": 0,
        "unknown": 0,
    }
    assert dimensions["directions"]["光学"] == 1
    assert dimensions["directions"]["算法"] == 1
    assert dimensions["directions"]["结构"] == 1
    assert dimensions["evidence_samples"]["directions"]["光学"] == [
        str(social.job_id)
    ]
    assert dimensions["trend"]["state"] == "baseline_only"
