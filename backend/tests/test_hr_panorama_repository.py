# ruff: noqa: F401, F811
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest
from test_control_plane_migration import control_database

from app.hr.panorama_models import (
    CreatePanoramaRun,
    CreatePositionInsightRetrieval,
    CreatePublicJobSnapshot,
    CreateTalentInsightVersion,
    CreateTalentSource,
    TransitionPanoramaRun,
)
from app.hr.panorama_repository import (
    PanoramaConflict,
    PanoramaNotFound,
    PanoramaRepository,
)

NOW = datetime(2026, 9, 5, 8, tzinfo=UTC)


def _owner_scope(connection, name):
    owner_id, conversation_id, turn_id = uuid4(), uuid4(), uuid4()
    user_message_id, assistant_message_id, position_id = uuid4(), uuid4(), uuid4()
    connection.execute(
        "insert into platform_control.internal_users("
        "internal_user_id,display_name,status) values (%s,%s,'active')",
        (owner_id, name),
    )
    connection.execute(
        "insert into platform_control.conversations("
        "conversation_id,owner_internal_user_id,started_by_client_request_id,"
        "mode,direct_agent_id,title,status) values ("
        "%s,%s,%s,'direct_agent','hr-bot','全景分析','active')",
        (conversation_id, owner_id, uuid4()),
    )
    connection.execute(
        "insert into platform_control.conversation_messages("
        "message_id,conversation_id,seq,role,content_ciphertext,"
        "encryption_key_version,turn_id,delivery_status,completed_at) values ("
        "%s,%s,1,'user',%s,1,%s,'completed',now()),("
        "%s,%s,2,'assistant',%s,1,%s,'completed',now())",
        (
            user_message_id,
            conversation_id,
            b"u" * 29,
            turn_id,
            assistant_message_id,
            conversation_id,
            b"a" * 29,
            turn_id,
        ),
    )
    connection.execute(
        "insert into platform_control.conversation_turns("
        "turn_id,conversation_id,user_message_id,assistant_message_id,"
        "client_request_id,status) values (%s,%s,%s,%s,%s,'completed')",
        (turn_id, conversation_id, user_message_id, assistant_message_id, uuid4()),
    )
    connection.execute(
        "insert into platform_hr.positions("
        "position_id,owner_internal_user_id,client_request_id,source_kind,title) "
        "values (%s,%s,%s,'manual','高级结构工程师')",
        (position_id, owner_id, uuid4()),
    )
    connection.execute(
        "insert into platform_hr.position_conversations("
        "conversation_id,owner_internal_user_id,position_id,client_request_id,"
        "binding_kind) values (%s,%s,%s,%s,'created_in_position')",
        (conversation_id, owner_id, position_id, uuid4()),
    )
    connection.commit()
    return owner_id, conversation_id, turn_id, position_id


def _source(owner_id, request_id, name="联合光电") -> CreateTalentSource:
    return CreateTalentSource(
        source_id=uuid4(),
        owner_id=owner_id,
        client_request_id=request_id,
        company_key=f"company-{request_id.hex}",
        canonical_name=name,
        aliases=("Union Optech",),
        approved_urls=("https://example.com/jobs",),
        active=True,
    )


