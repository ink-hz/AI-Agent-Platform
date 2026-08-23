from __future__ import annotations

from uuid import uuid4

import psycopg
import pytest

from app.agent_brain.conversation_projection import ConversationProjection
from test_agent_brain_conversation_context import _complete_mission
from test_agent_brain_conversation_repository import (
    conversation_database,
    repository,
)
from test_control_plane_migration import control_database


@pytest.mark.postgres
def test_terminal_projects_exactly_one_assistant_message(
    conversation_database,
    repository,
) -> None:
    environment, owner_id, _ = conversation_database
    started = repository.start(owner_id, uuid4(), "第一轮请求")
    _complete_mission(
        environment,
        repository,
        started.mission.mission_id,
        "最终交付",
    )
    projector = ConversationProjection(repository)

    assert projector.project_terminal(started.mission.mission_id) is True
    assert projector.project_terminal(started.mission.mission_id) is False

    messages = repository.messages_after(
        owner_id, started.conversation.conversation_id
    )
    assert [(item.role, item.content) for item in messages] == [
        ("user", "第一轮请求"),
        ("assistant", "最终交付"),
    ]
    with psycopg.connect(environment["admin"]) as connection:
        turn = connection.execute(
            "select status,assistant_message_id from "
            "platform_control.conversation_turns where turn_id=%s",
            (started.turn.turn_id,),
        ).fetchone()
    assert turn[0] == "completed"
    assert turn[1] == messages[-1].message_id


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("mission_status", "event_type", "turn_status", "role", "text"),
    (
        ("failed", "mission.failed", "failed", "system", "执行失败"),
        ("cancelled", "mission.cancelled", "cancelled", "system", "任务已取消"),
        (
            "interrupted",
            "mission.interrupted",
            "interrupted",
            "system",
            "执行已中断",
        ),
    ),
)
def test_terminal_failure_states_release_conversation_with_explicit_message(
    conversation_database,
    repository,
    mission_status,
    event_type,
    turn_status,
    role,
    text,
) -> None:
    _environment, owner_id, _ = conversation_database
    started = repository.start(owner_id, uuid4(), "会失败的请求")
    repository._missions.terminate_mission(
        owner_id,
        started.mission.mission_id,
        status=mission_status,
        event_type=event_type,
        event_payload={"text": text, "reason_code": "test_failure"},
    )

    assert ConversationProjection(repository).project_terminal(
        started.mission.mission_id
    ) is True
    messages = repository.messages_after(
        owner_id, started.conversation.conversation_id
    )
    assert (messages[-1].role, messages[-1].content) == (role, text)

    follow_up = repository.append_turn(
        owner_id,
        started.conversation.conversation_id,
        uuid4(),
        "失败后继续",
    )
    assert follow_up.message.seq == 3


@pytest.mark.postgres
def test_pending_projection_recovers_terminal_mission_after_crash(
    conversation_database,
    repository,
) -> None:
    environment, owner_id, _ = conversation_database
    started = repository.start(owner_id, uuid4(), "模拟提交后崩溃")
    _complete_mission(
        environment,
        repository,
        started.mission.mission_id,
        "已经完成但尚未投影",
    )
    projector = ConversationProjection(repository)

    assert projector.project_pending(limit=10) == 1
    assert projector.project_pending(limit=10) == 0
    assert repository.messages_after(
        owner_id, started.conversation.conversation_id
    )[-1].content == "已经完成但尚未投影"
