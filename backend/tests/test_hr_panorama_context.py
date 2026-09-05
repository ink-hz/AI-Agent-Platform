from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.hr.panorama_context import (
    MAX_PANORAMA_CONTEXT_BYTES,
    PanoramaContextError,
    PanoramaContextProvider,
)
from app.hr.panorama_models import (
    PositionInsightRetrieval,
    TalentInsightVersion,
    TalentSource,
    thaw_json,
)
from app.hr.panorama_repository import PanoramaConflict, PanoramaNotFound

NOW = datetime(2026, 9, 5, 8, tzinfo=UTC)
OWNER = uuid4()
POSITION = uuid4()
TURN = uuid4()
CONVERSATION = uuid4()
SOURCE = uuid4()


def _source(*, name: str = "联合光电", aliases=("Union Optech",)) -> TalentSource:
    return TalentSource(
        SOURCE,
        OWNER,
        uuid4(),
        "company",
        f"company-{SOURCE.hex}",
        name,
        aliases,
        ("https://example.com/jobs",),
        True,
        NOW,
        NOW,
    )


def _insight(
    insight_id: UUID,
    *,
    created_at: datetime = NOW,
    observed_at: datetime = NOW,
    text: str = "公开招聘结构工程师",
    source_id: UUID = SOURCE,
) -> TalentInsightVersion:
    fact_id = f"fact-{insight_id}"
    return TalentInsightVersion(
        insight_id,
        OWNER,
        uuid4(),
        uuid4(),
        1,
        (source_id,),
        (uuid4(),),
        (
            {
                "fact_id": fact_id,
                "text": text,
                "snapshot_id": str(uuid4()),
                "observation_id": str(uuid4()),
                "source_url": "https://example.com/jobs/1",
                "observed_at": observed_at.isoformat(),
            },
        ),
        ({"text": "结构投入增加", "basis_fact_ids": (fact_id,)},),
        ({"text": "招聘人数未知"},),
        {"结构": 4},
        "结构人才需求上升",
        uuid4(),
        uuid4(),
        "hr-bot",
        "gpt-5",
        created_at,
    )


class MemorySource:
    def __init__(self, insights=(), *, sources=None):
        self.insights = tuple(insights)
        self.sources = tuple(sources or (_source(),))
        self.relevant_calls = []
        self.source_page_calls = []
        self.recorded: PositionInsightRetrieval | None = None

    def list_sources_page(
        self,
        owner_id,
        *,
        include_inactive=False,
        before_created_at=None,
        before_source_id=None,
        limit=100,
    ):
        assert owner_id == OWNER
        assert include_inactive is False
        assert limit == 100
        self.source_page_calls.append((before_created_at, before_source_id))
        start = 0
        if before_source_id is not None:
            start = next(
                index + 1
                for index, source in enumerate(self.sources)
                if source.source_id == before_source_id
                and source.created_at == before_created_at
            )
        return self.sources[start : start + limit]

    def relevant_insights(self, owner_id, query, position_id, *, limit=5):
        self.relevant_calls.append((owner_id, query, position_id, limit))
        return self.insights[:limit]

    def retrieval_for_turn(self, owner_id, position_id, turn_id):
        if self.recorded is None:
            return None
        if (
            self.recorded.owner_id != owner_id
            or self.recorded.position_id != position_id
            or self.recorded.turn_id != turn_id
        ):
            return None
        return self.recorded

    def record_retrieval_for_turn(
        self,
        *,
        retrieval_id,
        owner_id,
        client_request_id,
        position_id,
        turn_id,
        insight_version_ids,
        query_sha256,
        retrieved_excerpts,
    ):
        candidate = PositionInsightRetrieval(
            retrieval_id,
            owner_id,
            client_request_id,
            position_id,
            CONVERSATION,
            turn_id,
            insight_version_ids,
            query_sha256,
            retrieved_excerpts,
            NOW,
        )
        if self.recorded is not None and self.recorded != candidate:
            raise PanoramaConflict("panorama retrieval conflict")
        self.recorded = candidate
        return candidate


def test_named_followed_company_retrieves_only_latest_version_and_records_turn() -> (
    None
):
    latest_id, older_id = uuid4(), uuid4()
    source = MemorySource(
        (
            _insight(latest_id, created_at=NOW),
            _insight(older_id, created_at=NOW - timedelta(days=1)),
        )
    )
    provider = PanoramaContextProvider(source, now=lambda: NOW)

    fragment = provider.for_turn(OWNER, POSITION, "参考联合光电修订这个岗位的 JR", TURN)

    assert fragment is not None
    assert fragment.insight_version_ids == (latest_id,)
    assert fragment.facts
    assert fragment.inferences
    assert fragment.unknowns
    assert fragment.source_urls == ("https://example.com/jobs/1",)
    assert source.recorded is not None
    assert source.recorded.turn_id == TURN
    assert source.recorded.insight_version_ids == (latest_id,)
    assert source.recorded.query_sha256 == fragment.query_sha256


