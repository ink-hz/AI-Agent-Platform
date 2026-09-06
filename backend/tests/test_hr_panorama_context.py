from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.hr.intelligence_markdown import VerifiedMarkdownChunk
from app.hr.panorama_context import MAX_PANORAMA_CONTEXT_BYTES, PanoramaContextProvider
from app.hr.panorama_repository import PanoramaConflict, PanoramaUnavailable

NOW = datetime(2026, 9, 6, 8, tzinfo=UTC)
OWNER = uuid4()
POSITION = uuid4()
TURN = uuid4()
CONVERSATION = uuid4()


class BundleSource:
    def __init__(self, *, current=True, coverage_state="succeeded") -> None:
        self.bundle_id = uuid4()
        self.calls = []
        self.current = current
        self.recorded = None
        self.record = {
            "bundle_id": self.bundle_id,
            "generated_at": NOW,
            "source_catalog": {"companies": [
                {"company_key": "union-optech", "canonical_name": "联合光电", "aliases": ["Union Optech"], "approved_urls": ["https://example.com/jobs"]},
                {"company_key": "hesai", "canonical_name": "禾赛科技", "aliases": [], "approved_urls": ["https://hesai.example/jobs"]},
            ]},
            "source_coverage": {"companies": [
                {"company_key": "union-optech", "state": coverage_state, "observed_at": NOW.isoformat(), "job_count": 2},
                {"company_key": "hesai", "state": "succeeded", "observed_at": NOW.isoformat(), "job_count": 1},
            ]},
            "aggregates": {"schema_version": 2, "tracks": {"social": 2, "campus": 1}, "directions": {"结构": 2, "算法": 1}, "locations": {"深圳": 2, "上海": 1}},
            "analysis": [{"unit_id": str(uuid4()), "kind": "company", "scope_key": "union-optech", "response": {
                "facts": [{"fact_id": "structure", "text": "联合光电公开招聘高级结构工程师，地点深圳", "evidence_sha256": "a" * 64, "source_url": "https://example.com/jobs/structure", "observed_at": NOW.isoformat()}],
                "inferences": [{"text": "精密结构与量产能力是当前公开人才信号", "basis_fact_ids": ["structure"]}],
                "unknowns": ["实际招聘人数未公开"], "alternatives": [], "summary": "结构岗位存在公开证据", "confidence": "high",
            }}],
        }
        self.jobs = (
            {"job_id": str(uuid4()), "source_id": str(uuid4()), "company_key": "union-optech", "public_job_key": "structure", "title": "高级结构工程师", "location": "深圳", "duty_excerpt": "负责精密结构和量产导入", "requirement_excerpt": "五年以上结构经验", "source_url": "https://example.com/jobs/structure", "evidence_sha256": "a" * 64, "observed_at": NOW.isoformat(), "status": "open"},
            {"job_id": str(uuid4()), "source_id": str(uuid4()), "company_key": "hesai", "public_job_key": "algorithm", "title": "算法工程师", "location": "上海", "duty_excerpt": "负责点云算法", "requirement_excerpt": "熟悉 C++", "source_url": "https://hesai.example/jobs/algorithm", "evidence_sha256": "b" * 64, "observed_at": NOW.isoformat(), "status": "open"},
        )

    def current_bundle(self):
        self.calls.append(("current_bundle",))
        return self.record if self.current else None

    def bundle_jobs(self, bundle_id):
        self.calls.append(("bundle_jobs", bundle_id))
        return self.jobs

    def bundle_reference_for_turn(self, owner_id, position_id, turn_id):
        self.calls.append(("bundle_reference_for_turn", owner_id, position_id, turn_id))
        return self.recorded

    def record_bundle_reference(self, **values):
        self.calls.append(("record_bundle_reference", values["bundle_id"]))
        self.recorded = {"context_document": values["context_document"]}
        return self.recorded

    def __getattr__(self, name):
        if any(token in name for token in ("collect", "analy", "operator", "model", "run", "publish")):
            raise AssertionError(f"forbidden execution surface requested: {name}")
        raise AttributeError(name)