@pytest.mark.postgres
def test_repository_reuses_v79_functions_and_preserves_owner_idempotency(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        owner_id, _, _, _ = _owner_scope(admin, "Repository Owner")
        other_id, _, _, _ = _owner_scope(admin, "Other Repository Owner")
    repository = PanoramaRepository(environment["urls"]["platform_control_app"])
    command = _source(owner_id, uuid4())

    first = repository.create_source(command)
    replay = repository.create_source(command)
    repository.create_source(_source(other_id, uuid4(), "另一家公司"))

    assert replay == first
    assert repository.list_sources(owner_id) == (first,)
    assert repository.list_sources(other_id)[0].owner_id == other_id
    with pytest.raises(PanoramaConflict):
        repository.create_source(replace_source(command, canonical_name="篡改名称"))


def replace_source(command, **changes):
    values = {
        field: getattr(command, field)
        for field in (
            "source_id",
            "owner_id",
            "client_request_id",
            "company_key",
            "canonical_name",
            "aliases",
            "approved_urls",
            "active",
        )
    }
    values.update(changes)
    return CreateTalentSource(**values)


def test_runtime_context_and_claim_use_only_dedicated_point_functions() -> None:
    calls = []

    class Cursor:
        def fetchall(self):
            return []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, sql, parameters):
            calls.append((sql, parameters))
            return Cursor()

    repository = PanoramaRepository(
        "postgresql://unused", connect=lambda *args, **kwargs: Connection()
    )
    run_id = uuid4()

    assert repository.runtime_context(run_id) is None
    assert repository.claim_next_runtime(claim_seconds=7) is None

    assert "read_panorama_run_runtime_v79" in calls[0][0]
    assert calls[0][1] == (run_id,)
    assert "claim_next_panorama_run_v79" in calls[1][0]
    assert calls[1][1] == (7,)
    assert all("from platform_hr.panorama_runs" not in sql for sql, _ in calls)


