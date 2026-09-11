from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from uuid import UUID

import pytest

from tools.hr_intelligence.agent_markdown import (
    MarkdownContractError,
    compile_agent_markdown,
)
from tools.hr_intelligence.chunk_index import validate_chunk_index

BUNDLE_ID = UUID("00000000-0000-4000-8000-000000000001")
NOW = datetime(2026, 9, 6, 8, tzinfo=UTC)
SHA256 = "a" * 64
SOURCE_URL = "https://example.com/jobs/1"


def _inputs(*, fact_sha256: str = SHA256, summary: str = "禾赛招聘情报摘要"):
    return {
        "bundle_id": BUNDLE_ID,
        "generated_at": NOW,
        "catalog": {
            "schema_version": 1,
            "companies": [
                {
                    "company_key": "hesai",
                    "canonical_name": "禾赛科技",
                    "aliases": ["Hesai"],
                    "approved_urls": ["https://example.com/jobs"],
                }
            ],
        },
        "coverage": {
            "schema_version": 1,
            "companies": [
                {
                    "company_key": "hesai",
                    "state": "succeeded",
                    "observed_at": NOW.isoformat(),
                    "job_count": 1,
                }
            ],
        },
        "aggregates": {
            "schema_version": 3,
            "tracks": {"social": 1},
            "directions": {"算法": 1},
            "secondary_directions": {"算法/点云": 1},
            "job_families": {"research_development": 1},
            "company_matrix": {
                "hesai": {
                    "tracks": {"social": 1},
                    "directions": {"算法": 1},
                    "secondary_directions": {"算法/点云": 1},
                    "job_families": {"research_development": 1},
                    "locations": {"上海": 1},
                    "seniority": {"senior": 1},
                    "skills": {"C++": 1},
                }
            },
        },
        "analyses": (
            {
                "bundle_id": str(BUNDLE_ID),
                "unit_id": "00000000-0000-4000-8000-000000000002",
                "kind": "company",
                "scope_key": "hesai",
                "evidence": [
                    {
                        "job_id": "00000000-0000-4000-8000-000000000003",
                        "sha256": SHA256,
                        "source_url": SOURCE_URL,
                        "observed_at": NOW.isoformat(),
                    }
                ],
                "response": {
                    "schema_version": 2,
                    "summary": summary,
                    "confidence": "high",
                    "facts": [
                        {
                            "fact_id": "F-company-hesai-1",
                            "text": "禾赛公开招聘点云算法工程师。",
                            "evidence_sha256": fact_sha256,
                            "source_url": SOURCE_URL,
                            "observed_at": NOW.isoformat(),
                        }
                    ],
                    "inferences": [
                        {
                            "inference_id": "I-company-hesai-1",
                            "text": "点云能力是当前公开人才信号。",
                            "basis_fact_ids": ["F-company-hesai-1"],
                        }
                    ],
                    "unknowns": ["实际 HC 未公开。"],
                    "alternatives": [
                        {
                            "alternative_id": "X-company-hesai-1",
                            "text": "常年开放岗位可能放大当期需求。",
                            "basis_fact_ids": ["F-company-hesai-1"],
                            "challenged_inference_ids": ["I-company-hesai-1"],
                        }
                    ],
                    "recommendations": [
                        {
                            "recommendation_id": "R-company-hesai-1",
                            "text": "建立点云人才池。",
                            "basis_fact_ids": ["F-company-hesai-1"],
                            "target_tasks": ["talent_profile", "sourcing_strategy"],
                        }
                    ],
                },
            },
        ),
    }


def test_compiles_company_markdown_with_provenance_and_claim_classes() -> None:
    package = compile_agent_markdown(**_inputs())

    body = package.files["agent/companies/hesai.md"].decode("utf-8")

    assert f"bundle_id: {BUNDLE_ID}" in body
    assert "scope: company" in body
    assert "scope_key: hesai" in body
    assert "confidence: high" in body
    assert "coverage: succeeded" in body
    assert "evidence_count: 1" in body
    assert "## 可引用事实" in body
    assert "事实 F-company-hesai-1" in body
    assert "研判 I-company-hesai-1" in body
    assert "未知 U-company-hesai-1" in body
    assert "替代解释 X-company-hesai-1" in body
    assert "建议 R-company-hesai-1" in body
    assert SOURCE_URL in body
    assert SHA256 in body


def test_chunk_index_is_deterministic_and_covers_every_agent_markdown() -> None:
    first = compile_agent_markdown(**_inputs())
    second = compile_agent_markdown(**_inputs())

    assert first.files == second.files
    assert [item.as_dict() for item in first.chunks] == [
        item.as_dict() for item in second.chunks
    ]
    validate_chunk_index(first.files, first.chunks)
    markdown_paths = {
        path for path in first.files if path.endswith(".md")
    }
    assert {item.path for item in first.chunks} == markdown_paths
    serialized = json.loads(first.files["agent/chunk-index.json"])
    assert serialized == [item.as_dict() for item in first.chunks]
    assert any(SHA256 in item.evidence_ids for item in first.chunks)


def test_compiler_escapes_untrusted_html_and_rejects_orphan_evidence() -> None:
    escaped = compile_agent_markdown(
        **_inputs(summary="<script>alert('source')</script>")
    )
    body = escaped.files["agent/companies/hesai.md"].decode("utf-8")
    assert "<script>" not in body
    assert "&lt;script&gt;" in body

    with pytest.raises(MarkdownContractError, match="evidence"):
        compile_agent_markdown(**_inputs(fact_sha256="b" * 64))