def _chunk(chunk_id, path, text, **routing):
    body = text.encode()
    return {
        "chunk_id": chunk_id,
        "path": path,
        "heading": routing.pop("heading", "情报"),
        "byte_start": 0,
        "byte_end": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "scope": routing.pop("scope", "topic"),
        "scope_key": routing.pop("scope_key", "all"),
        "companies": routing.pop("companies", []),
        "tracks": routing.pop("tracks", []),
        "job_families": routing.pop("job_families", []),
        "directions": routing.pop("directions", []),
        "secondary_directions": routing.pop("secondary_directions", []),
        "locations": routing.pop("locations", []),
        "seniority": routing.pop("seniority", []),
        "skills": routing.pop("skills", []),
        "task_kinds": routing.pop("task_kinds", []),
        "evidence_ids": routing.pop("evidence_ids", ["a" * 64]),
        "priority": routing.pop("priority", 100),
        "_text": text,
    }


class MarkdownBundleSource(BundleSource):
    def __init__(self, *, tampered=False):
        super().__init__()
        self.tampered = tampered
        self.conversation_recorded = None
        chunks = [
            _chunk(
                "usage-boundary",
                "agent/index.md",
                "## 使用边界\n\n公开岗位数不等于 HC。\n",
                scope="index",
                scope_key="usage-boundary",
                evidence_ids=[],
                priority=1000,
            ),
            _chunk(
                "hesai-company",
                "agent/companies/hesai.md",
                "## 禾赛招聘情报\n\n禾赛公开招聘信号及产品路线证据。\n",
                scope="company",
                scope_key="hesai",
                companies=["hesai"],
                priority=200,
            ),
            _chunk(
                "point-cloud",
                "agent/directions/algorithm.md",
                "## 算法方向\n\n算法/点云与算法/SLAM能力组合。\n",
                scope="direction",
                scope_key="algorithm",
                directions=["算法"],
                secondary_directions=["算法/点云", "算法/SLAM"],
                locations=["深圳"],
                priority=150,
            ),
            _chunk(
                "interview-task",
                "agent/tasks/interview.md",
                "## 面试任务\n\n围绕点云工程化设计面试题。\n",
                scope="task",
                scope_key="interview",
                task_kinds=["position_interview_plan", "candidate_interview_plan"],
                priority=180,
            ),
            _chunk(
                "dfm",
                "agent/directions/manufacturing-process--dfm.md",
                "## DFM 工艺方向\n\n制造工艺/DFM 的岗位证据。\n",
                scope="secondary-direction",
                scope_key="制造工艺/DFM",
                directions=["制造工艺"],
                secondary_directions=["制造工艺/DFM"],
                priority=175,
            ),
        ]
        self.text_by_id = {item["chunk_id"]: item.pop("_text") for item in chunks}
        self.record.update({
            "schema_version": 2,
            "manifest_sha256": "f" * 64,
            "agent_chunk_index": chunks,
            "agent_document_index": {},
        })

    def conversation_bundle_reference_for_turn(
        self, owner_id, conversation_id, turn_id
    ):
        self.calls.append((
            "conversation_bundle_reference_for_turn",
            owner_id,
            conversation_id,
            turn_id,
        ))
        return self.conversation_recorded

    def record_conversation_bundle_reference(self, **values):
        self.calls.append(("record_conversation_bundle_reference", values["bundle_id"]))
        self.conversation_recorded = {"context_document": values["context_document"]}
        return self.conversation_recorded


class MarkdownStore:
    def __init__(self, source):
        self.source = source

    def read_chunk(self, bundle_id, record):
        if self.source.tampered:
            raise PanoramaUnavailable("tampered")
        text = self.source.text_by_id[record["chunk_id"]]
        return VerifiedMarkdownChunk(
            record["chunk_id"], record["path"], record["sha256"], text
        )