def test_position_task_kind_selects_panorama_only_for_external_market_tasks() -> None:
    sourcing = PanoramaContextProvider(
        MemorySource((_insight(uuid4()),)), now=lambda: NOW
    ).for_turn(
        OWNER, POSITION, "生成搜寻策略", TURN, task_kind="sourcing_strategy"
    )
    interview = PanoramaContextProvider(
        MemorySource((_insight(uuid4()),)), now=lambda: NOW
    ).for_turn(
        OWNER, POSITION, "生成岗位面试方案", uuid4(),
        task_kind="position_interview_plan",
    )

    assert sourcing is not None
    assert interview is None


def test_named_company_filters_unrelated_facts_from_a_multi_company_insight() -> None:
    alpha_id, beta_id = uuid4(), uuid4()
    sources = (
        replace(
            _source(name="示例光学甲", aliases=()),
            source_id=alpha_id,
            approved_urls=("https://example.com/company-alpha",),
        ),
        replace(
            _source(name="示例光学乙", aliases=()),
            source_id=beta_id,
            approved_urls=("https://example.com/company-beta",),
        ),
    )
    insight = replace(
        _insight(uuid4(), source_id=alpha_id),
        selected_source_ids=(alpha_id, beta_id),
        facts=(
            {
                "fact_id": "alpha-fact",
                "text": "示例光学甲公开招聘高级结构工程师",
                "snapshot_id": str(uuid4()),
                "observation_id": str(uuid4()),
                "source_url": "https://example.com/company-alpha/jobs/1",
                "observed_at": NOW.isoformat(),
            },
            {
                "fact_id": "beta-fact",
                "text": "示例光学乙公开招聘算法工程师",
                "snapshot_id": str(uuid4()),
                "observation_id": str(uuid4()),
                "source_url": "https://example.com/company-beta/jobs/1",
                "observed_at": NOW.isoformat(),
            },
        ),
        inferences=(),
        unknowns=({"text": "示例光学乙的岗位地点仍待核验"},),
    )

    fragment = PanoramaContextProvider(
        MemorySource((insight,), sources=sources), now=lambda: NOW
    ).for_turn(OWNER, POSITION, "只参考示例光学甲最新招聘证据", TURN)

    assert fragment is not None
    assert [fact["fact_id"] for fact in fragment.facts] == ["alpha-fact"]
    assert fragment.source_urls == ("https://example.com/company-alpha/jobs/1",)
    assert fragment.unknowns == ()


@pytest.mark.parametrize(
    "query",
    (
        "参考 Union Optech 的岗位",
        "看看竞品的人才方向",
        "使用招聘情报更新建议",
        "参考全景分析",
        "比较外部岗位",
        "参考关注公司",
    ),
)
def test_alias_or_explicit_language_triggers_retrieval(query: str) -> None:
    source = MemorySource((_insight(uuid4()),))

    fragment = PanoramaContextProvider(source, now=lambda: NOW).for_turn(
        OWNER, POSITION, query, TURN
    )

    assert fragment is not None
    assert len(source.relevant_calls) == 1


def test_unrelated_question_returns_none_without_relevance_lookup_or_record() -> None:
    source = MemorySource((_insight(uuid4()),))

    fragment = PanoramaContextProvider(source, now=lambda: NOW).for_turn(
        OWNER, POSITION, "这个岗位的汇报对象是谁？", TURN
    )

    assert fragment is None
    assert source.relevant_calls == []
    assert len(source.source_page_calls) == 1
    assert source.recorded is None


def test_stale_last_valid_data_carries_explicit_age_warning() -> None:
    observed_at = NOW - timedelta(days=45, hours=3)
    source = MemorySource((_insight(uuid4(), observed_at=observed_at),))

    fragment = PanoramaContextProvider(source, now=lambda: NOW).for_turn(
        OWNER, POSITION, "参考全景分析修订 JR", TURN
    )

    assert fragment is not None
    assert fragment.stale_age_days == 45
    document = fragment.as_prompt_document()
    assert document["freshness"]["status"] == "stale_last_valid"
    assert document["freshness"]["age_days"] == 45
    assert "45" in document["freshness"]["warning"]


def _insight_with_facts(facts) -> TalentInsightVersion:
    return TalentInsightVersion(
        uuid4(),
        OWNER,
        uuid4(),
        uuid4(),
        1,
        (SOURCE,),
        tuple(uuid4() for _ in facts),
        tuple(facts),
        (),
        (),
        {"结构": 1},
        "结构方向",
        uuid4(),
        uuid4(),
        "hr-bot",
        "gpt-5",
        NOW,
    )