@pytest.mark.postgres
def test_repository_creates_runs_snapshots_reports_and_ranks_deterministically(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        owner_id, conversation_id, turn_id, position_id = _owner_scope(
            admin, "Ranking Owner"
        )
        other_id, _, _, other_position_id = _owner_scope(admin, "Hidden Owner")
    repository = PanoramaRepository(environment["urls"]["platform_control_app"])
    source = repository.create_source(_source(owner_id, uuid4()))
    run = repository.create_run(
        CreatePanoramaRun(
            run_id=uuid4(),
            owner_id=owner_id,
            client_request_id=uuid4(),
            selected_source_ids=(source.source_id,),
            conversation_id=conversation_id,
        )
    )
    running = repository.transition_run(
        TransitionPanoramaRun(
            owner_id=owner_id,
            run_id=run.run_id,
            client_request_id=uuid4(),
            expected_row_version=run.row_version,
            state="running",
            error_code=None,
            source_failures={},
        )
    )
    observation_id = uuid4()
    snapshot = repository.create_snapshot(
        CreatePublicJobSnapshot(
            snapshot_id=uuid4(),
            owner_id=owner_id,
            client_request_id=observation_id,
            run_id=run.run_id,
            source_id=source.source_id,
            public_job_key="job-1",
            title="结构工程师",
            location="中山",
            duty_excerpt="负责精密结构设计",
            requirement_excerpt="五年以上经验",
            source_url="https://example.com/jobs/1",
            observed_at=NOW,
            content_sha256="a" * 64,
            status="open",
        )
    )
    insight = repository.create_insight(
        CreateTalentInsightVersion(
            insight_version_id=uuid4(),
            owner_id=owner_id,
            client_request_id=uuid4(),
            run_id=run.run_id,
            selected_source_ids=(source.source_id,),
            snapshot_ids=(snapshot.snapshot_id,),
            facts=(
                {
                    "fact_id": "f1",
                    "text": "公开招聘结构工程师",
                    "snapshot_id": str(snapshot.snapshot_id),
                    "observation_id": str(observation_id),
                    "source_url": snapshot.source_url,
                    "observed_at": "2026-09-05T08:00:00Z",
                },
            ),
            inferences=({"text": "结构投入增加", "basis_fact_ids": ("f1",)},),
            unknowns=({"text": "招聘人数未知"},),
            direction_clusters={"结构": 4},
            summary="结构人才需求上升",
            source_conversation_id=conversation_id,
            source_turn_id=turn_id,
            agent_id="hr-bot",
            model_version="gpt-5",
        )
    )
    retrieval = CreatePositionInsightRetrieval(
        retrieval_id=uuid4(),
        owner_id=owner_id,
        client_request_id=uuid4(),
        position_id=position_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        insight_version_ids=(insight.insight_version_id,),
        query_sha256="b" * 64,
        retrieved_excerpts=({"text": "结构人才需求上升"},),
    )
    first_retrieval = repository.create_retrieval(retrieval)

    assert repository.run(owner_id, run.run_id) == running
    assert (
        repository.report(owner_id, insight.insight_version_id).summary
        == insight.summary
    )
    assert repository.create_retrieval(retrieval) == first_retrieval
    assert repository.list_retrievals(owner_id, position_id) == (first_retrieval,)
    with pytest.raises(PanoramaConflict):
        repository.create_retrieval(
            replace(
                retrieval,
                retrieved_excerpts=({"text": "被篡改的摘要"},),
            )
        )
    assert repository.relevant_insights(
        owner_id, "参考联合光电的结构招聘", position_id
    ) == (insight,)
    with pytest.raises(PanoramaNotFound):
        repository.relevant_insights(other_id, "参考联合光电", position_id)
    with pytest.raises(PanoramaNotFound):
        repository.relevant_insights(owner_id, "参考联合光电", other_position_id)


@pytest.mark.postgres
def test_ranking_order_is_company_then_direction_then_position_freshness_and_id(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        owner_id, _, _, position_id = _owner_scope(admin, "Ranking Contract Owner")
    repository = PanoramaRepository(environment["urls"]["platform_control_app"])

    company = _ranking_insight(uuid4(), "联合光电", {"光学": 1}, "其他", NOW)
    direction = _ranking_insight(uuid4(), "另一家公司", {"结构": 1}, "其他", NOW)
    position = _ranking_insight(
        uuid4(), "第三家公司", {"软件": 1}, "高级结构工程师", NOW
    )
    sources = {
        company.selected_source_ids[0]: _source_record(
            company.selected_source_ids[0], "联合光电"
        ),
        direction.selected_source_ids[0]: _source_record(
            direction.selected_source_ids[0], "另一家公司"
        ),
        position.selected_source_ids[0]: _source_record(
            position.selected_source_ids[0], "第三家公司"
        ),
    }
    repository._ranking_candidates = lambda owner: (position, direction, company)  # type: ignore[attr-defined]
    repository._sources_for_ranking = lambda owner, ids: sources  # type: ignore[attr-defined]

    ranked = repository.relevant_insights(
        owner_id, "参考联合光电的结构方向", position_id, limit=3
    )

    assert ranked == (company, direction, position)


def test_each_ranking_tie_breaker_is_applied_only_after_preceding_signals() -> None:
    owner_id, position_id = uuid4(), uuid4()
    repository = PanoramaRepository("postgresql://unused")
    repository._position_terms = lambda owner, position: (  # type: ignore[method-assign]
        "高级结构工程师",
        "机械",
    )

    def rank_pair(query, first, second, first_source, second_source):
        repository._ranking_candidates = lambda owner: (second, first)  # type: ignore[method-assign]
        repository._sources_for_ranking = lambda owner, ids: {  # type: ignore[method-assign]
            first_source.source_id: first_source,
            second_source.source_id: second_source,
        }
        return repository.relevant_insights(owner_id, query, position_id, limit=2)

    baseline_time = NOW
    canonical = _ranking_insight(
        UUID(int=10), "unused", {"其他": 1}, "无关", baseline_time
    )
    other = _ranking_insight(UUID(int=11), "unused", {"其他": 1}, "无关", baseline_time)
    assert (
        rank_pair(
            "参考联合光电",
            canonical,
            other,
            _source_record(canonical.selected_source_ids[0], "联合光电"),
            _source_record(other.selected_source_ids[0], "另一家公司"),
        )[0]
        == canonical
    )

    alias = _ranking_insight(UUID(int=12), "unused", {"其他": 1}, "无关", baseline_time)
    no_alias = _ranking_insight(
        UUID(int=13), "unused", {"其他": 1}, "无关", baseline_time
    )
    assert (
        rank_pair(
            "参考 Union Optech",
            alias,
            no_alias,
            _source_record(
                alias.selected_source_ids[0], "第一家公司", ("Union Optech",)
            ),
            _source_record(no_alias.selected_source_ids[0], "第二家公司"),
        )[0]
        == alias
    )

    direction = _ranking_insight(
        UUID(int=14), "unused", {"结构": 1}, "无关", baseline_time
    )
    no_direction = _ranking_insight(
        UUID(int=15), "unused", {"软件": 1}, "无关", baseline_time
    )
    assert (
        rank_pair(
            "分析结构方向",
            direction,
            no_direction,
            _source_record(direction.selected_source_ids[0], "第一家公司"),
            _source_record(no_direction.selected_source_ids[0], "第二家公司"),
        )[0]
        == direction
    )

    category = _ranking_insight(
        UUID(int=16), "unused", {"其他": 1}, "机械人才", baseline_time
    )
    no_category = _ranking_insight(
        UUID(int=17), "unused", {"其他": 1}, "无关", baseline_time
    )
    assert (
        rank_pair(
            "行业参考",
            category,
            no_category,
            _source_record(category.selected_source_ids[0], "第一家公司"),
            _source_record(no_category.selected_source_ids[0], "第二家公司"),
        )[0]
        == category
    )

    fresh = _ranking_insight(
        UUID(int=18),
        "unused",
        {"其他": 1},
        "无关",
        baseline_time + timedelta(seconds=1),
    )
    stale = _ranking_insight(UUID(int=19), "unused", {"其他": 1}, "无关", baseline_time)
    assert (
        rank_pair(
            "行业参考",
            fresh,
            stale,
            _source_record(fresh.selected_source_ids[0], "第一家公司"),
            _source_record(stale.selected_source_ids[0], "第二家公司"),
        )[0]
        == fresh
    )

    low_id = _ranking_insight(UUID(int=1), "unused", {"其他": 1}, "无关", baseline_time)
    high_id = _ranking_insight(
        UUID(int=2), "unused", {"其他": 1}, "无关", baseline_time
    )
    assert (
        rank_pair(
            "行业参考",
            low_id,
            high_id,
            _source_record(low_id.selected_source_ids[0], "第一家公司"),
            _source_record(high_id.selected_source_ids[0], "第二家公司"),
        )[0]
        == low_id
    )


def _ranking_insight(insight_id, company_name, clusters, summary, created_at):
    source_id = uuid4()
    insight = CreateTalentInsightVersion(
        insight_version_id=insight_id,
        owner_id=uuid4(),
        client_request_id=uuid4(),
        run_id=uuid4(),
        selected_source_ids=(source_id,),
        snapshot_ids=(uuid4(),),
        facts=(
            {
                "fact_id": "f",
                "text": summary,
                "snapshot_id": str(uuid4()),
                "observation_id": str(uuid4()),
                "source_url": "https://example.com/jobs",
                "observed_at": "2026-09-05T08:00:00Z",
            },
        ),
        inferences=(),
        unknowns=({"text": "unknown"},),
        direction_clusters=clusters,
        summary=summary,
        source_conversation_id=uuid4(),
        source_turn_id=uuid4(),
        agent_id="hr-bot",
        model_version="gpt-5",
    )
    record = insight.as_version(version_number=1, created_at=created_at)
    return record


def _source_record(source_id, name, aliases=()):
    from app.hr.panorama_models import TalentSource

    return TalentSource(
        source_id,
        uuid4(),
        uuid4(),
        "company",
        f"company-{source_id.hex}",
        name,
        aliases,
        ("https://example.com/jobs",),
        True,
        NOW,
        NOW,
    )
