# ruff: noqa: C408
from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from types import MappingProxyType
from uuid import uuid4

import pytest

from app.hr.panorama_models import (
    CreatePanoramaRun,
    CreatePositionInsightRetrieval,
    CreatePublicJobSnapshot,
    CreateTalentInsightVersion,
    CreateTalentSource,
    PanoramaReport,
    PanoramaRun,
    TalentInsightVersion,
    TalentSource,
    TransitionPanoramaRun,
)

NOW = datetime(2026, 9, 5, 8, tzinfo=UTC)


def _source(**changes) -> TalentSource:
    values = dict(
        source_id=uuid4(),
        owner_id=uuid4(),
        client_request_id=uuid4(),
        source_kind="company",
        company_key="union-optech",
        canonical_name="联合光电",
        aliases=("Union Optech",),
        approved_urls=("https://www.union-optech.com/jobs",),
        active=True,
        created_at=NOW,
        updated_at=NOW,
    )
    values.update(changes)
    return TalentSource(**values)


def _insight(**changes) -> TalentInsightVersion:
    snapshot_id, observation_id = uuid4(), uuid4()
    values = dict(
        insight_version_id=uuid4(),
        owner_id=uuid4(),
        client_request_id=uuid4(),
        run_id=uuid4(),
        version_number=1,
        selected_source_ids=(uuid4(),),
        snapshot_ids=(snapshot_id,),
        facts=(
            {
                "fact_id": "fact-1",
                "text": "公开招聘结构工程师",
                "snapshot_id": str(snapshot_id),
                "observation_id": str(observation_id),
                "source_url": "https://example.com/jobs/1",
                "observed_at": "2026-09-05T08:00:00Z",
            },
        ),
        inferences=({"text": "结构投入增加", "basis_fact_ids": ("fact-1",)},),
        unknowns=({"text": "招聘人数未知"},),
        direction_clusters={"结构": 4},
        summary="结构人才需求上升",
        source_conversation_id=uuid4(),
        source_turn_id=uuid4(),
        agent_id="hr-bot",
        model_version="gpt-5",
        created_at=NOW,
    )
    values.update(changes)
    return TalentInsightVersion(**values)


def test_panorama_records_are_normalized_bounded_and_deeply_immutable() -> None:
    owner_id = uuid4()
    source = _source(
        owner_id=owner_id,
        canonical_name="  联合光电  ",
        aliases=(" Union Optech ",),
    )
    insight = _insight(owner_id=owner_id, selected_source_ids=(source.source_id,))
    report = PanoramaReport(insight=insight, sources=(source,), snapshots=())

    assert source.canonical_name == "联合光电"
    assert source.aliases == ("Union Optech",)
    assert report.summary == insight.summary
    assert report.direction_clusters == {"结构": 4}
    assert isinstance(insight.direction_clusters, MappingProxyType)
    assert isinstance(insight.facts[0], MappingProxyType)
    assert insight.inferences[0]["basis_fact_ids"] == ("fact-1",)
    with pytest.raises(TypeError):
        insight.direction_clusters["结构"] = 5  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        source.active = False  # type: ignore[misc]
    with pytest.raises(ValueError, match="timestamp invalid"):
        _source(created_at=None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="timestamp invalid"):
        _insight(created_at=None)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "changes",
    (
        {"canonical_name": "   "},
        {"source_kind": "school"},
        {"approved_urls": ("http://example.com/jobs",)},
        {"approved_urls": ("https://Example.com/jobs",)},
        {"approved_urls": ("https://user:pass@example.com/jobs",)},
        {"approved_urls": tuple(f"https://example.com/jobs/{i}" for i in range(21))},
    ),
)
def test_talent_source_rejects_ambiguous_names_arbitrary_kinds_and_unsafe_urls(
    changes,
) -> None:
    with pytest.raises(ValueError):
        _source(**changes)