def _fact(index: int, observed_at: datetime, *, text="公开事实"):
    return {
        "fact_id": f"f{index}",
        "text": text,
        "snapshot_id": str(uuid4()),
        "observation_id": str(uuid4()),
        "source_url": f"https://example.com/jobs/{index}",
        "observed_at": observed_at.isoformat(),
    }


def test_mixed_fresh_and_stale_included_facts_warn_with_oldest_age() -> None:
    insight = _insight_with_facts((_fact(1, NOW), _fact(2, NOW - timedelta(days=60))))

    fragment = PanoramaContextProvider(
        MemorySource((insight,)), now=lambda: NOW
    ).for_turn(OWNER, POSITION, "参考全景分析", TURN)

    assert fragment is not None
    assert fragment.stale_age_days == 60
    assert (
        "oldest included evidence is 60 days old"
        in fragment.as_prompt_document()["freshness"]["warning"]
    )


def test_stale_fact_trimmed_by_budget_does_not_make_retained_context_stale() -> None:
    fresh_facts = tuple(
        _fact(index, NOW, text="新事实" * 1000) for index in range(1, 21)
    )
    omitted_old = _fact(999, NOW - timedelta(days=90), text="旧事实" * 1000)
    insight = _insight_with_facts((*fresh_facts, omitted_old))

    fragment = PanoramaContextProvider(
        MemorySource((insight,)), now=lambda: NOW
    ).for_turn(OWNER, POSITION, "参考全景分析", TURN)

    assert fragment is not None
    assert all(item["fact_id"] != "f999" for item in fragment.facts)
    assert fragment.stale_age_days is None


