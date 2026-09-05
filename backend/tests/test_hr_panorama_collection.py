from __future__ import annotations

import hashlib
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
