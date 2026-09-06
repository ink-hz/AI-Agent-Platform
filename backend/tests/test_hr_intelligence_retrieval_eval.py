from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from test_hr_intelligence_bundle import _inputs
from test_hr_panorama_context import MarkdownBundleSource, MarkdownStore, _chunk

from app.hr.panorama_context import PanoramaContextProvider
from tools.hr_intelligence.bundle import build_bundle
from tools.hr_intelligence.cli import _parser
from tools.hr_intelligence.retrieval_eval import (
    LocalBundleRetrievalProbe,
    RetrievalCase,
    RetrievalObservation,
    evaluate_retrieval,
    load_cases,
)

CASES = (
    Path(__file__).parents[1]
    / "tools"
    / "hr_intelligence"
    / "retrieval_eval_cases.v1.json"
)
SHA256 = "a" * 64


class PassingProbe:
    def retrieve(self, case, *, replay=False):
        evidence = (SHA256,) if case.requires_evidence else ()
        return RetrievalObservation(
            prompt_document={
                "schema_version": 3,
                "case_id": case.case_id,
                "markdown_context": "已验签招聘情报",
                "intelligence_status": (
                    "partial"
                    if case.coverage_scenario == "partial_source"
                    else "available"
                ),
            },
            selected_companies=frozenset(case.expected_companies),
            selected_tags=frozenset(case.expected_tags),
            selected_evidence_ids=frozenset(evidence),
            known_evidence_ids=frozenset({SHA256}),
        )


def test_fixed_suite_covers_all_companies_tasks_and_required_dimensions() -> None:
    cases = load_cases(CASES)

    assert len(cases) == 40
    assert {item.task_kind for item in cases} == {
        "jd",
        "jr",
        "talent_profile",
        "sourcing_strategy",
        "candidate_match",
        "position_interview_plan",
        "candidate_interview_plan",
        "general_chat",
    }
    assert {company for item in cases for company in item.expected_companies} == {
        "union-optech",
        "robosense",
        "hesai",
        "bambu-lab",
        "creality",
        "elegoo",
        "revopoint",
        "shining3d",
        "scantech",
        "agibot",
        "insta360",
        "huawei",
    }
    directions = {
        tag for item in cases for tag in item.expected_tags if "/" not in tag
    }
    secondary = {
        tag for item in cases for tag in item.expected_tags if "/" in tag
    }
    assert len(directions & {"光学", "硬件", "结构", "软件", "算法", "制造工艺", "质量", "产品", "供应链"}) >= 8
    assert len(secondary) >= 12
    assert {"social", "campus", "intern"} <= directions
    assert any(not item.expected_companies and not item.requires_evidence for item in cases)
    assert any(item.coverage_scenario == "partial_source" for item in cases)
    assert any("Bambu Lab" in item.query for item in cases)
    assert any("ELEGOO" in item.query for item in cases)


def test_retrieval_acceptance_suite_meets_release_threshold() -> None:
    result = evaluate_retrieval(PassingProbe(), load_cases(CASES))

    assert result.case_count == 40
    assert result.context_relevance == Decimal(1)
    assert result.provenance_coverage == Decimal(1)
    assert result.fabricated_source_count == 0
    assert result.replay_mismatch_count == 0
    assert result.failures == ()


def test_evaluator_reports_relevance_provenance_and_replay_failures() -> None:
    case = load_cases(CASES)[0]

    class FailingProbe:
        def retrieve(self, selected, *, replay=False):
            return RetrievalObservation(
                prompt_document={"replay": replay},
                selected_companies=frozenset(),
                selected_tags=frozenset(),
                selected_evidence_ids=frozenset({"b" * 64}),
                known_evidence_ids=frozenset({SHA256}),
            )

    result = evaluate_retrieval(FailingProbe(), (case,))

    assert result.context_relevance == Decimal(0)
    assert result.provenance_coverage == Decimal(0)
    assert result.fabricated_source_count == 1
    assert result.replay_mismatch_count == 1
    assert any(case.case_id in failure for failure in result.failures)
    assert not result.accepted


def test_case_loader_rejects_duplicate_ids_and_relative_paths(tmp_path) -> None:
    with pytest.raises(ValueError, match="absolute"):
        load_cases(Path("relative.json"))

    document = CASES.read_text("utf-8").replace(
        '"case_id":"general-hesai-point-cloud"',
        '"case_id":"general-robosense-slam"',
    )
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(document, "utf-8")
    with pytest.raises(ValueError, match="identity"):
        load_cases(duplicate)


def test_cli_accepts_bundle_and_fixed_cases_for_retrieval_evaluation() -> None:
    args = _parser().parse_args(
        [
            "evaluate-retrieval",
            "--bundle",
            "/absolute/bundle",
            "--cases",
            str(CASES),
        ]
    )

    assert args.command == "evaluate-retrieval"
    assert args.bundle == "/absolute/bundle"
    assert Path(args.cases) == CASES


def test_local_bundle_probe_uses_real_retriever_and_survives_current_loss(
    tmp_path,
) -> None:
    bundle = build_bundle(_inputs(tmp_path), root=tmp_path / "bundles")
    case = RetrievalCase(
        case_id="local-interview",
        task_kind="candidate_interview_plan",
        query="生成候选人面试题",
        position_context={"title": "量子传感研究员"},
        expected_companies=(),
        expected_tags=("candidate_interview_plan",),
        forbidden_companies=(),
        requires_evidence=False,
        coverage_scenario=None,
    )
    probe = LocalBundleRetrievalProbe(bundle)

    first = probe.retrieve(case)
    replay = probe.retrieve(case, replay=True)

    assert first.prompt_document["schema_version"] == 3
    assert "candidate_interview_plan" in first.selected_tags
    assert replay.prompt_document == first.prompt_document


