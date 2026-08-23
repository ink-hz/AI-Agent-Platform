from __future__ import annotations

import json
from uuid import uuid4

import psycopg
import pytest

from app.agent_brain import conversation_context
from app.agent_brain.conversation_context import ConversationContextBuilder
from app.agent_brain.conversation_projection import ConversationProjection
from app.agent_brain.conversation_repository import ConversationRepository
from app.agent_brain.models import load_capability_cards
from app.agent_brain.orchestrator import MissionOrchestrator
from app.agent_brain.repository import MissionRepository
from test_agent_brain_orchestrator import ScriptedRelay, _codec, brain_database
from test_agent_brain_conversation_context import _complete_mission
from test_control_plane_migration import control_database


def test_summary_protocol_accepts_only_exact_selected_sequence() -> None:
    parse = conversation_context.parse_summary_result

    result = parse(
        json.dumps(
            {"summary": "已确认目标岗位和搜索方向。", "through_seq": 8},
            ensure_ascii=False,
        ),
        expected_through_seq=8,
    )

    assert result.summary == "已确认目标岗位和搜索方向。"
    assert result.through_seq == 8
    for invalid in (
        '{"summary":"内容","through_seq":7}',
        '{"summary":"内容","through_seq":8,"extra":true}',
        '{"summary":"","through_seq":8}',
        json.dumps({"summary": "A" * (32 * 1024 + 1), "through_seq": 8}),
        "not json",
    ):
        with pytest.raises(conversation_context.ConversationSummaryProtocolError):
            parse(invalid, expected_through_seq=8)


@pytest.mark.postgres
def test_over_budget_follow_up_runs_summary_before_planning(
    brain_database,
) -> None:
    environment, owner_id = brain_database
    missions = MissionRepository(
        environment["urls"]["platform_control_app"], content_codec=_codec()
    )
    conversations = ConversationRepository(
        environment["urls"]["platform_control_app"],
        content_codec=missions.content_codec,
        mission_repository=missions,
    )
    relay = ScriptedRelay()
    service = MissionOrchestrator(
        missions,
        relay,
        capability_provider=lambda _owner: load_capability_cards(),
        conversation_context_builder=ConversationContextBuilder(conversations),
        conversation_projection=ConversationProjection(conversations),
    )
    first = conversations.start(owner_id, uuid4(), "A" * 30_000)
    assert service.advance_pending(limit=50) == 1
    first_run = next(iter(relay.payloads))
    relay.terminal(
        first_run,
        "completed",
        json.dumps(
            {
                "kind": "direct",
                "answer": "B" * 8_000,
                "agent_id": None,
                "objective": None,
                "rationale_summary": "直接回答",
            },
            ensure_ascii=False,
        ),
    )
    assert service.advance_pending(limit=50) == 1
    second = conversations.append_turn(
        owner_id,
        first.conversation.conversation_id,
        uuid4(),
        "C" * 30_000,
    )

    assert service.advance_pending(limit=50) == 1
    with psycopg.connect(environment["admin"]) as connection:
        phases = connection.execute(
            "select run_id,phase from platform_control.mission_runs "
            "where mission_id=%s order by created_at,run_id",
            (second.mission.mission_id,),
        ).fetchall()
    assert [phase for _run_id, phase in phases] == ["summary"]
    summary_run = phases[0][0]
    summary_envelope = json.loads(relay.payloads[summary_run].prompt.split("\n", 1)[1])
    assert summary_envelope["through_seq"] == 2
    assert summary_envelope["conversation_messages"][-1]["role"] == "assistant"
    assert "C" * 100 not in relay.payloads[summary_run].prompt

    relay.terminal(
        summary_run,
        "completed",
        json.dumps(
            {"summary": "第一轮已确认方向。", "through_seq": 2},
            ensure_ascii=False,
        ),
    )
    assert service.advance_pending(limit=50) == 1
    assert service.advance_pending(limit=50) == 1

    with psycopg.connect(environment["admin"]) as connection:
        planning_run = connection.execute(
            "select run_id from platform_control.mission_runs "
            "where mission_id=%s and phase='planning'",
            (second.mission.mission_id,),
        ).fetchone()[0]
    planning_envelope = json.loads(
        relay.payloads[planning_run].prompt.split("\n", 1)[1]
    )
    assert planning_envelope["conversation_summary"] == "第一轮已确认方向。"
    assert planning_envelope["conversation_messages"] == [
        {"role": "user", "content": "C" * 30_000}
    ]


