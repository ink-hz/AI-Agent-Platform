import hashlib
import json
import shutil
from pathlib import Path

import pytest
from app.hr.panorama_repository import PanoramaNotFound, PanoramaUnavailable
from app.hr.source_library import SourceLibrary


def _canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def _write_fixture(root: Path) -> str:
    company = {
        "company": {
            "company_key": "sample",
            "name": "样例公司",
            "aliases": ["Sample"],
            "job_count": 2,
            "coverage_state": "succeeded",
            "document_state": "succeeded",
            "observed_at": "2026-09-06T14:00:00+00:00",
        },
        "documents": [
            {
                "title": "样例官网",
                "text": "第一段\n第二段",
                "source_url": "https://example.com",
                "observed_at": "2026-09-06T14:00:00+00:00",
                "evidence_sha256": "a" * 64,
            }
        ],
        "channels": [
            {
                "channel": "社会招聘",
                "source_url": "https://example.com/jobs",
                "state": "succeeded",
                "observed_at": "2026-09-06T14:00:00+00:00",
                "job_count": 2,
                "error_code": None,
            }
        ],
        "limitations": [],
        "jobs": [
            {
                "job_id": "00000000-0000-0000-0000-000000000001",
                "title": "光学工程师",
                "location": "深圳",
                "status": "open",
                "source_url": "https://example.com/jobs/1",
                "observed_at": "2026-09-06T14:00:00+00:00",
                "channel": "社会招聘",
                "duty": "1. 第一项\n2. 第二项",
                "requirement": "1. 本科\n2. 光学专业",
                "fields": [{"label": "学历", "value": "本科"}],
                "evidence_sha256": "b" * 64,
                "public_job_key": "public-1",
                "source_kind": "job",
                "content_note": "正文已从确定归档岗位记录恢复并保留段落。",
            },
            {
                "job_id": "00000000-0000-0000-0000-000000000002",
                "title": "算法工程师",
                "location": "上海",
                "status": "unknown",
                "source_url": "https://example.com/jobs",
                "observed_at": "2026-09-06T14:00:00+00:00",
                "channel": "社会招聘",
                "duty": None,
                "requirement": None,
                "fields": [],
                "evidence_sha256": "c" * 64,
                "public_job_key": "public-2",
                "source_kind": "archive",
                "content_note": "归档没有可确认的岗位正文。",
            },
        ],
    }
    company_body = _canonical(company) + b"\n"
    (root / "sample.json").write_bytes(company_body)
    manifest = {
        "schema_version": 1,
        "source_bundle_id": "2ed2262c-63a0-47e5-996c-7d00eb0be1a0",
        "observed_at": "2026-09-06T14:00:00+00:00",
        "job_count": 2,
        "companies": [
            {
                **company["company"],
                "path": "sample.json",
                "sha256": hashlib.sha256(company_body).hexdigest(),
                "size_bytes": len(company_body),
            }
        ],
    }
    edition = "source-" + hashlib.sha256(_canonical(manifest)).hexdigest()[:20]
    manifest["edition"] = edition
    (root / "manifest.json").write_bytes(_canonical(manifest) + b"\n")
    return edition


def test_source_library_is_edition_pinned_filterable_and_paginated(tmp_path):
    edition = _write_fixture(tmp_path)
    library = SourceLibrary(tmp_path)

    assert library.catalog() == {
        "edition": edition,
        "source_bundle_id": "2ed2262c-63a0-47e5-996c-7d00eb0be1a0",
        "observed_at": "2026-09-06T14:00:00+00:00",
        "job_count": 2,
        "companies": [
            {
                "company_key": "sample",
                "name": "样例公司",
                "aliases": ["Sample"],
                "job_count": 2,
                "coverage_state": "succeeded",
                "document_state": "succeeded",
                "observed_at": "2026-09-06T14:00:00+00:00",
            }
        ],
    }
    page = library.company(
        "sample",
        edition,
        q="光学",
        location="深圳",
        channel="社会招聘",
        offset=0,
        limit=1,
    )
    assert page["total"] == 1
    assert page["offset"] == 0 and page["limit"] == 1
    assert page["locations"] == ["上海", "深圳"]
    assert page["channel_options"] == ["社会招聘"]
    assert page["items"] == [
        {
            key: company_value
            for key, company_value in company["jobs"][0].items()
            if key
            in {
                "job_id",
                "title",
                "location",
                "status",
                "source_url",
                "observed_at",
                "channel",
            }
        }
        for company in [json.loads((tmp_path / "sample.json").read_text())]
    ]
    body_search = library.company("sample", edition, q="本科")
    assert [item["job_id"] for item in body_search["items"]] == [
        "00000000-0000-0000-0000-000000000001"
    ]
    detail = library.job("sample", page["items"][0]["job_id"], edition)
    assert detail["edition"] == edition
    assert detail["company_key"] == "sample"
    assert detail["duty"] == "1. 第一项\n2. 第二项"
    assert detail["source_kind"] == "job"

    with pytest.raises(PanoramaNotFound):
        library.company("sample", "wrong-edition")
    with pytest.raises(PanoramaNotFound):
        library.job("other", detail["job_id"], edition)


