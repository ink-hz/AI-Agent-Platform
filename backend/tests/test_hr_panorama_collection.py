from __future__ import annotations

import hashlib
import json
from uuid import uuid4

import httpx
import pytest

from tools.hr_intelligence.collectors import (
    CollectionError,
    PublicSourceCollector,
    SourceTarget,
)
from tools.hr_intelligence.evidence import EvidenceArchive

HTML = """<!doctype html><html><head><script type="application/ld+json">
{"@context":"https://schema.org","@type":"JobPosting","identifier":{"value":"structure-1"},
"title":"高级结构工程师","jobLocation":{"address":{"addressLocality":"深圳"}},
"description":"负责精密结构研发","qualifications":"五年以上量产经验"}
</script></head></html>"""


def target(url: str = "https://a.example/jobs") -> SourceTarget:
    return SourceTarget(
        source_id=uuid4(),
        company_name="A 公司",
        source_url=url,
        approved_urls=("https://a.example/jobs",),
    )


@pytest.mark.asyncio
async def test_collects_json_ld_jobs_and_archives_the_original_response(
    tmp_path,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=HTML, headers={"Content-Type": "text/html"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    archive = EvidenceArchive(tmp_path)
    collector = PublicSourceCollector(
        client,
        archive,
        destination_validator=lambda value: value,
    )

    result = await collector.collect(target())
    await client.aclose()

    assert [job.title for job in result.jobs] == ["高级结构工程师"]
    assert result.jobs[0].public_job_key == "structure-1"
    assert result.jobs[0].location == "深圳"
    assert result.content_sha256 == hashlib.sha256(HTML.encode()).hexdigest()
    assert archive.read(result.evidence.sha256) == HTML.encode()


@pytest.mark.asyncio
async def test_collects_a_json_jobs_feed(tmp_path) -> None:
    body = json.dumps(
        {
            "jobs": [
                {
                    "id": "algorithm-1",
                    "title": "感知算法工程师",
                    "location": "上海",
                    "description": "负责感知算法",
                    "requirements": "熟悉深度学习",
                    "status": "open",
                    "url": "https://a.example/jobs/algorithm-1",
                }
            ]
        },
        ensure_ascii=False,
    ).encode()

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=body, headers={"Content-Type": "application/json"}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await PublicSourceCollector(
        client,
        EvidenceArchive(tmp_path),
        destination_validator=lambda value: value,
    ).collect(target())
    await client.aclose()

    assert result.jobs[0].title == "感知算法工程师"
    assert result.jobs[0].source_url.endswith("algorithm-1")


@pytest.mark.asyncio
async def test_collects_complete_feishu_public_job_feed_and_preserves_raw_json(
    tmp_path,
) -> None:
    body = json.dumps(
        {
            "code": 0,
            "data": {
                "count": 1,
                "job_post_list": [
                    {
                        "id": "7680774393907939620",
                        "title": "高级光学工程师",
                        "description": "负责成像光学设计与验证",
                        "requirement": "熟悉 Zemax 和量产导入",
                        "city_list": [{"code": "CT_130", "name": "深圳"}],
                        "recruit_type": {
                            "id": "101",
                            "name": "全职",
                            "parent": {"id": "1", "name": "社招"},
                        },
                    }
                ],
            },
            "message": "ok",
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url == httpx.URL(
            "https://hesai.jobs.feishu.cn/api/v1/search/job/posts"
        )
        assert request.headers["website-path"] == "experienced"
        assert json.loads(request.content) == {
            "keyword": "",
            "limit": 1000,
            "offset": 0,
            "portal_type": 2,
            "job_category_id_list": [],
            "location_code_list": [],
            "subject_id_list": [],
            "recruitment_id_list": [],
            "job_function_id_list": [],
        }
        return httpx.Response(
            200, content=body, headers={"Content-Type": "application/json"}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    archive = EvidenceArchive(tmp_path)
    result = await PublicSourceCollector(
        client,
        archive,
        destination_validator=lambda value: value,
    ).collect(
        SourceTarget(
            source_id=uuid4(),
            company_name="禾赛科技",
            source_url="https://hesai.jobs.feishu.cn/experienced",
            approved_urls=("https://hesai.jobs.feishu.cn/experienced",),
        )
    )
    await client.aclose()

    assert archive.read(result.evidence.sha256) == body
    assert result.jobs[0].public_job_key == "7680774393907939620"
    assert result.jobs[0].location == "深圳"
    assert result.jobs[0].duty_excerpt == "负责成像光学设计与验证"
    assert result.jobs[0].requirement_excerpt == "熟悉 Zemax 和量产导入"
    assert result.jobs[0].source_url == (
        "https://hesai.jobs.feishu.cn/experienced/position/"
        "7680774393907939620/detail"
    )


@pytest.mark.asyncio
async def test_rejects_a_truncated_feishu_feed_after_archiving_the_response(
    tmp_path,
) -> None:
    body = json.dumps(
        {
            "code": 0,
            "data": {
                "count": 2,
                "job_post_list": [
                    {
                        "id": "job-1",
                        "title": "算法工程师",
                        "description": "负责算法",
                        "requirement": "熟悉 Python",
                        "city_list": [{"name": "上海"}],
                    }
                ],
            },
        },
        ensure_ascii=False,
    ).encode()

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=body, headers={"Content-Type": "application/json"}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    archive = EvidenceArchive(tmp_path)
    collector = PublicSourceCollector(
        client,
        archive,
        destination_validator=lambda value: value,
    )

    with pytest.raises(CollectionError, match="response_truncated") as raised:
        await collector.collect(
            SourceTarget(
                source_id=uuid4(),
                company_name="拓竹",
                source_url="https://bambulab.jobs.feishu.cn/campus",
                approved_urls=("https://bambulab.jobs.feishu.cn/campus",),
            )
        )
    await client.aclose()

    assert raised.value.evidence is not None
    assert archive.read(raised.value.evidence.sha256) == body


@pytest.mark.asyncio
async def test_collects_complete_beisen_public_job_feed_and_preserves_raw_json(
    tmp_path,
) -> None:
    body = json.dumps(
        {
            "Code": 200,
            "Count": 1,
            "Data": [
                {
                    "JobAdId": 230936163,
                    "JobAdName": "高级结构工程师(J10001)",
                    "LocNames": ["广东省·深圳市"],
                    "Category": "社会招聘",
                    "Duty": "负责整机结构与量产导入",
                    "Require": "熟悉注塑、钣金与公差分析",
                    "PostDate": "2026-09-02T11:19:40",
                    "Status": "招聘中",
                }
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url == httpx.URL(
            "https://creality.zhiye.com/api/Jobad/GetJobAdPageList"
        )
        assert json.loads(request.content)["Category"] == ["1"]
        assert json.loads(request.content)["PageSize"] == 1000
        return httpx.Response(
            200, content=body, headers={"Content-Type": "application/json"}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    archive = EvidenceArchive(tmp_path)
    result = await PublicSourceCollector(
        client,
        archive,
        destination_validator=lambda value: value,
    ).collect(
        SourceTarget(
            source_id=uuid4(),
            company_name="创想三维",
            source_url="https://creality.zhiye.com/social/jobs",
            approved_urls=("https://creality.zhiye.com/social/jobs",),
        )
    )
    await client.aclose()

    assert archive.read(result.evidence.sha256) == body
    assert result.jobs[0].public_job_key == "230936163"
    assert result.jobs[0].title == "高级结构工程师(J10001)"
    assert result.jobs[0].location == "广东省·深圳市"
    assert result.jobs[0].duty_excerpt == "负责整机结构与量产导入"
    assert result.jobs[0].requirement_excerpt == "熟悉注塑、钣金与公差分析"


@pytest.mark.asyncio
async def test_rejects_redirects_outside_the_approved_origin(tmp_path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "https://evil.example/jobs"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    collector = PublicSourceCollector(
        client,
        EvidenceArchive(tmp_path),
        destination_validator=lambda value: value,
    )
    with pytest.raises(CollectionError, match="redirect_not_approved"):
        await collector.collect(target())
    await client.aclose()


@pytest.mark.asyncio
async def test_rejects_oversized_responses_before_archiving(tmp_path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 129)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    collector = PublicSourceCollector(
        client,
        EvidenceArchive(tmp_path),
        maximum_response_bytes=128,
        destination_validator=lambda value: value,
    )
    with pytest.raises(CollectionError, match="response_too_large"):
        await collector.collect(target())
    await client.aclose()
    assert list(tmp_path.rglob("[0-9a-f]" * 64)) == []


@pytest.mark.asyncio
async def test_archives_a_received_response_even_when_its_schema_is_unsupported(
    tmp_path,
) -> None:
    body = b"<html><body>new portal schema</body></html>"

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"Content-Type": "text/html"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    archive = EvidenceArchive(tmp_path)
    collector = PublicSourceCollector(
        client,
        archive,
        destination_validator=lambda value: value,
    )

    with pytest.raises(CollectionError, match="unsupported_schema") as raised:
        await collector.collect(target())
    await client.aclose()

    assert raised.value.evidence is not None
    assert archive.read(raised.value.evidence.sha256) == body
