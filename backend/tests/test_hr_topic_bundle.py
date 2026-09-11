import hashlib
import json
from dataclasses import replace
from uuid import UUID

import pytest
from test_hr_intelligence_bundle import NOW, _inputs

from app.hr.intelligence_bundle import verify_import_bundle
from tools.hr_intelligence import bundle, topic_bundle
from tools.hr_intelligence.analysis_units import (
    accept_unit_response,
    prepare_company_unit,
)


def source_bundle(tmp_path):
    inputs = _inputs(tmp_path)
    job = inputs.jobs[0]
    unit = replace(
        prepare_company_unit(inputs.bundle_id, "hesai", inputs.jobs, inputs.aggregates),
        kind="track",
        scope_key="campus",
    )
    accepted = accept_unit_response(
        unit,
        {
            "schema_version": 2,
            "summary": "当前公开校招覆盖禾赛。",
            "confidence": "medium",
            "facts": [
                {
                    "fact_id": "F-campus-1",
                    "text": "禾赛发布招聘岗位。",
                    "evidence_sha256": job.evidence_sha256,
                    "source_url": job.source_url,
                    "observed_at": NOW.isoformat(),
                }
            ],
            "inferences": [],
            "alternatives": [],
            "recommendations": [],
            "unknowns": ["实际编制未知。"],
        },
        {
            "unit_id": str(unit.unit_id),
            "provider": "openai",
            "model": "test-boundary",
            "input_tokens": 1,
            "output_tokens": 1,
            "estimated_cost": "0",
            "unavailable_reason": None,
        },
    )
    path = bundle.build_bundle(
        replace(inputs, analyses=(accepted,)), root=tmp_path / "source"
    )
    topics = [
        {
            "topic_id": "campus-review",
            "title": "校招样本",
            "question": "公开岗位覆盖哪些公司？",
            "scope": {
                "description": "当前采集范围",
                "company_keys": ["hesai"],
                "tracks": ["campus"],
            },
            "analysis_state": "limited",
            "unit_ids": [str(unit.unit_id)],
            "discussed_companies": [
                {
                    "company_key": "hesai",
                    "unit_id": str(unit.unit_id),
                    "claim_ids": ["F-campus-1"],
                    "explanation": "原事实列明禾赛岗位。",
                }
            ],
            "limitations": ["公开岗位不是编制。"],
        }
    ]
    return path, topics


def hashes(path):
    return {
        str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in path.rglob("*")
        if p.is_file()
    }


def test_derivation_is_immutable_preserves_analysis_and_reports_and_is_verifiable(
    tmp_path,
):
    source, topics = source_bundle(tmp_path)
    before = hashes(source)
    derived = topic_bundle.derive_topic_bundle(
        source, topics, root=tmp_path / "derived"
    )
    assert source != derived and hashes(source) == before
    for name in [
        "analysis.json",
        "analysis-usage.json",
        "normalized-jobs.jsonl",
        "source-coverage.json",
        "aggregates.json",
        "report.md",
        "report.pdf",
        "report.xlsx",
    ]:
        assert (source / name).read_bytes() == (derived / name).read_bytes()
    verified = verify_import_bundle(derived)
    assert bundle.verify_bundle(derived, strict=True).bundle_id == verified.bundle_id
    assert verified.manifest["provenance"]["source_bundle_id"] == source.name
    assert (
        verified.manifest["provenance"]["source_manifest_sha256"]
        == before["manifest.json"]
    )
    assert verified.manifest["provenance"]["analysis_sha256"] == before["analysis.json"]
    assert (
        verified.manifest["provenance"]["origin_documents"]
        == verified.manifest["document_index"]
    )
    assert str(verified.generated_at.isoformat()) == NOW.isoformat()
    assert all(u["bundle_id"] == source.name for u in verified.analysis)
    assert (
        f"bundle_id: {verified.bundle_id}"
        in (derived / "agent/topics/campus-review.md").read_text()
    )
    assert (
        topic_bundle.derive_topic_bundle(source, topics, root=tmp_path / "derived")
        == derived
    )


@pytest.mark.parametrize("verifier", [bundle.verify_bundle, verify_import_bundle])
def test_verifiers_reject_semantic_metadata_even_with_valid_checksums(
    tmp_path, verifier
):
    source, topics = source_bundle(tmp_path)
    catalog_path = source / "source-catalog.json"
    catalog = json.loads(catalog_path.read_text())
    topics[0]["discussed_companies"][0]["claim_ids"] = ["nonexistent"]
    catalog["topics"] = topics
    catalog_path.write_text(json.dumps(catalog))
    bundle._write_checksums(source)
    with pytest.raises(ValueError, match="topic"):
        verifier(source)


def test_compiler_accepts_only_explicit_analysis_origin(tmp_path):
    from tools.hr_intelligence.agent_markdown import (
        MarkdownContractError,
        compile_agent_markdown,
    )

    source, _ = source_bundle(tmp_path)
    v = verify_import_bundle(source)
    arguments = {
        "bundle_id": UUID("00000000-0000-4000-8000-000000000099"),
        "generated_at": v.generated_at,
        "catalog": v.catalog,
        "coverage": v.coverage,
        "aggregates": v.aggregates,
        "analyses": v.analysis,
    }
    with pytest.raises(MarkdownContractError, match="mismatch"):
        compile_agent_markdown(**arguments)
    result = compile_agent_markdown(**arguments, analysis_bundle_id=v.bundle_id)
    assert b"analysis_bundle_id:" in result.files["agent/index.md"]


@pytest.mark.parametrize("verifier", [bundle.verify_bundle, verify_import_bundle])
@pytest.mark.parametrize(
    "field,value",
    [
        ("analysis_sha256", "0" * 64),
        ("analysis_bundle_id", "00000000-0000-4000-8000-000000000099"),
        ("analysis_generated_at", "2026-09-08T12:00:00+00:00"),
    ],
)
def test_verifiers_reject_relabelled_analysis_origin(tmp_path, verifier, field, value):
    source, topics = source_bundle(tmp_path)
    derived = topic_bundle.derive_topic_bundle(
        source, topics, root=tmp_path / "derived"
    )
    manifest_path = derived / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["provenance"][field] = value
    manifest_path.write_text(json.dumps(manifest))
    bundle._write_checksums(derived)
    with pytest.raises(ValueError, match="provenance"):
        verifier(derived)


@pytest.mark.parametrize("verifier", [bundle.verify_bundle, verify_import_bundle])
def test_verifiers_reject_catalog_changed_after_review(tmp_path, verifier):
    source, topics = source_bundle(tmp_path)
    derived = topic_bundle.derive_topic_bundle(
        source, topics, root=tmp_path / "derived"
    )
    catalog_path = derived / "source-catalog.json"
    catalog = json.loads(catalog_path.read_text())
    catalog["topics"][0]["title"] = "未经复核的新研究名称"
    catalog_path.write_text(json.dumps(catalog))
    bundle._write_checksums(derived)
    with pytest.raises(ValueError, match="provenance"):
        verifier(derived)
