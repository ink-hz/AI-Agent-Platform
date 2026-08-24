from __future__ import annotations

import psycopg
import pytest

from app.agent_brain.conversation_repository import ConversationRepository
from app.agent_brain.repository import MissionRepository
from test_agent_brain_loop_repository import loop_database, loop_repository, seeded_loop
from test_agent_brain_loop_runtime import (
    _delegate_response,
    _list_agents_response,
    _runtime,
    _submit_response,
)
from test_control_plane_migration import control_database


@pytest.mark.postgres
def test_crash_before_model_commit_reclaims_step_without_duplicate_calls(
    loop_database, loop_repository, seeded_loop
) -> None:
    environment, *_unused = loop_database
    loop_id, _snapshot_id = seeded_loop
    abandoned = loop_repository.lease_step("crashed-worker", lease_seconds=45)
    assert abandoned is not None
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_brain.brain_steps set lease_expires_at="
            "clock_timestamp()-interval '1 second' where step_id=%s",
            (abandoned.step_id,),
        )
    assert loop_repository.expire_leases(limit=10) == 1

    assert _runtime(loop_repository, _list_agents_response()).advance_one() is True
    with psycopg.connect(environment["admin"]) as connection:
        assert connection.execute(
            "select count(*) from platform_brain.brain_tool_calls call "
            "join platform_brain.brain_steps step on step.step_id=call.step_id "
            "where step.loop_id=%s",
            (loop_id,),
        ).fetchone() == (1,)


@pytest.mark.postgres
def test_recreated_runtime_after_every_commit_keeps_one_task_and_answer(
    loop_database, loop_repository, seeded_loop
) -> None:
    environment, *_unused = loop_database
    loop_id, _snapshot_id = seeded_loop
    _runtime(loop_repository, _list_agents_response()).advance_one()
    _runtime(loop_repository, _delegate_response()).advance_one()
    _runtime(loop_repository).dispatch_one()
    _runtime(loop_repository, _submit_response()).advance_one()

    assert _runtime(loop_repository, _submit_response()).advance_one() is False
    with psycopg.connect(environment["admin"]) as connection:
        counts = connection.execute(
            "select (select count(*) from platform_brain.agent_tasks "
            "where loop_id=%s),(select count(*) from "
            "platform_control.conversation_messages message join "
            "platform_control.conversation_turns turn on "
            "turn.assistant_message_id=message.message_id join "
            "platform_brain.brain_loops loop on loop.turn_id=turn.turn_id "
            "where loop.loop_id=%s),"
            "(select count(*) from platform_brain.brain_checkpoints where loop_id=%s)",
            (loop_id, loop_id, loop_id),
        ).fetchone()
    assert counts == (1, 1, 0)


@pytest.mark.postgres
def test_expired_delivery_reuses_business_idempotency_key(
    loop_database, loop_repository, seeded_loop
) -> None:
    environment, *_unused = loop_database
    _runtime(loop_repository, _delegate_response()).advance_one()
    first = loop_repository.lease_task_delivery("adapter-a", lease_seconds=45)
    assert first is not None
    assert first.requester_subject.internal_user_id == loop_database[2]
    assert first.requester_subject.display_name == "Brain Owner"
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_brain.adapter_deliveries set lease_expires_at="
            "clock_timestamp()-interval '1 second' where delivery_id=%s",
            (first.delivery_id,),
        )
    assert loop_repository.expire_delivery_leases(limit=10) == 1
    second = loop_repository.lease_task_delivery("adapter-b", lease_seconds=45)
    assert second is not None
    assert second.delivery_id == first.delivery_id
    assert second.idempotency_key == first.idempotency_key
    assert second.attempt == 2


@pytest.mark.postgres
def test_user_stop_terminalizes_waiting_user_without_normal_answer(
    loop_database, loop_repository, seeded_loop
) -> None:
    environment, codec, owner, conversation_id, _turn_id = loop_database
    loop_id, _snapshot = seeded_loop
    from test_agent_brain_loop_runtime import _request_user_response

    _runtime(loop_repository, _request_user_response()).advance_one()
    conversations = ConversationRepository(
        environment["urls"]["platform_control_app"],
        content_codec=codec,
        mission_repository=MissionRepository(
            environment["urls"]["platform_control_app"], content_codec=codec
        ),
    )
    conversations.request_cancel_v2(owner, conversation_id)
    assert _runtime(loop_repository).reconcile_cancellations() == 1
    with psycopg.connect(environment["admin"]) as connection:
        state = connection.execute(
            "select loop.status,loop.reason_code,turn.status,turn.assistant_message_id "
            "from platform_brain.brain_loops loop join "
            "platform_control.conversation_turns turn on turn.turn_id=loop.turn_id "
            "where loop.loop_id=%s",
            (loop_id,),
        ).fetchone()
    assert state == ("cancelled", "cancelled_by_user", "cancelled", None)