def test_compiler_always_emits_index_and_all_task_playbooks() -> None:
    package = compile_agent_markdown(**_inputs())

    assert {
        "agent/index.md",
        "agent/executive-brief.md",
        "agent/tasks/jd-jr.md",
        "agent/tasks/talent-profile.md",
        "agent/tasks/sourcing.md",
        "agent/tasks/resume-review.md",
        "agent/tasks/interview.md",
    }.issubset(package.files)


def test_task_playbook_preserves_accepted_task_analysis_and_evidence() -> None:
    inputs = _inputs()
    task = deepcopy(inputs["analyses"][0])
    task.update(
        {
            "unit_id": "00000000-0000-4000-8000-000000000004",
            "kind": "task",
            "scope_key": "interview",
        }
    )
    task["response"]["summary"] = "面试任务应围绕可验证证据设计问题。"
    inputs["analyses"] = (*inputs["analyses"], task)

    package = compile_agent_markdown(**inputs)
    body = package.files["agent/tasks/interview.md"].decode("utf-8")

    assert "面试任务应围绕可验证证据设计问题" in body
    assert "## 可引用事实" in body
    assert "事实 F-company-hesai-1" in body
    assert "evidence_count: 1" in body
    assert SOURCE_URL in body
    assert SHA256 in body


def test_company_and_direction_chunks_carry_real_aggregate_routing() -> None:
    package = compile_agent_markdown(**_inputs())

    company = next(
        item for item in package.chunks if item.path == "agent/companies/hesai.md"
    )
    direction = next(
        item
        for item in package.chunks
        if item.path == "agent/directions/algorithm.md"
    )

    assert company.companies == ("hesai",)
    assert company.tracks == ("social",)
    assert company.directions == ("算法",)
    assert company.secondary_directions == ("算法/点云",)
    assert company.job_families == ("research_development",)
    assert company.locations == ("上海",)
    assert company.seniority == ("senior",)
    assert company.skills == ("C++",)
    assert direction.secondary_directions == ("算法/点云",)


def test_secondary_direction_analysis_is_preserved_as_agent_markdown() -> None:
    inputs = _inputs()
    secondary = deepcopy(inputs["analyses"][0])
    secondary.update(
        {
            "unit_id": "00000000-0000-4000-8000-000000000004",
            "kind": "secondary-direction",
            "scope_key": "算法/点云",
        }
    )
    inputs["analyses"] = (*inputs["analyses"], secondary)

    package = compile_agent_markdown(**inputs)
    chunks = [
        item
        for item in package.chunks
        if item.scope == "secondary-direction"
        and item.secondary_directions == ("算法/点云",)
    ]

    assert len(chunks) > 0
    assert chunks[0].directions == ("算法",)
    assert "禾赛招聘情报摘要" in package.files[chunks[0].path].decode("utf-8")


def test_evidence_section_only_lists_exact_fact_references() -> None:
    inputs = _inputs()
    analysis = deepcopy(inputs["analyses"][0])
    analysis["evidence"].append(
        {
            "job_id": "00000000-0000-4000-8000-000000000099",
            "sha256": SHA256,
            "source_url": "https://example.com/jobs/unused",
            "observed_at": NOW.isoformat(),
        }
    )
    inputs["analyses"] = (analysis,)

    package = compile_agent_markdown(**inputs)
    body = package.files["agent/companies/hesai.md"].decode("utf-8")

    assert "evidence_count: 1" in body
    assert SOURCE_URL in body
    assert "https://example.com/jobs/unused" not in body


def test_declared_topic_metadata_controls_docs_and_discussion_routing():
    inputs = _inputs()
    unit = deepcopy(inputs['analyses'][0])
    unit.update(kind='track', scope_key='campus')
    inputs['analyses'] += (dict(unit, unit_id='00000000-0000-4000-8000-000000000004'),)
    inputs['catalog']['topics'] = [{
        'topic_id': 'campus-review', 'title': '校园人才布局', 'question': '校招正在关注什么能力？',
        'scope': {'description': '已采集禾赛校招岗位', 'company_keys': ['hesai'], 'tracks': ['campus']},
        'analysis_state': 'limited', 'unit_ids': ['00000000-0000-4000-8000-000000000004'],
        'discussed_companies': [], 'limitations': ['没有跨期证据。'],
    }]
    package = compile_agent_markdown(**inputs)
    assert {p for p in package.files if p.startswith('agent/topics/')} == {'agent/topics/campus-review.md'}
    body = package.files['agent/topics/campus-review.md'].decode()
    for text in ['校园人才布局', '校招正在关注什么能力？', '已采集禾赛校招岗位', 'limited', '没有跨期证据。']:
        assert text in body
    chunks = [c for c in package.chunks if c.scope == 'topic']
    assert chunks and all(c.companies == () and c.tracks == ('campus',) for c in chunks)
    inputs['catalog']['topics'][0]['discussed_companies'] = [{
        'company_key': 'hesai', 'unit_id': '00000000-0000-4000-8000-000000000004',
        'claim_ids': ['I-company-hesai-1'], 'explanation': '研判指出禾赛公开人才需求。',
    }]
    related = compile_agent_markdown(**inputs)
    assert all(c.companies == ('hesai',) for c in related.chunks if c.scope == 'topic')