def test_position_task_gets_only_relevant_bundle_excerpts_with_provenance() -> None:
    source = BundleSource()
    fragment = PanoramaContextProvider(source).for_turn(
        OWNER, POSITION, "生成面试方案", TURN,
        task_kind="position_interview_plan",
        position_context={"title": "高级结构工程师", "location": "深圳"},
    )

    assert fragment is not None and fragment.status == "available"
    assert fragment.bundle_id == source.bundle_id
    assert all(item.bundle_id == source.bundle_id for item in (*fragment.source_facts, *fragment.aggregates, *fragment.interpretations))
    assert all(item.source_url.startswith("https://") and len(item.evidence_sha256) == 64 and item.observed_at == NOW for item in (*fragment.source_facts, *fragment.aggregates, *fragment.interpretations))
    text = json.dumps(fragment.as_prompt_document(), ensure_ascii=False)
    assert "高级结构工程师" in text and "深圳" in text
    assert "禾赛" not in text and "点云算法" not in text
    assert source.calls == [
        ("bundle_reference_for_turn", OWNER, POSITION, TURN),
        ("current_bundle",), ("bundle_jobs", source.bundle_id),
        ("record_bundle_reference", source.bundle_id),
    ]


def test_prompt_separates_facts_aggregates_interpretations_and_unknowns() -> None:
    fragment = PanoramaContextProvider(BundleSource()).for_turn(
        OWNER, POSITION, "生成 JR", TURN, task_kind="jr",
        position_context={"title": "结构工程师", "location": "深圳"},
    )
    document = fragment.as_prompt_document()
    assert document["intelligence_status"] == "available"
    assert document["source_facts"] and document["deterministic_aggregates"]
    assert document["ai_interpretations"] and document["unknowns"] == ["实际招聘人数未公开"]
    assert len(json.dumps(document, ensure_ascii=False).encode()) <= MAX_PANORAMA_CONTEXT_BYTES


def test_no_published_bundle_returns_explicit_unavailable_without_execution() -> None:
    source = BundleSource(current=False)
    fragment = PanoramaContextProvider(source).for_turn(
        OWNER, POSITION, "生成搜寻策略", TURN, task_kind="sourcing_strategy",
        position_context={"title": "结构工程师"},
    )
    assert fragment is not None
    assert fragment.status == "unavailable" and fragment.bundle_id is None
    assert fragment.as_prompt_document()["intelligence_status"] == "unavailable"
    assert source.calls == [("bundle_reference_for_turn", OWNER, POSITION, TURN), ("current_bundle",)]


def test_missing_position_dimension_is_an_explicit_gap_without_execution() -> None:
    source = BundleSource()
    fragment = PanoramaContextProvider(source).for_turn(
        OWNER, POSITION, "生成 JD", TURN, task_kind="jd",
        position_context={"title": "量子芯片工程师", "location": "合肥"},
    )
    assert fragment is not None and fragment.status == "partial"
    assert "当前 Bundle 没有与本岗位匹配的公开岗位证据" in fragment.unknowns
    assert source.calls == [
        ("bundle_reference_for_turn", OWNER, POSITION, TURN),
        ("current_bundle",), ("bundle_jobs", source.bundle_id),
        ("record_bundle_reference", source.bundle_id),
    ]


def test_later_failed_import_cannot_change_the_bundle_referenced_by_a_loaded_task() -> None:
    source = BundleSource()
    fragment = PanoramaContextProvider(source).for_turn(
        OWNER, POSITION, "生成 JD", TURN, task_kind="jd",
        position_context={"title": "结构工程师"},
    )
    original = fragment.as_prompt_document()
    source.current = False
    replayed = PanoramaContextProvider(source).for_turn(
        OWNER, POSITION, "生成 JD", TURN, task_kind="jd",
        position_context={"title": "结构工程师"},
    )
    assert replayed.as_prompt_document() == original
    assert replayed.bundle_id == source.bundle_id


def test_concurrent_bundle_pin_replays_the_exact_committed_winner() -> None:
    class RacingBundleSource(BundleSource):
        def record_bundle_reference(self, **values):
            super().record_bundle_reference(**values)
            raise PanoramaConflict("concurrent winner")

    source = RacingBundleSource()
    fragment = PanoramaContextProvider(source).for_turn(
        OWNER, POSITION, "生成 JD", TURN, task_kind="jd",
        position_context={"title": "结构工程师"},
    )

    assert fragment.bundle_id == source.bundle_id
    assert source.calls[-1] == (
        "bundle_reference_for_turn", OWNER, POSITION, TURN,
    )


