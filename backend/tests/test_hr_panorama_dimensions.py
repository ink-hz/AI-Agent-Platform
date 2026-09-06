from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

from app.hr.panorama_models import PublicJobSnapshot
from tools.hr_intelligence.dimensions import (
    compile_panorama_dimensions,
    recruitment_track,
    technical_directions,
)

NOW = datetime(2026, 9, 6, 8, tzinfo=timezone.utc)


def _job(
    title: str,
    *,
    key: str,
    source_id=None,
    url: str = "https://company.example/social/jobs",
    location: str = "深圳",
    duty: str = "负责产品研发",
    requirement: str = "本科，熟悉 Python",
) -> PublicJobSnapshot:
    return PublicJobSnapshot(
        uuid4(), uuid4(), uuid4(), None, source_id or uuid4(), key, title,
        location, duty, requirement, url, NOW, "a" * 64, "open", NOW,
        uuid4(), uuid4(),
    )


def test_dimensions_deduplicate_and_classify_recruiting_layers() -> None:
    company = uuid4()
    senior = _job(
        "高级光学算法工程师",
        key="job-1",
        source_id=company,
        duty="负责成像算法、镜头标定和 Zemax 仿真",
        requirement="硕士，5年以上经验，熟悉 C++、Python",
    )
    duplicate = replace(senior, snapshot_id=uuid4(), observation_id=uuid4())
    campus = _job(
        "27届结构工程师",
        key="job-2",
        source_id=company,
        url="https://company.example/campus/jobs",
        location="东莞",
        duty="负责机械结构和量产导入",
        requirement="本科应届生，熟悉 CAD",
    )

    dimensions = compile_panorama_dimensions((senior, duplicate, campus))

    assert dimensions["scope"]["snapshot_count"] == 3
    assert dimensions["scope"]["unique_job_count"] == 2
    assert dimensions["scope"]["duplicate_snapshot_count"] == 1
    assert dimensions["tracks"] == {
        "social": 1,
        "campus": 1,
        "intern": 0,
        "unknown": 0,
    }
    assert dimensions["seniority"]["senior"] == 1
    assert dimensions["education"]["master"] == 1
    assert dimensions["directions"]["光学"] == 1
    assert dimensions["directions"]["算法"] == 1
    assert dimensions["directions"]["结构"] == 1
    assert dimensions["locations"] == {"深圳": 1, "东莞": 1}
    assert {item["name"] for item in dimensions["skills"]} >= {
        "Zemax",
        "C++",
        "Python",
        "CAD",
    }
    assert dimensions["trend"]["state"] == "baseline_only"
    assert dimensions["company_matrix"][str(company)]["job_count"] == 2


def test_soc_does_not_match_social_and_unproven_trend_stays_baseline() -> None:
    job = _job(
        "SoC工程师",
        key="soc-1",
        url="https://company.example/jobs/soc-engineer",
        duty="负责芯片验证",
        requirement="熟悉 Verilog",
    )

    dimensions = compile_panorama_dimensions((job,))

    assert dimensions["tracks"]["unknown"] == 1
    assert dimensions["trend"] == {
        "state": "baseline_only",
        "message": "基线版本：尚不能判断月度变化",
    }


def test_multi_label_directions_keep_bounded_snapshot_evidence() -> None:
    job = _job(
        "硬件测试工程师",
        key="hw-1",
        duty="负责 PCB、EMC、可靠性和 DQE 质量验证",
    )

    dimensions = compile_panorama_dimensions((job,))

    assert dimensions["directions"]["硬件"] == 1
    assert dimensions["directions"]["质量"] == 1
    assert dimensions["evidence_samples"]["directions"]["硬件"] == [
        str(job.snapshot_id)
    ]