@pytest.mark.postgres
def test_invalid_summary_keeps_previous_summary_and_writes_visible_failure(
    brain_database,
) -> None:
    environment, owner_id = brain_database
    missions = MissionRepository(
        environment["urls"]["platform_control_app"], content_codec=_codec()
    )
    conversations = ConversationRepository(
        environment["urls"]["platform_control_app"],
        content_codec=missions.content_codec,
        mission_repository=missions,
    )
    projection = ConversationProjection(conversations)
    first = conversations.start(owner_id, uuid4(), "A" * 30_000)
    _complete_mission(environment, conversations, first.mission.mission_id, "B" * 8_000)
    assert projection.project_terminal(first.mission.mission_id)
    second = conversations.append_turn(
        owner_id, first.conversation.conversation_id, uuid4(), "C" * 30_000
    )
    conversations.store_summary(
        first.conversation.conversation_id,
        second.turn.turn_id,
        2,
        "既有摘要",
    )
    _complete_mission(environment, conversations, second.mission.mission_id, "D" * 8_000)
    assert projection.project_terminal(second.mission.mission_id)
    third = conversations.append_turn(
        owner_id, first.conversation.conversation_id, uuid4(), "E" * 30_000
    )
    relay = ScriptedRelay()
    service = MissionOrchestrator(
        missions,
        relay,
        capability_provider=lambda _owner: load_capability_cards(),
        conversation_context_builder=ConversationContextBuilder(conversations),
        conversation_projection=projection,
    )

    assert service.advance_pending(limit=50) == 1
    summary_run = next(iter(relay.payloads))
    relay.terminal(summary_run, "completed", '{"summary":"坏结果","through_seq":999}')
    assert service.advance_pending(limit=50) == 1

    persisted = conversations.conversation_for_owner(
        owner_id, first.conversation.conversation_id
    )
    assert persisted.summary == "既有摘要"
    assert persisted.summary_through_seq == 2
    messages = conversations.messages_after(
        owner_id, first.conversation.conversation_id
    )
    assert messages[-1].role == "system"
    assert "无法安全整理" in messages[-1].content
    assert messages[-1].turn_id == third.turn.turn_id


@pytest.mark.postgres
def test_summary_prompt_overflow_fails_the_turn_visibly(
    brain_database,
    monkeypatch,
) -> None:
    environment, owner_id = brain_database
    missions = MissionRepository(
        environment["urls"]["platform_control_app"], content_codec=_codec()
    )
    conversations = ConversationRepository(
        environment["urls"]["platform_control_app"],
        content_codec=missions.content_codec,
        mission_repository=missions,
    )
    projection = ConversationProjection(conversations)
    first = conversations.start(owner_id, uuid4(), "A" * 30_000)
    _complete_mission(environment, conversations, first.mission.mission_id, "B" * 8_000)
    assert projection.project_terminal(first.mission.mission_id)
    second = conversations.append_turn(
        owner_id, first.conversation.conversation_id, uuid4(), "C" * 30_000
    )
    monkeypatch.setattr(conversation_context, "MAX_CONTEXT_BYTES", 96 * 1024)
    import app.agent_brain.orchestrator as orchestrator_module

    monkeypatch.setattr(orchestrator_module, "MAX_BRAIN_PROMPT_BYTES", 128)
    service = MissionOrchestrator(
        missions,
        ScriptedRelay(),
        capability_provider=lambda _owner: load_capability_cards(),
        conversation_context_builder=ConversationContextBuilder(conversations),
        conversation_projection=projection,
    )

    assert service.advance_pending(limit=50) == 1

    mission = missions.mission_for_owner(owner_id, second.mission.mission_id)
    assert mission.status == "failed"
    assert "无法安全整理" in conversations.messages_after(
        owner_id, first.conversation.conversation_id
    )[-1].content


@pytest.mark.postgres
def test_direct_agent_follow_up_uses_the_same_summary_phase(
    brain_database,
) -> None:
    environment, owner_id = brain_database
    missions = MissionRepository(
        environment["urls"]["platform_control_app"], content_codec=_codec()
    )
    conversations = ConversationRepository(
        environment["urls"]["platform_control_app"],
        content_codec=missions.content_codec,
        mission_repository=missions,
    )
    relay = ScriptedRelay()
    service = MissionOrchestrator(
        missions,
        relay,
        capability_provider=lambda _owner: load_capability_cards(),
        conversation_context_builder=ConversationContextBuilder(conversations),
        conversation_projection=ConversationProjection(conversations),
    )
    first = conversations.start(
        owner_id,
        uuid4(),
        "A" * 30_000,
        mode="direct_agent",
        direct_agent_id="hr-bot",
    )
    assert service.advance_pending(limit=50) == 1
    first_run = next(iter(relay.payloads))
    relay.terminal(first_run, "completed", "B" * 8_000)
    assert service.advance_pending(limit=50) == 1
    second = conversations.append_turn(
        owner_id, first.conversation.conversation_id, uuid4(), "C" * 30_000
    )

    assert service.advance_pending(limit=50) == 1
    with psycopg.connect(environment["admin"]) as connection:
        summary_run = connection.execute(
            "select run_id from platform_control.mission_runs "
            "where mission_id=%s and phase='summary'",
            (second.mission.mission_id,),
        ).fetchone()[0]
    relay.terminal(
        summary_run,
        "completed",
        json.dumps({"summary": "直接 Agent 历史摘要", "through_seq": 2}, ensure_ascii=False),
    )
    assert service.advance_pending(limit=50) == 1
    assert service.advance_pending(limit=50) == 1
    with psycopg.connect(environment["admin"]) as connection:
        phases = [
            row[0]
            for row in connection.execute(
                "select phase from platform_control.mission_runs "
                "where mission_id=%s order by created_at,run_id",
                (second.mission.mission_id,),
            )
        ]
    assert phases == ["summary", "direct"]