def test_context_is_limited_to_five_versions_and_32_kib_with_valid_boundaries() -> None:
    insights = tuple(
        _insight(
            uuid4(),
            text=(f"事实-{index}-" + "结论" * 3500),
            source_id=uuid4(),
        )
        for index in range(6)
    )
    source = MemorySource(insights)

    fragment = PanoramaContextProvider(source, now=lambda: NOW).for_turn(
        OWNER, POSITION, "参考全景分析修订岗位", TURN
    )

    assert fragment is not None
    assert len(fragment.insight_version_ids) <= 5
    rendered = json.dumps(
        fragment.as_prompt_document(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(rendered) <= MAX_PANORAMA_CONTEXT_BYTES
    included_fact_keys = {
        (item["insight_version_id"], item["fact_id"]) for item in fragment.facts
    }
    assert all(
        all(
            (item["insight_version_id"], fact_id) in included_fact_keys
            for fact_id in item["basis_fact_ids"]
        )
        for item in fragment.inferences
    )


def test_prompt_separates_derived_inference_provenance_and_unverified_unknowns() -> (
    None
):
    insight = _insight(uuid4(), observed_at=NOW - timedelta(days=2))

    fragment = PanoramaContextProvider(
        MemorySource((insight,)), now=lambda: NOW
    ).for_turn(OWNER, POSITION, "参考全景分析", TURN)

    assert fragment is not None
    assert fragment.inferences[0]["basis_sources"] == (
        {
            "source_url": "https://example.com/jobs/1",
            "observed_at": (NOW - timedelta(days=2)).isoformat(),
        },
    )
    assert fragment.unknowns[0]["source_urls"] == ()
    assert fragment.unknowns[0]["evidence_status"] == "unverified"
    assert fragment.unknowns[0]["as_of"] == insight.created_at.isoformat()


def test_cross_owner_or_position_lookup_fails_closed() -> None:
    class HiddenSource(MemorySource):
        def relevant_insights(self, owner_id, query, position_id, *, limit=5):
            raise PanoramaNotFound("panorama position not found")

    provider = PanoramaContextProvider(HiddenSource(), now=lambda: NOW)

    with pytest.raises(PanoramaContextError):
        provider.for_turn(OWNER, POSITION, "参考全景分析", TURN)


def test_exact_turn_replay_reuses_recorded_ids_and_different_query_conflicts() -> None:
    insight_id = uuid4()
    source = MemorySource((_insight(insight_id),))
    provider = PanoramaContextProvider(source, now=lambda: NOW)

    first = provider.for_turn(OWNER, POSITION, "参考全景分析", TURN)
    source.insights = (_insight(uuid4(), created_at=NOW + timedelta(days=1)),)
    replay = provider.for_turn(OWNER, POSITION, "参考全景分析", TURN)

    assert replay == first
    assert replay is not None
    assert replay.insight_version_ids == (insight_id,)
    assert len(source.relevant_calls) == 1
    with pytest.raises(PanoramaConflict):
        provider.for_turn(OWNER, POSITION, "参考竞品招聘情报", TURN)


def test_exact_turn_raw_query_hash_conflicts_on_whitespace_only_change() -> None:
    source = MemorySource((_insight(uuid4()),))
    provider = PanoramaContextProvider(source, now=lambda: NOW)
    provider.for_turn(OWNER, POSITION, "参考全景分析", TURN)

    with pytest.raises(PanoramaConflict, match="query conflict"):
        provider.for_turn(OWNER, POSITION, " 参考全景分析 ", TURN)


def test_concurrent_same_query_create_conflict_replays_exact_winner() -> None:
    class RacingSource(MemorySource):
        def record_retrieval_for_turn(self, **values):
            super().record_retrieval_for_turn(**values)
            raise PanoramaConflict("concurrent winner committed")

    insight_id = uuid4()
    source = RacingSource((_insight(insight_id),))

    fragment = PanoramaContextProvider(source, now=lambda: NOW).for_turn(
        OWNER, POSITION, "参考全景分析", TURN
    )

    assert fragment is not None
    assert fragment.insight_version_ids == (insight_id,)


def test_replayed_excerpt_rejects_non_https_citation() -> None:
    source = MemorySource((_insight(uuid4()),))
    provider = PanoramaContextProvider(source, now=lambda: NOW)
    provider.for_turn(OWNER, POSITION, "参考全景分析", TURN)
    assert source.recorded is not None
    document = dict(source.recorded.retrieved_excerpts[0])
    document["source_urls"] = ["http://example.com/jobs/1"]
    source.recorded = PositionInsightRetrieval(
        source.recorded.retrieval_id,
        source.recorded.owner_id,
        source.recorded.client_request_id,
        source.recorded.position_id,
        source.recorded.conversation_id,
        source.recorded.turn_id,
        source.recorded.insight_version_ids,
        source.recorded.query_sha256,
        (document,),
        source.recorded.created_at,
    )

    with pytest.raises(PanoramaContextError):
        provider.for_turn(OWNER, POSITION, "参考全景分析", TURN)


@pytest.mark.parametrize(
    "mutate",
    (
        lambda document: document["facts"][0].update(
            {"source_url": "http://example.com/jobs/1"}
        ),
        lambda document: document["facts"][0].update(
            {"source_url": "https://other.example.com/jobs/1"}
        ),
        lambda document: document["facts"][0].update(
            {"observed_at": "2026-09-05T08:00:00"}
        ),
    ),
)
def test_replay_revalidates_each_fact_citation_and_observed_time(mutate) -> None:
    source = MemorySource((_insight(uuid4()),))
    provider = PanoramaContextProvider(source, now=lambda: NOW)
    provider.for_turn(OWNER, POSITION, "参考全景分析", TURN)
    assert source.recorded is not None
    document = thaw_json(source.recorded.retrieved_excerpts[0])
    mutate(document)
    source.recorded = PositionInsightRetrieval(
        source.recorded.retrieval_id,
        source.recorded.owner_id,
        source.recorded.client_request_id,
        source.recorded.position_id,
        source.recorded.conversation_id,
        source.recorded.turn_id,
        source.recorded.insight_version_ids,
        source.recorded.query_sha256,
        (document,),
        source.recorded.created_at,
    )

    with pytest.raises(PanoramaContextError):
        provider.for_turn(OWNER, POSITION, "参考全景分析", TURN)


def test_named_company_trigger_scans_beyond_first_source_page() -> None:
    sources = tuple(
        TalentSource(
            uuid4(),
            OWNER,
            uuid4(),
            "company",
            f"company-{index:03d}",
            "目标公司" if index == 100 else f"公司{index}",
            ("Target 101",) if index == 100 else (),
            (
                "https://example.com/jobs"
                if index == 100
                else f"https://example.com/jobs/{index}",
            ),
            True,
            NOW - timedelta(seconds=index),
            NOW - timedelta(seconds=index),
        )
        for index in range(101)
    )
    source = MemorySource((_insight(uuid4()),), sources=sources)

    fragment = PanoramaContextProvider(source, now=lambda: NOW).for_turn(
        OWNER, POSITION, "参考 Target 101 的岗位", TURN
    )

    assert fragment is not None
    assert len(source.source_page_calls) == 2


def test_prompt_states_evidence_and_draft_confirmation_boundaries() -> None:
    source = MemorySource((_insight(uuid4()),))

    fragment = PanoramaContextProvider(source, now=lambda: NOW).for_turn(
        OWNER, POSITION, "参考全景分析修订 JD", TURN
    )

    assert fragment is not None
    boundary = fragment.as_prompt_document()["usage_boundary"]
    assert boundary == {
        "facts": "may_be_cited_with_https_source",
        "inferences": "must_be_explicitly_labelled_as_ai_inference",
        "unknowns": "must_remain_unknown_not_negative_fact",
        "position_changes": "draft_only_until_user_confirmation",
        "automatic_position_write": "forbidden",
    }