def test_job_body_does_not_turn_software_role_into_unrelated_resource_directions() -> None:
    job = _job(
        "软件开发工程师",
        key="software-1",
        duty="负责功能开发、完成测试并配合量产交付",
        requirement="本科，熟悉 Python",
    )

    dimensions = compile_panorama_dimensions((job,))

    assert dimensions["directions"]["软件"] == 1
    assert dimensions["directions"]["质量"] == 0
    assert dimensions["directions"]["制造工艺"] == 0
    assert dimensions["directions"]["供应链"] == 0
    assert dimensions["job_families"]["research_development"] == 1
    assert dimensions["job_families"]["quality"] == 0


def test_strong_duty_signal_adds_a_second_technical_direction() -> None:
    job = _job(
        "硬件工程师",
        key="hardware-optics-1",
        duty="负责光学系统设计和硬件电路开发",
    )

    dimensions = compile_panorama_dimensions((job,))

    assert dimensions["directions"]["硬件"] == 1
    assert dimensions["directions"]["光学"] == 1


def test_recruitment_portal_path_overrides_incidental_track_words_in_body() -> None:
    campus = _job(
        "27届算法工程师",
        key="campus-1",
        url="https://company.example/campus/jobs",
        requirement="有实习经历优先",
    )
    social = _job(
        "算法工程师",
        key="social-1",
        url="https://company.example/social/jobs",
        requirement="有实习经验优先",
    )

    dimensions = compile_panorama_dimensions((campus, social))

    assert dimensions["tracks"] == {
        "social": 1,
        "campus": 1,
        "intern": 0,
        "unknown": 0,
    }
    assert recruitment_track(campus) == "campus"
    assert recruitment_track(social) == "social"


def test_local_direction_uses_the_same_title_first_classification() -> None:
    job = _job(
        "软件开发工程师",
        key="software-export-1",
        duty="完成测试并配合量产交付",
        requirement="熟悉 Python",
    )

    assert technical_directions(job)[0] == "软件"


def test_verified_portal_paths_supply_track_when_titles_are_unmarked() -> None:
    jobs = tuple(
        _job("研发工程师", key=f"portal-{index}", url=url)
        for index, url in enumerate(
            (
                "https://kwh0jtf778.jobs.feishu.cn/index/position/1/detail",
                "https://kwh0jtf778.jobs.feishu.cn/229043/position/2/detail",
                "https://kwh0jtf778.jobs.feishu.cn/073183/position/3/detail",
                "https://agirobot.jobs.feishu.cn/campusrecruitment/position/4/detail",
                "https://arashivision.jobs.feishu.cn/socialENG/position/5/detail",
                "https://shining3d.zhiye.com/",
                "https://www.elegoo.com.cn/index/join/index.html",
            )
        )
    )

    dimensions = compile_panorama_dimensions(jobs)

    assert dimensions["tracks"] == {
        "social": 4,
        "campus": 2,
        "intern": 1,
        "unknown": 0,
    }


def test_elegoo_detail_urls_are_social_recruiting() -> None:
    job = _job(
        "工程师",
        key="elegoo-detail",
        url="https://www.elegoo.com.cn/index/join/detail/id/15.html",
    )

    assert recruitment_track(job) == "social"


def test_chinese_years_and_minimum_degree_do_not_overstate_requirements() -> None:
    job = _job(
        "结构工程师",
        key="seniority-cn-1",
        requirement="硕士及以上学历，博士优先，五年以上结构设计经验",
    )

    dimensions = compile_panorama_dimensions((job,))

    assert dimensions["seniority"]["senior"] == 1
    assert dimensions["education"]["master"] == 1
    assert dimensions["education"]["doctorate"] == 0


def test_dimensions_are_identical_when_snapshot_input_order_changes() -> None:
    company = uuid4()
    snapshots = tuple(
        _job(
            title,
            key=key,
            source_id=company,
            location=location,
            requirement=requirement,
        )
        for title, key, location, requirement in (
            ("算法工程师", "algorithm", "深圳", "熟悉 Python"),
            ("软件工程师", "software", "上海", "熟悉 C++"),
            ("光学工程师", "optics", "东莞", "熟悉 Zemax"),
        )
    )

    assert compile_panorama_dimensions(snapshots) == compile_panorama_dimensions(
        tuple(reversed(snapshots))
    )
