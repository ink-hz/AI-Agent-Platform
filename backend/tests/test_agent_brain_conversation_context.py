from __future__ import annotations

from uuid import uuid4

import psycopg
import pytest

from app.agent_brain.conversation_context import (
    MAX_CONTEXT_BYTES,
    ConversationContextBuilder,
)
from app.agent_brain.conversation_projection import ConversationProjection
from test_agent_brain_conversation_repository import (
    conversation_database,
    repository,
)
from test_control_plane_migration import control_database


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
