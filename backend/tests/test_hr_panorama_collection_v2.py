from __future__ import annotations

import json
from uuid import uuid4

import httpx
import pytest
from app.hr.panorama_collection import (
    CollectionError,
    PublicSourceCollector,
    SourceTarget,
)
from app.hr.panorama_evidence import EvidenceArchive


def _target(company: str, url: str, *approved: str) -> SourceTarget:
    return SourceTarget(uuid4(), company, url, approved or (url,))


@pytest.mark.asyncio
async def test_moka_adapter_collects_every_page_and_splits_job_description(tmp_path) -> None:
    calls: list[int] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.mokahr.com"
        offset = int(request.url.params["offset"])
        calls.append(offset)
        job_id = "system-1" if offset == 0 else "algorithm-2"
        return httpx.Response(
            200,
            json={
                "code": 0,
                "total": 2,
                "jobs": [
                    {
                        "id": job_id,
                        "title": "激光雷达系统工程师",
                        "status": "open",
                        "description": "工作职责：负责标定系统。任职要求：熟悉 Python。",
                        "locations": [
                            {
                                "province": "广东",
                                "city": "深圳市",
                                "area": "南山区",
                                "address": "众冠时代广场",
                            }
                        ],
                    }
                ],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    archive = EvidenceArchive(tmp_path)
    result = await PublicSourceCollector(
        client, archive, destination_validator=lambda value: value
    ).collect(
        _target(
            "速腾聚创",
            "https://app.mokahr.com/social-recruitment/robosense/77883",
        )
    )
    await client.aclose()

    assert calls == [0, 1]
    assert [job.public_job_key for job in result.jobs] == ["system-1", "algorithm-2"]
    assert result.jobs[0].location == "广东·深圳市·南山区"
    assert result.jobs[0].duty_excerpt == "负责标定系统。"
    assert result.jobs[0].requirement_excerpt == "熟悉 Python。"
    evidence = json.loads(archive.read(result.evidence.sha256))
    assert len(evidence["_evidence_pages"]) == 2


@pytest.mark.asyncio
async def test_moka_adapter_treats_verified_zero_jobs_as_success(tmp_path) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 0, "total": 0, "jobs": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await PublicSourceCollector(
        client, EvidenceArchive(tmp_path), destination_validator=lambda value: value
    ).collect(
        _target(
            "思看科技",
            "https://app135149.eapps.dingtalkcloud.com/social-recruitment/"
            "sikankeji/100000204",
        )
    )
    await client.aclose()

    assert result.jobs == ()


@pytest.mark.asyncio
async def test_derived_response_stops_streaming_once_size_limit_is_exceeded(
    tmp_path,
) -> None:
    chunks_read = 0

    class OversizedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            nonlocal chunks_read
            for chunk in (b"123456", b"abcdef", b"must-not-be-read"):
                chunks_read += 1
                yield chunk

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=OversizedStream(),
            headers={"content-type": "application/json"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    collector = PublicSourceCollector(
        client,
        EvidenceArchive(tmp_path),
        maximum_response_bytes=10,
        destination_validator=lambda value: value,
    )

    with pytest.raises(CollectionError, match="response_too_large"):
        await collector.collect(
            _target(
                "速腾聚创",
                "https://app.mokahr.com/social-recruitment/robosense/77883",
            )
        )
    await client.aclose()

    assert chunks_read == 2


@pytest.mark.asyncio
async def test_moka_adapter_rejects_overlapping_pages(tmp_path) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "code": 0,
                "total": 2,
                "jobs": [
                    {
                        "id": "repeated-job",
                        "title": "算法工程师",
                        "description": "负责算法开发",
                    }
                ],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    collector = PublicSourceCollector(
        client, EvidenceArchive(tmp_path), destination_validator=lambda value: value
    )

    with pytest.raises(
        CollectionError, match="source_changed_during_collection"
    ) as captured:
        await collector.collect(
            _target(
                "速腾聚创",
                "https://app.mokahr.com/social-recruitment/robosense/77883",
            )
        )
    await client.aclose()

    assert captured.value.retryable is True


@pytest.mark.asyncio
async def test_elegoo_adapter_collects_listed_job_details(tmp_path) -> None:
    listing = """
    <div class="jrlbb"><a href="/index/join/detail/id/15.html"><h2>硬件工程师</h2></a></div>
    <a href="/index/join/detail/id/16.html"><div class="jrlbb"><h2>结构工程师</h2></div></a>
    """.encode()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("index.html"):
            return httpx.Response(200, content=listing, headers={"content-type": "text/html"})
        job_id = request.url.path.split("/")[-1].split(".")[0]
        title = "硬件工程师" if job_id == "15" else "结构工程师"
        return httpx.Response(
            200,
            text=(
                f'<div class="xqtitle"><h2>{title}</h2><span>薪资范围：15-20K</span></div>'
                '<div class="zpxq"><p>岗位职责</p><p>负责产品研发。</p>'
                '<p>任职要求</p><p>本科以上，3 年经验。</p></div>'
            ),
            headers={"content-type": "text/html"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await PublicSourceCollector(
        client, EvidenceArchive(tmp_path), destination_validator=lambda value: value
    ).collect(
        _target(
            "智能派",
            "https://www.elegoo.com.cn/index/join/index.html",
            "https://www.elegoo.com.cn/index/join",
        )
    )
    await client.aclose()

    assert [job.title for job in result.jobs] == ["硬件工程师", "结构工程师"]
    assert result.jobs[0].duty_excerpt == "负责产品研发。"
    assert "3 年经验" in result.jobs[0].requirement_excerpt


@pytest.mark.asyncio
async def test_elegoo_adapter_never_follows_a_cross_origin_detail_link(tmp_path) -> None:
    listing = (
        '<a href="https://attacker.example/index/join/detail/id/15.html">'
        '<div class="jrlbb"><h2>伪造岗位</h2></div></a>'
    )
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        assert request.url.host == "www.elegoo.com.cn"
        return httpx.Response(200, text=listing, headers={"content-type": "text/html"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    collector = PublicSourceCollector(
        client, EvidenceArchive(tmp_path), destination_validator=lambda value: value
    )

    with pytest.raises(CollectionError, match="unsupported_schema") as captured:
        await collector.collect(
            _target(
                "智能派",
                "https://www.elegoo.com.cn/index/join/index.html",
            )
        )
    await client.aclose()

    assert calls == ["www.elegoo.com.cn"]
    assert captured.value.evidence is not None
    archived = json.loads(EvidenceArchive(tmp_path).read(captured.value.evidence.sha256))
    assert "attacker.example" in archived["_evidence_pages"][0]


@pytest.mark.asyncio
async def test_elegoo_adapter_ignores_commented_job_links(tmp_path) -> None:
    listing = """
    <!-- <a href="/index/join/detail/id/15.html">已下线岗位</a> -->
    <a href="/index/join/detail/id/16.html">当前岗位</a>
    """
    detail_calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("index.html"):
            return httpx.Response(200, text=listing)
        detail_calls.append(request.url.path)
        return httpx.Response(
            200,
            text=(
                '<div class="xqtitle"><h2>结构工程师</h2></div>'
                '<div class="zpxq">岗位职责：负责结构研发。</div>'
            ),
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await PublicSourceCollector(
        client, EvidenceArchive(tmp_path), destination_validator=lambda value: value
    ).collect(
        _target("智能派", "https://www.elegoo.com.cn/index/join/index.html")
    )
    await client.aclose()

    assert detail_calls == ["/index/join/detail/id/16.html"]
    assert [job.public_job_key for job in result.jobs] == ["16"]


@pytest.mark.asyncio
async def test_elegoo_adapter_rejects_listing_beyond_detail_limit(tmp_path) -> None:
    listing = "".join(
        f'<a href="/index/join/detail/id/{job_id}.html">岗位</a>'
        for job_id in range(1, 102)
    )
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, text=listing)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    collector = PublicSourceCollector(
        client, EvidenceArchive(tmp_path), destination_validator=lambda value: value
    )

    with pytest.raises(CollectionError, match="response_truncated"):
        await collector.collect(
            _target("智能派", "https://www.elegoo.com.cn/index/join/index.html")
        )
    await client.aclose()

    assert calls == ["/index/join/index.html"]


@pytest.mark.asyncio
async def test_elegoo_adapter_bounds_aggregate_evidence_before_all_details_load(
    tmp_path,
) -> None:
    listing = "".join(
        f'<a href="/index/join/detail/id/{job_id}.html">岗位</a>'
        for job_id in range(1, 4)
    )
    detail_calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("index.html"):
            return httpx.Response(200, text=listing)
        detail_calls.append(request.url.path)
        return httpx.Response(
            200,
            text=(
                '<div class="xqtitle"><h2>结构工程师</h2></div>'
                f'<div class="zpxq">岗位职责：{"负责结构设计" * 10}</div>'
            ),
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    collector = PublicSourceCollector(
        client,
        EvidenceArchive(tmp_path),
        maximum_response_bytes=400,
        destination_validator=lambda value: value,
    )

    with pytest.raises(CollectionError, match="response_too_large"):
        await collector.collect(
            _target("智能派", "https://www.elegoo.com.cn/index/join/index.html")
        )
    await client.aclose()

    assert len(detail_calls) < 3


@pytest.mark.asyncio
async def test_elegoo_adapter_rejects_partial_success_when_a_detail_schema_changes(
    tmp_path,
) -> None:
    listing = (
        '<a href="/index/join/detail/id/15.html">岗位一</a>'
        '<a href="/index/join/detail/id/16.html">岗位二</a>'
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("index.html"):
            return httpx.Response(200, text=listing)
        if request.url.path.endswith("15.html"):
            return httpx.Response(
                200,
                text=(
                    '<div class="xqtitle"><h2>结构工程师</h2></div>'
                    '<div class="zpxq">岗位职责：负责结构设计</div>'
                ),
            )
        return httpx.Response(200, text="<main>新版岗位详情</main>")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    collector = PublicSourceCollector(
        client, EvidenceArchive(tmp_path), destination_validator=lambda value: value
    )

    with pytest.raises(CollectionError, match="unsupported_schema") as captured:
        await collector.collect(
            _target("智能派", "https://www.elegoo.com.cn/index/join/index.html")
        )
    await client.aclose()

    assert captured.value.evidence is not None


@pytest.mark.asyncio
async def test_revopoint_adapter_collects_all_listing_pages_and_details(tmp_path) -> None:
    def listing(job_id: int, title: str, next_page: bool = False) -> str:
        page = '<a href="/gwtd-2.html" data-ci-pagination-page="2">2</a>' if next_page else ""
        return (
            '<table><tbody><tr><td onclick="my_load_more('
            f'{job_id});" class="apply-btn">{title}</td><td>研发类</td>'
            f"<td>西安</td><td>2026-08-18</td></tr></tbody></table>{page}"
        )

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/gwtd.html":
            return httpx.Response(200, text=listing(111, "【27秋招】产品经理", True))
        if request.url.path == "/gwtd-2.html":
            return httpx.Response(200, text=listing(112, "【27秋招】算法工程师"))
        job_id = request.url.params["id"]
        return httpx.Response(
            200,
            json={
                "code": 1,
                "msg": (
                    f"<h2>岗位 {job_id}</h2><div>一、岗位职责<br>负责三维产品研发。"
                    "<br>二、任职要求<br>硕士，熟悉 C++。</div>"
                ),
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await PublicSourceCollector(
        client, EvidenceArchive(tmp_path), destination_validator=lambda value: value
    ).collect(
        _target(
            "知象光电",
            "https://hr.revopoint3d.com.cn/gwtd.html",
            "https://hr.revopoint3d.com.cn",
        )
    )
    await client.aclose()

    assert [job.public_job_key for job in result.jobs] == ["111", "112"]
    assert result.jobs[1].title == "【27秋招】算法工程师"
    assert "三维产品研发" in result.jobs[1].duty_excerpt
    assert "C++" in result.jobs[1].requirement_excerpt


@pytest.mark.asyncio
async def test_revopoint_adapter_rejects_pagination_beyond_page_limit(tmp_path) -> None:
    listing = "".join(
        f'<a href="/gwtd-{page}.html">{page}</a>' for page in range(2, 23)
    )
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, text=listing)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    collector = PublicSourceCollector(
        client, EvidenceArchive(tmp_path), destination_validator=lambda value: value
    )

    with pytest.raises(CollectionError, match="response_truncated"):
        await collector.collect(
            _target("知象光电", "https://hr.revopoint3d.com.cn/gwtd.html")
        )
    await client.aclose()

    assert calls == ["/gwtd.html"]


@pytest.mark.asyncio
async def test_static_adapter_treats_verified_empty_listing_as_success(tmp_path) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<main>当前暂无招聘岗位</main>")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await PublicSourceCollector(
        client, EvidenceArchive(tmp_path), destination_validator=lambda value: value
    ).collect(
        _target("智能派", "https://www.elegoo.com.cn/index/join/index.html")
    )
    await client.aclose()

    assert result.jobs == ()


@pytest.mark.asyncio
async def test_static_adapter_does_not_treat_unknown_markup_as_verified_empty(
    tmp_path,
) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<main>网站新版正在加载</main>")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    collector = PublicSourceCollector(
        client, EvidenceArchive(tmp_path), destination_validator=lambda value: value
    )

    with pytest.raises(CollectionError, match="unsupported_schema") as captured:
        await collector.collect(
            _target("智能派", "https://www.elegoo.com.cn/index/join/index.html")
        )
    await client.aclose()

    assert captured.value.evidence is not None


@pytest.mark.asyncio
async def test_huawei_special_recruitment_adapter_reads_official_job_script(tmp_path) -> None:
    script = """
    var __data_list = [
      {name: 'AI工程师', type: '校园招聘', direction: ['AI'],
       res: `负责AI研发`, int: `机器学习<br>计算机视觉`, req: '',
       link: '/reccampportal/portal5/campus-recruitment-detail.html?jobId=223567'},
      {name: '测试装备开发专家', type: '社会招聘', direction: ['控制工程'],
       res: `负责运动控制`, int: '', req: `6年以上经验`,
       link: '/reccampportal/portal5/social-recruitment-detail.html?jobId=223665'}
    ];
    """.encode()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("huawei-special-recruitment.html"):
            return httpx.Response(200, text='<script src="./js/postDatas.js"></script>')
        return httpx.Response(200, content=script, headers={"content-type": "text/javascript"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await PublicSourceCollector(
        client, EvidenceArchive(tmp_path), destination_validator=lambda value: value
    ).collect(
        _target(
            "华为",
            "https://career.huawei.com/reccampportal/globle/huawei-special-recruitment.html",
            "https://career.huawei.com/reccampportal",
        )
    )
    await client.aclose()

    assert [job.public_job_key for job in result.jobs] == ["223567", "223665"]
    assert result.jobs[0].title == "AI工程师"
    assert "计算机视觉" in result.jobs[0].duty_excerpt
    assert "6年以上经验" in result.jobs[1].requirement_excerpt


@pytest.mark.asyncio
async def test_huawei_special_adapter_accepts_explicit_empty_job_array(tmp_path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("huawei-special-recruitment.html"):
            return httpx.Response(200, text="<main>当前暂无招聘岗位</main>")
        return httpx.Response(200, text="var __data_list = [];")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await PublicSourceCollector(
        client, EvidenceArchive(tmp_path), destination_validator=lambda value: value
    ).collect(
        _target(
            "华为",
            "https://career.huawei.com/reccampportal/globle/huawei-special-recruitment.html",
        )
    )
    await client.aclose()

    assert result.jobs == ()


@pytest.mark.asyncio
async def test_huawei_current_adapter_collects_social_jobs_and_verified_empty_campus(
    tmp_path,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("social-recruitment.html"):
            return httpx.Response(200, text="<main>社会招聘职位搜索</main>")
        if request.url.path.endswith("campus-recruitment.html"):
            return httpx.Response(200, text="<main>校园招聘职位搜索</main>")
        if "getJob/newHr/page" in request.url.path:
            assert request.headers["referer"] in {
                "https://career.huawei.com/reccampportal/portal5/social-recruitment.html",
                "https://career.huawei.com/reccampportal/portal5/campus-recruitment.html",
            }
            campus = request.url.params.get("jobType") == "0"
            jobs = [] if campus else [{
                "jobId": 34114,
                "jobname": "单板硬件工程师",
                "jobArea": "杭州",
                "mainBusiness": "负责硬件架构设计",
                "jobRequire": "本科，五年以上经验",
            }]
            return httpx.Response(
                200,
                json={
                    "pageVO": {
                        "totalRows": len(jobs),
                        "curPage": 1,
                        "pageSize": 100,
                        "totalPages": 1 if jobs else 0,
                    },
                    "result": jobs,
                },
            )
        raise AssertionError(request.url)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    collector = PublicSourceCollector(
        client, EvidenceArchive(tmp_path), destination_validator=lambda value: value
    )
    social = await collector.collect(
        _target(
            "华为",
            "https://career.huawei.com/reccampportal/portal5/social-recruitment.html",
        )
    )
    campus = await collector.collect(
        _target(
            "华为",
            "https://career.huawei.com/reccampportal/portal5/campus-recruitment.html",
        )
    )
    await client.aclose()

    assert [job.public_job_key for job in social.jobs] == ["34114"]
    assert social.jobs[0].title == "单板硬件工程师"
    assert campus.jobs == ()
