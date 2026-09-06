import hashlib
import json
from uuid import UUID

import pytest

from tools.hr_intelligence.cli import main


def test_cli_initializes_only_the_explicit_external_local_root(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    bundle_id = UUID("00000000-0000-4000-8000-000000000001")
    catalog = tmp_path / "catalog.json"
    catalog.write_text('{"schema_version":1,"companies":[]}', encoding="utf-8")
    local_root = tmp_path / "factory"
    monkeypatch.setenv("HR_INTELLIGENCE_LOCAL_ROOT", str(local_root))

    assert (
        main(
            [
                "init",
                "--bundle-id",
                str(bundle_id),
                "--catalog",
                str(catalog),
            ]
        )
        == 0
    )

    assert (local_root / "work" / str(bundle_id) / "source-catalog.json").is_file()
    assert str(bundle_id) in capsys.readouterr().out


def test_cli_rejects_relative_catalog_path(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HR_INTELLIGENCE_LOCAL_ROOT", str(tmp_path / "factory"))

    with pytest.raises(ValueError, match="absolute"):
        main(
            [
                "init",
                "--bundle-id",
                "00000000-0000-4000-8000-000000000001",
                "--catalog",
                "relative.json",
            ]
        )


def test_cli_prepares_accepts_builds_and_verifies_one_bundle(
    tmp_path,
    monkeypatch,
) -> None:
    bundle_id = UUID("00000000-0000-4000-8000-000000000002")
    local_root = tmp_path / "factory"
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "companies": [
                    {
                        "company_key": "hesai",
                        "canonical_name": "禾赛科技",
                        "approved_urls": ["https://example.com/jobs"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HR_INTELLIGENCE_LOCAL_ROOT", str(local_root))
    assert main(["init", "--bundle-id", str(bundle_id), "--catalog", str(catalog)]) == 0
    work = local_root / "work" / str(bundle_id)
    evidence_body = b"one immutable public source"
    evidence_sha256 = hashlib.sha256(evidence_body).hexdigest()
    evidence = work / "evidence" / "sha256" / evidence_sha256[:2] / evidence_sha256
    evidence.parent.mkdir(parents=True)
    evidence.write_bytes(evidence_body)
    job = {
        "job_id": "00000000-0000-4000-8000-000000000010",
        "source_id": "00000000-0000-4000-8000-000000000011",
        "company_key": "hesai",
        "public_job_key": "algorithm-1",
        "title": "算法工程师",
        "location": "上海",
        "duty_excerpt": "负责点云算法",
        "requirement_excerpt": "熟悉 Python",
        "source_url": "https://example.com/social/jobs/1",
        "evidence_sha256": evidence_sha256,
        "observed_at": "2026-09-06T08:00:00+00:00",
        "status": "open",
    }
    (work / "normalized-jobs.jsonl").write_text(
        json.dumps(job, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (work / "source-coverage.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "companies": [
                    {
                        "company_key": "hesai",
                        "state": "succeeded",
                        "observed_at": "2026-09-06T08:00:00+00:00",
                        "job_count": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (work / "aggregates.json").write_text(
        json.dumps({"schema_version": 2, "tracks": {"social": 1}}),
        encoding="utf-8",
    )
    assert (
        main(
            [
                "prepare-analysis",
                "--bundle-id",
                str(bundle_id),
                "--units",
                "company,comparison,executive-summary",
            ]
        )
        == 0
    )
    for request_path in sorted((work / "analysis" / "requests").glob("*.json")):
        request = json.loads(request_path.read_text("utf-8"))
        unit_id = request["unit_id"]
        evidence_ref = request["evidence"][0]
        response = {
            "facts": [
                {
                    "fact_id": "fact-1",
                    "text": "公开岗位需要点云算法能力",
                    "evidence_sha256": evidence_ref["sha256"],
                    "source_url": evidence_ref["source_url"],
                    "observed_at": evidence_ref["observed_at"],
                }
            ],
            "inferences": [
                {"text": "存在点云算法人才需求信号", "basis_fact_ids": ["fact-1"]}
            ],
            "unknowns": ["实际 HC 未公开"],
            "alternatives": ["可能是常规补员"],
            "summary": "公开招聘信号指向点云算法能力。",
            "confidence": "medium",
        }
        usage = {
            "unit_id": unit_id,
            "provider": "openai",
            "model": "gpt-5.6-sol",
            "input_tokens": "unavailable",
            "output_tokens": "unavailable",
            "estimated_cost": "unavailable",
            "unavailable_reason": "test fixture has no provider telemetry",
        }
        response_path = work / "analysis" / "responses" / f"{unit_id}.json"
        usage_path = work / "analysis" / "usage" / f"{unit_id}.json"
        response_path.parent.mkdir(parents=True, exist_ok=True)
        usage_path.parent.mkdir(parents=True, exist_ok=True)
        response_path.write_text(json.dumps(response), encoding="utf-8")
        usage_path.write_text(json.dumps(usage), encoding="utf-8")
    assert main(["accept-analysis", "--bundle-id", str(bundle_id), "--all-ready"]) == 0
    assert (
        main(["analysis-status", "--bundle-id", str(bundle_id), "--require-complete"])
        == 0
    )
    assert main(["build", "--bundle-id", str(bundle_id)]) == 0
    built = local_root / "bundles" / str(bundle_id)
    assert main(["verify", "--bundle", str(built), "--strict"]) == 0