def test_production_retriever_meets_all_fixed_routing_expectations() -> None:
    cases = load_cases(CASES)
    companies = {
        "union-optech": ("联合光电", ["中山联合光电", "Union Optech"]),
        "robosense": ("速腾聚创", ["RoboSense"]),
        "hesai": ("禾赛科技", ["禾赛", "Hesai"]),
        "bambu-lab": ("拓竹", ["Bambu Lab"]),
        "creality": ("创想三维", ["Creality"]),
        "elegoo": ("智能派", ["ELEGOO"]),
        "revopoint": ("知象光电", ["Revopoint"]),
        "shining3d": ("先临三维", ["SHINING 3D"]),
        "scantech": ("思看科技", ["SCANTECH"]),
        "agibot": ("智元机器人", ["智元", "AGIBOT"]),
        "insta360": ("影石创新", ["影石", "Insta360"]),
        "huawei": ("华为", ["Huawei"]),
    }
    expected_tags = {tag for case in cases for tag in case.expected_tags}

    for case in cases:
        source = MarkdownBundleSource()
        raw_chunks = [
            _chunk(
                "usage-boundary",
                "agent/index.md",
                "## 使用边界\n\n公开岗位数不等于 HC。\n",
                scope="index",
                scope_key="usage-boundary",
                evidence_ids=[],
                priority=1000,
            )
        ]
        for key in companies:
            raw_chunks.append(
                _chunk(
                    f"company-{key}",
                    f"agent/companies/{key}.md",
                    f"## {key}\n\n已核验公司招聘事实 {'a' * 64}。\n",
                    scope="company",
                    scope_key=key,
                    companies=[key],
                )
            )
        for tag in sorted(expected_tags):
            if tag in {
                "jd",
                "jr",
                "talent_profile",
                "sourcing_strategy",
                "candidate_match",
                "position_interview_plan",
                "candidate_interview_plan",
            }:
                raw_chunks.append(
                    _chunk(
                        f"task-{tag}",
                        f"agent/tasks/{tag}.md",
                        f"## {tag}\n\n任务方法 {'a' * 64}。\n",
                        scope="task",
                        scope_key=tag,
                        task_kinds=[tag],
                    )
                )
            elif tag in {"social", "campus", "intern"}:
                raw_chunks.append(
                    _chunk(
                        f"track-{tag}",
                        f"agent/topics/{tag}.md",
                        f"## {tag}\n\n招聘渠道事实 {'a' * 64}。\n",
                        scope="topic",
                        scope_key=tag,
                        tracks=[tag],
                    )
                )
            elif "/" in tag:
                parent = tag.split("/", 1)[0]
                slug = str(abs(hash(tag)))
                raw_chunks.append(
                    _chunk(
                        f"secondary-{slug}",
                        f"agent/directions/secondary-{slug}.md",
                        f"## {tag}\n\n二级方向事实 {'a' * 64}。\n",
                        scope="secondary-direction",
                        scope_key=tag,
                        directions=[parent],
                        secondary_directions=[tag],
                    )
                )
            elif tag in {"光学", "硬件", "结构", "软件", "算法", "制造工艺", "质量", "产品", "供应链"}:
                slug = str(abs(hash(tag)))
                raw_chunks.append(
                    _chunk(
                        f"direction-{slug}",
                        f"agent/directions/direction-{slug}.md",
                        f"## {tag}\n\n一级方向事实 {'a' * 64}。\n",
                        scope="direction",
                        scope_key=tag,
                        directions=[tag],
                    )
                )
        source.text_by_id = {
            item["chunk_id"]: item.pop("_text") for item in raw_chunks
        }
        source.record["agent_chunk_index"] = raw_chunks
        source.record["source_catalog"] = {
            "companies": [
                {
                    "company_key": key,
                    "canonical_name": name,
                    "aliases": aliases,
                    "approved_urls": ["https://example.com/jobs"],
                }
                for key, (name, aliases) in companies.items()
            ]
        }
        provider = PanoramaContextProvider(
            source, markdown_store=MarkdownStore(source)
        )
        fragment = (
            provider.for_conversation_turn(
                uuid4(), uuid4(), case.query, uuid4()
            )
            if case.task_kind == "general_chat"
            else provider.for_turn(
                uuid4(),
                uuid4(),
                case.query,
                uuid4(),
                task_kind=case.task_kind,
                position_context=case.position_context,
            )
        )
        by_id = {str(item["chunk_id"]): item for item in raw_chunks}
        selected = [by_id[str(item["chunk_id"])] for item in fragment.chunks]
        selected_companies = {
            company
            for item in selected
            for company in item.get("companies", [])
        }
        selected_tags = {
            value
            for item in selected
            for field in (
                "tracks",
                "job_families",
                "directions",
                "secondary_directions",
                "locations",
                "seniority",
                "skills",
                "task_kinds",
            )
            for value in item.get(field, [])
        }
        assert set(case.expected_companies) <= selected_companies, case.case_id
        assert set(case.expected_tags) <= selected_tags, case.case_id
        assert not set(case.forbidden_companies) & selected_companies, case.case_id