@pytest.mark.parametrize("task_kind", [
    "jd",
    "jr",
    "talent_profile",
    "sourcing_strategy",
    "candidate_match",
    "position_interview_plan",
    "candidate_interview_plan",
])
def test_all_core_hr_tasks_receive_relevant_markdown(task_kind) -> None:
    source = MarkdownBundleSource()
    fragment = PanoramaContextProvider(
        source, markdown_store=MarkdownStore(source)
    ).for_turn(
        OWNER,
        POSITION,
        "处理当前岗位",
        TURN,
        task_kind=task_kind,
        position_context={"title": "点云 SLAM 算法工程师", "location": "深圳"},
    )

    document = fragment.as_prompt_document()
    assert document["schema_version"] == 3
    assert "## 招聘情报上下文" in document["markdown_context"]
    assert "算法/点云" in document["markdown_context"]
    assert document["chunks"]
    assert len(json.dumps(document, ensure_ascii=False).encode()) <= 32 * 1024


def test_replay_uses_exact_pinned_markdown_after_current_bundle_changes() -> None:
    source = MarkdownBundleSource()
    provider = PanoramaContextProvider(source, markdown_store=MarkdownStore(source))
    first = provider.for_turn(
        OWNER,
        POSITION,
        "生成点云岗位面试方案",
        TURN,
        task_kind="position_interview_plan",
        position_context={"title": "点云算法工程师", "location": "深圳"},
    )
    source.bundle_id = uuid4()
    source.record["bundle_id"] = source.bundle_id

    replay = provider.for_turn(
        OWNER,
        POSITION,
        "生成点云岗位面试方案",
        TURN,
        task_kind="position_interview_plan",
        position_context={"title": "点云算法工程师", "location": "深圳"},
    )

    assert replay.as_prompt_document() == first.as_prompt_document()


def test_tampered_markdown_falls_back_without_running_any_producer() -> None:
    source = MarkdownBundleSource(tampered=True)
    fragment = PanoramaContextProvider(
        source, markdown_store=MarkdownStore(source)
    ).for_turn(
        OWNER,
        POSITION,
        "生成结构岗位 JD",
        TURN,
        task_kind="jd",
        position_context={"title": "高级结构工程师", "location": "深圳"},
    )

    document = fragment.as_prompt_document()
    assert document["degraded_reason"] == "markdown_verification_failed"
    assert document["source_facts"]
    assert not any(
        token in call[0]
        for call in source.calls
        for token in ("collect", "analy", "produce")
    )


def test_general_hr_chat_can_retrieve_named_company_intelligence() -> None:
    source = MarkdownBundleSource()
    fragment = PanoramaContextProvider(
        source, markdown_store=MarkdownStore(source)
    ).for_conversation_turn(
        OWNER,
        CONVERSATION,
        "分析禾赛当前招聘和产品路线",
        TURN,
    )

    assert "禾赛" in fragment.markdown_context
    assert fragment.bundle_id is not None


def test_general_hr_chat_replays_pinned_markdown_without_a_current_bundle() -> None:
    source = MarkdownBundleSource()
    provider = PanoramaContextProvider(source, markdown_store=MarkdownStore(source))
    first = provider.for_conversation_turn(
        OWNER,
        CONVERSATION,
        "分析禾赛当前招聘和产品路线",
        TURN,
    )
    source.current = False

    replay = provider.for_conversation_turn(
        OWNER,
        CONVERSATION,
        "这条消息内容不再触发情报检索",
        TURN,
    )

    assert replay.as_prompt_document() == first.as_prompt_document()


def test_company_in_position_context_and_indexed_secondary_route_are_retrieved() -> None:
    source = MarkdownBundleSource()
    fragment = PanoramaContextProvider(
        source, markdown_store=MarkdownStore(source)
    ).for_turn(
        OWNER,
        POSITION,
        "生成人才画像",
        TURN,
        task_kind="talent_profile",
        position_context={"title": "禾赛 DFM 工艺工程师"},
    )

    selected_ids = {item["chunk_id"] for item in fragment.chunks}
    assert {"hesai-company", "dfm"} <= selected_ids
