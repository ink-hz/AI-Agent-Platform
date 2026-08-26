from __future__ import annotations

from uuid import uuid4

import psycopg
import pytest

from app.agent_brain.conversation_repository import ConversationRepositoryNotFound
from app.agent_brain.tool_protocol import stable_runtime_id
from test_agent_brain_loop_repository import loop_database, loop_repository, seeded_loop
from test_control_plane_migration import control_database


def _conversations(environment, loop_repository):
    from app.agent_brain.conversation_repository import ConversationRepository
    from app.agent_brain.repository import MissionRepository

    return ConversationRepository(
        environment["urls"]["platform_control_app"],
        content_codec=loop_repository.content_codec,
        mission_repository=MissionRepository(
            environment["urls"]["platform_control_app"],
            content_codec=loop_repository.content_codec,
        ),
    )


@pytest.mark.postgres
def test_running_turn_accepts_idempotent_intervention(
    loop_database, loop_repository, seeded_loop
) -> None:
    environment, _codec, owner_id, conversation_id, turn_id = loop_database
    loop_id, _snapshot = seeded_loop
    # The Conversation repository owns the user-facing write transaction.
    conversations = _conversations(environment, loop_repository)
    request_id = uuid4()

    first = conversations.append_brain_intervention(
        owner_id, conversation_id, request_id, "只看深圳，排除管理岗"
    )
    replay = conversations.append_brain_intervention(
        owner_id, conversation_id, request_id, "只看深圳，排除管理岗"
    )

    assert first.created is True and replay.created is False
    assert first.message.message_id == replay.message.message_id == request_id
    assert first.turn.turn_id == turn_id
    with psycopg.connect(environment["admin"]) as connection:
        assert connection.execute(
            "select count(*) from platform_brain.brain_user_interventions "
            "where loop_id=%s",
            (loop_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "select count(*) from platform_control.conversation_events "
            "where conversation_id=%s and event_type='brain.user_intervention'",
            (conversation_id,),
        ).fetchone()[0] == 1


@pytest.mark.postgres
def test_intervention_wakes_waiting_agent_step(
    loop_database, loop_repository, seeded_loop
) -> None:
    environment, _codec, owner_id, conversation_id, _turn_id = loop_database
    loop_id, _snapshot = seeded_loop
    task_id = stable_runtime_id(loop_id, 1, 0, "task")
    from test_agent_brain_live_loop import _await_response
    from test_agent_brain_loop_runtime import _delegate_response, _runtime

    runtime = _runtime(
        loop_repository, [_delegate_response(), _await_response(task_id, "finding")]
    )
    assert runtime.advance_one() is True
    assert runtime.advance_one() is True

    result = _conversations(environment, loop_repository).append_brain_intervention(
        owner_id, conversation_id, uuid4(), "先只看深圳候选人"
    )

    assert result.created is True
    assert loop_repository.loop_status(loop_id) == "running"
    assert loop_repository.queued_step_count(loop_id) == 1


@pytest.mark.postgres
def test_foreign_owner_cannot_read_child_task(
    loop_database, loop_repository, seeded_loop
) -> None:
    environment, _codec, _owner_id, conversation_id, turn_id = loop_database
    conversations = _conversations(environment, loop_repository)
    with pytest.raises(ConversationRepositoryNotFound):
        conversations.task_detail_for_owner(
            uuid4(), conversation_id, turn_id, uuid4()
        )