def test_run_snapshot_and_insight_commands_match_the_v79_bounds() -> None:
    owner_id, source_id, run_id = uuid4(), uuid4(), uuid4()
    run = CreatePanoramaRun(
        run_id=run_id,
        owner_id=owner_id,
        client_request_id=uuid4(),
        selected_source_ids=(source_id,),
        conversation_id=uuid4(),
    )
    transition = TransitionPanoramaRun(
        owner_id=owner_id,
        run_id=run_id,
        client_request_id=uuid4(),
        expected_row_version=1,
        state="running",
        error_code=None,
        source_failures={},
    )
    snapshot = CreatePublicJobSnapshot(
        snapshot_id=uuid4(),
        owner_id=owner_id,
        client_request_id=uuid4(),
        run_id=run_id,
        source_id=source_id,
        public_job_key="job-1",
        title="结构工程师",
        location="中山",
        duty_excerpt="结构设计",
        requirement_excerpt="五年经验",
        source_url="https://example.com/jobs/1",
        observed_at=NOW,
        content_sha256="a" * 64,
        status="open",
    )
    insight = CreateTalentInsightVersion(
        insight_version_id=uuid4(),
        owner_id=owner_id,
        client_request_id=uuid4(),
        run_id=run_id,
        selected_source_ids=(source_id,),
        snapshot_ids=(snapshot.snapshot_id,),
        facts=(
            {
                "fact_id": "f1",
                "text": "招聘结构工程师",
                "snapshot_id": str(snapshot.snapshot_id),
                "observation_id": str(snapshot.client_request_id),
                "source_url": snapshot.source_url,
                "observed_at": "2026-09-05T08:00:00Z",
            },
        ),
        inferences=({"text": "结构投入增加", "basis_fact_ids": ("f1",)},),
        unknowns=({"text": "人数未知"},),
        direction_clusters={"结构": 4},
        summary="结构人才需求上升",
        source_conversation_id=uuid4(),
        source_turn_id=uuid4(),
        agent_id="hr-bot",
        model_version="gpt-5",
    )
    retrieval = CreatePositionInsightRetrieval(
        retrieval_id=uuid4(),
        owner_id=owner_id,
        client_request_id=uuid4(),
        position_id=uuid4(),
        conversation_id=uuid4(),
        turn_id=uuid4(),
        insight_version_ids=(insight.insight_version_id,),
        query_sha256="b" * 64,
        retrieved_excerpts=({"text": "结构人才需求上升"},),
    )

    assert run.selected_source_ids == (source_id,)
    assert transition.source_failures == {}
    assert snapshot.status == "open"
    assert insight.direction_clusters == {"结构": 4}
    assert retrieval.insight_version_ids == (insight.insight_version_id,)
    with pytest.raises(ValueError, match="source selection invalid"):
        replace(run, selected_source_ids=())
    with pytest.raises(ValueError, match="source URL invalid"):
        replace(snapshot, source_url="http://example.com/jobs/1")
    with pytest.raises(ValueError, match="timestamp invalid"):
        replace(snapshot, observed_at=None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="insight facts invalid"):
        replace(insight, facts=())


def test_run_state_timestamps_and_source_failures_are_coherent() -> None:
    values = dict(
        run_id=uuid4(),
        owner_id=uuid4(),
        client_request_id=uuid4(),
        selected_source_ids=(uuid4(),),
        conversation_id=uuid4(),
        state="queued",
        error_code=None,
        source_failures={},
        row_version=1,
        started_at=None,
        finished_at=None,
        created_at=NOW,
        updated_at=NOW,
    )
    assert PanoramaRun(**values).state == "queued"
    with pytest.raises(ValueError, match="run state invalid"):
        PanoramaRun(**(values | {"state": "completed"}))
    with pytest.raises(ValueError, match="source failures invalid"):
        PanoramaRun(**(values | {"source_failures": {uuid4(): "failed"}}))


def test_snapshot_and_source_commands_are_not_satisfied_by_strings_for_ids() -> None:
    with pytest.raises(ValueError, match="identifiers invalid"):
        CreateTalentSource(
            source_id="not-a-uuid",  # type: ignore[arg-type]
            owner_id=uuid4(),
            client_request_id=uuid4(),
            company_key="company",
            canonical_name="Company",
            aliases=(),
            approved_urls=("https://example.com/jobs",),
            active=True,
        )