def test_source_reference_resolution_is_exact_and_keeps_archive_at_company_scope(
    tmp_path,
):
    edition = _write_fixture(tmp_path)
    library = SourceLibrary(tmp_path)
    markdown = (
        "[direct](https://example.com/jobs/1) "
        "[archive](https://example.com/jobs) "
        "[unrelated](https://example.com/jobs/10)"
    )
    resolved = library.resolve_references(
        markdown, "2ed2262c-63a0-47e5-996c-7d00eb0be1a0"
    )
    assert resolved == {
        "edition": edition,
        "source_bundle_id": "2ed2262c-63a0-47e5-996c-7d00eb0be1a0",
        "links": {
            "https://example.com/jobs/1": {
                "company_key": "sample",
                "job_id": "00000000-0000-0000-0000-000000000001",
            },
            "https://example.com/jobs": {
                "company_key": "sample",
                "job_id": None,
            },
        },
    }
    with pytest.raises(PanoramaNotFound):
        library.resolve_references(markdown, "different-bundle")


def test_source_library_rejects_tampered_company_file(tmp_path):
    edition = _write_fixture(tmp_path)
    path = tmp_path / "sample.json"
    path.write_text(path.read_text().replace("光学工程师", "伪造岗位"))
    with pytest.raises(PanoramaUnavailable):
        SourceLibrary(tmp_path).company("sample", edition)


def test_committed_source_package_preserves_all_identities_and_real_paragraphs():
    library = SourceLibrary()
    catalog = library.catalog()
    assert catalog["source_bundle_id"] == "2ed2262c-63a0-47e5-996c-7d00eb0be1a0"
    assert catalog["observed_at"] == "2026-09-06T14:11:24.097448+00:00"
    assert catalog["job_count"] == 3437
    assert len(catalog["companies"]) == 12
    assert sum(item["job_count"] for item in catalog["companies"]) == 3437

    page = library.company("robosense", catalog["edition"], q="激光雷达系统工程师")
    assert page["total"] >= 1
    detail = library.job("robosense", page["items"][0]["job_id"], catalog["edition"])
    assert "\n" in detail["duty"]
    assert "1." in detail["duty"] and "2." in detail["duty"]
    assert "任职要求" not in detail["duty"]
    assert detail["requirement"].startswith("1.")
    assert (
        detail["evidence_sha256"]
        == "b6d10a28729cda19f3bd1e558030d35f7da0cbcfcb350e8b8ba46f0d9d290836"
    )
    assert "确定归档" in detail["content_note"]

    union = library.company("union-optech", catalog["edition"])
    assert union["total"] == 8
    for item in union["items"]:
        record = library.job("union-optech", item["job_id"], catalog["edition"])
        assert "规范化摘录" not in record["content_note"]
        assert record["duty"]
    union_page = library.company("union-optech", catalog["edition"], q="制程工程师")
    union_detail = library.job(
        "union-optech", union_page["items"][0]["job_id"], catalog["edition"]
    )
    assert "\n" in union_detail["duty"]
    assert {field["label"] for field in union_detail["fields"]} >= {"学历", "薪资"}

    bambu = library.company("bambu-lab", catalog["edition"], limit=1)
    bambu_detail = library.job(
        "bambu-lab", bambu["items"][0]["job_id"], catalog["edition"]
    )
    assert bambu_detail["status"] == "unknown"
    assert "状态标为 unknown" in bambu_detail["content_note"]

    hesai = library.company("hesai", catalog["edition"])
    assert {
        channel["source_url"]: channel["channel"]
        for channel in hesai["channels"]
        if channel["job_count"] is not None
    } == {
        "https://kwh0jtf778.jobs.feishu.cn/index": "社会招聘",
        "https://kwh0jtf778.jobs.feishu.cn/229043": "校园招聘",
        "https://kwh0jtf778.jobs.feishu.cn/073183": "实习招聘",
    }
    assert any(
        channel
        == {
            "channel": "公司官网资料",
            "source_url": "https://www.hesaitech.com",
            "state": "failed",
            "observed_at": "2026-09-06T14:11:00.343213+00:00",
            "job_count": None,
            "error_code": "source_rejected",
        }
        for channel in hesai["channels"]
    )
    assert "公司官网资料" not in hesai["channel_options"]


def test_changed_committed_body_cannot_masquerade_as_source_edition(tmp_path):
    source = Path(__file__).parents[1] / "app/hr/source_content"
    shutil.copytree(source, tmp_path / "content")
    library = SourceLibrary(tmp_path / "content")
    catalog = library.catalog()
    entry = next(
        item
        for item in json.loads((tmp_path / "content/manifest.json").read_text())[
            "companies"
        ]
        if item["company_key"] == "robosense"
    )
    path = tmp_path / "content" / entry["path"]
    path.write_text(path.read_text().replace("激光雷达系统工程师", "伪造岗位", 1))
    with pytest.raises(PanoramaUnavailable):
        SourceLibrary(tmp_path / "content").company("robosense", catalog["edition"])
