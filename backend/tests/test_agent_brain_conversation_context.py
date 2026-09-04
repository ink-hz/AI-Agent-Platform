from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import psycopg
import pytest
from test_agent_brain_conversation_repository import (
    conversation_database,
    repository,
)
from test_control_plane_migration import control_database

from app.agent_brain.conversation_context import (
    MAX_CONTEXT_BYTES,
    ConversationContextBuilder,
)
from app.agent_brain.conversation_projection import ConversationProjection
from app.hr.models import BindPositionConversation, CreateManualPosition
from app.hr.position_intelligence_models import HrPositionContextEnvelope
from app.hr.repository import HrPositionRepository
from app.hr.structured_output import HR_WORKFLOW_CONTRACT_V1
from app.hr.task_context import canonical_hash


@pytest.mark.postgres
def test_second_turn_contains_first_exchange_and_current_request(
    conversation_database,
    repository,
) -> None:
    environment, owner_id, _ = conversation_database
    first = repository.start(owner_id, uuid4(), "定义视觉算法候选人画像")
    _complete_mission(environment, repository, first.mission.mission_id, "第一轮结果")
    assert ConversationProjection(repository).project_terminal(
        first.mission.mission_id
    ) is True
    second = repository.append_turn(
        owner_id,
        first.conversation.conversation_id,
        uuid4(),
        "继续，给我搜索式",
    )

    context = ConversationContextBuilder(repository).build(
        first.conversation.conversation_id,
        second.turn.turn_id,
    )

    assert [item.role for item in context.messages] == [
        "user",
        "assistant",
        "user",
    ]
    assert [item.content for item in context.messages] == [
        "定义视觉算法候选人画像",
        "第一轮结果",
        "继续，给我搜索式",
    ]
    assert context.messages[-1].content == "继续，给我搜索式"
    assert context.estimated_utf8_bytes <= MAX_CONTEXT_BYTES


@pytest.mark.postgres
def test_context_never_reads_another_conversation(
    conversation_database,
    repository,
) -> None:
    environment, owner_id, _ = conversation_database
    first = repository.start(owner_id, uuid4(), "第一段对话")
    other = repository.start(owner_id, uuid4(), "绝不能混入的另一段对话")
    _complete_mission(environment, repository, other.mission.mission_id, "另一段答案")
    ConversationProjection(repository).project_terminal(other.mission.mission_id)

    context = ConversationContextBuilder(repository).build(
        first.conversation.conversation_id,
        first.turn.turn_id,
    )

    rendered = "\n".join(item.content for item in context.messages)
    assert rendered == "第一段对话"
    assert "另一段" not in rendered


@pytest.mark.postgres
def test_verified_hr_position_turn_receives_exactly_one_pinned_envelope(
    conversation_database,
    repository,
    request,
) -> None:
    environment, owner_id, _ = conversation_database
    positions = HrPositionRepository(environment["urls"]["platform_control_app"])
    position = positions.create_manual(
        CreateManualPosition(owner_id, uuid4(), uuid4(), "结构工程师")
    )

    def cleanup():
        with psycopg.connect(environment["admin"]) as connection:
            connection.execute(
                "delete from platform_hr.position_conversations "
                "where owner_internal_user_id=%s",
                (owner_id,),
            )
            connection.execute(
                "delete from platform_hr.positions where owner_internal_user_id=%s",
                (owner_id,),
            )

    request.addfinalizer(cleanup)
    started = repository.start(
        owner_id, uuid4(), "生成 JD", mode="direct_agent", direct_agent_id="hr-bot"
    )
    positions.bind_conversation(BindPositionConversation(
        owner_id, position.position_id, started.conversation.conversation_id,
        uuid4(), "created_in_position",
    ))
    envelope = HrPositionContextEnvelope(
        position.position_id, None, None, "jd", (), None, None, (), (),
        "Pinned position", "a" * 64,
    )
    envelope = replace(envelope, canonical_sha256=canonical_hash(envelope))

    class Provider:
        def __init__(self):
            self.calls = []

        def build_for_turn(self, selected_owner, conversation_id, turn_id):
            self.calls.append((selected_owner, conversation_id, turn_id))
            return envelope

    provider = Provider()
    context = ConversationContextBuilder(
        repository, hr_task_context_provider=provider
    ).build(started.conversation.conversation_id, started.turn.turn_id)

    assert context.hr_position_context is envelope
    assert provider.calls == [(
        owner_id, started.conversation.conversation_id, started.turn.turn_id
    )]


@pytest.mark.postgres
def test_unbound_hr_turn_does_not_call_position_provider(
    conversation_database,
    repository,
) -> None:
    _environment, owner_id, _ = conversation_database
    started = repository.start(
        owner_id, uuid4(), "普通 HR 问题",
        mode="direct_agent", direct_agent_id="hr-bot",
    )

    class Provider:
        def build_for_turn(self, *_args):
            raise AssertionError("unbound conversation must not use HR provider")

    context = ConversationContextBuilder(
        repository, hr_task_context_provider=Provider()
    ).build(started.conversation.conversation_id, started.turn.turn_id)

    assert context.hr_position_context is None
    assert context.hr_workflow_contract == HR_WORKFLOW_CONTRACT_V1


@pytest.mark.postgres
def test_unbound_candidate_parser_turn_receives_only_verified_special_input(
    conversation_database,
    repository,
) -> None:
    _environment, owner_id, _ = conversation_database
    request_id = uuid4()
    attachment_id = uuid4()
    started = repository.start(
        owner_id, request_id, "parse one resume",
        mode="direct_agent", direct_agent_id="hr-bot",
    )

    class Provider:
        def __init__(self):
            self.calls = []

        def for_turn(self, selected_owner, conversation_id, turn_id):
            self.calls.append((selected_owner, conversation_id, turn_id))
            return attachment_id

    provider = Provider()
    context = ConversationContextBuilder(
        repository, candidate_parser_input_provider=provider
    ).build(started.conversation.conversation_id, started.turn.turn_id)

    assert context.active_attachment_ids == (attachment_id,)
    assert context.hr_position_context is None
    assert provider.calls == [(
        owner_id, started.conversation.conversation_id, started.turn.turn_id
    )]


@pytest.mark.postgres
def test_non_hr_turn_never_receives_candidate_parser_input(
    conversation_database,
    repository,
) -> None:
    _environment, owner_id, _ = conversation_database
    started = repository.start(owner_id, uuid4(), "ordinary request")

    class Provider:
        def for_turn(self, *_args):
            raise AssertionError("ordinary conversation must not use parser input")

    context = ConversationContextBuilder(
        repository, candidate_parser_input_provider=Provider()
    ).build(started.conversation.conversation_id, started.turn.turn_id)

    assert context.active_attachment_ids == ()
    assert context.hr_workflow_contract is None


@pytest.mark.postgres
def test_other_direct_agent_never_receives_hr_workflow_contract(
    conversation_database,
    repository,
) -> None:
    _environment, owner_id, _ = conversation_database
    started = repository.start(
        owner_id,
        uuid4(),
        "普通专业问题",
        mode="direct_agent",
        direct_agent_id="fae-bot",
    )

    context = ConversationContextBuilder(repository).build(
        started.conversation.conversation_id, started.turn.turn_id
    )

    assert context.hr_workflow_contract is None


@pytest.mark.postgres
def test_compaction_covers_only_completed_exchange_and_keeps_current_request(
    conversation_database,
    repository,
) -> None:
    environment, owner_id, _ = conversation_database
    first = repository.start(owner_id, uuid4(), "A" * 30_000)
    _complete_mission(
        environment, repository, first.mission.mission_id, "B" * 8_000
    )
    assert ConversationProjection(repository).project_terminal(
        first.mission.mission_id
    )
    second = repository.append_turn(
        owner_id,
        first.conversation.conversation_id,
        uuid4(),
        "C" * 30_000,
    )
    builder = ConversationContextBuilder(repository)

    candidate = builder.compaction_candidate(
        first.conversation.conversation_id, second.turn.turn_id
    )

    assert candidate is not None
    assert candidate.through_seq == 2
    assert [message.role for message in candidate.messages] == [
        "user",
        "assistant",
    ]
    assert all("C" not in message.content for message in candidate.messages)

    repository.store_summary(
        first.conversation.conversation_id,
        second.turn.turn_id,
        candidate.through_seq,
        "已确认第一轮的大体方向。",
    )
    context = builder.build(
        first.conversation.conversation_id, second.turn.turn_id
    )
    assert context.summary == "已确认第一轮的大体方向。"
    assert [message.content for message in context.messages] == ["C" * 30_000]
    assert context.estimated_utf8_bytes <= MAX_CONTEXT_BYTES

    with psycopg.connect(environment["admin"]) as connection:
        row = connection.execute(
            "select summary_ciphertext,summary_through_seq from "
            "platform_control.conversations where conversation_id=%s",
            (first.conversation.conversation_id,),
        ).fetchone()
    assert row[1] == 2
    assert "已确认".encode() not in bytes(row[0])


def _complete_mission(environment, repository, mission_id, text: str) -> None:
    from app.agent_brain.repository import _canonical_payload, _event_subject

    event_id = uuid4()
    payload, _ = _canonical_payload(
        {"text": text}, event_type="mission.completed"
    )
    sealed = repository.content_codec.seal_json(
        _event_subject(mission_id, event_id), payload
    )
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.mission_events "
            "(event_id,mission_id,seq,event_type,payload_ciphertext,"
            "encryption_key_version) values (%s,%s,2,'mission.completed',%s,%s)",
            (event_id, mission_id, sealed.ciphertext, sealed.key_version),
        )
        connection.execute(
            "update platform_control.missions set status='completed',"
            "terminal_at=now(),updated_at=now(),row_version=row_version+1 "
            "where mission_id=%s",
            (mission_id,),
        )
