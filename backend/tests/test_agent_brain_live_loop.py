from __future__ import annotations

from uuid import UUID

import psycopg
import pytest

from app.agent_brain.model_adapter import BrainModelResponse, BrainUsage
from app.agent_brain.tool_protocol import stable_runtime_id
from test_agent_brain_loop_repository import loop_database, loop_repository, seeded_loop
from test_agent_brain_loop_runtime import (
    _delegate_response,
    _response,
    _runtime,
)
from test_control_plane_migration import control_database


def _await_response(task_id: UUID, *wake_on: str) -> BrainModelResponse:
    return _response(
        "await_agent_events",
        {
            "task_ids": [str(task_id)],
            "wake_on": list(wake_on or ("finding", "result")),
            "public_reason": "等待专业 Agent 的真实发现",
        },
    )


def _send_response(task_id: UUID) -> BrainModelResponse:
    return _response(
        "send_agent_message",
        {
            "task_id": str(task_id),
            "message": "请补充一条可核验的证据",
            "public_reason": "根据已返回发现补充追问",
        },
    )


def _stop_response(task_id: UUID) -> BrainModelResponse:
    return _response(
        "stop_agent_task",
        {
            "task_id": str(task_id),
            "reason": "当前信息已经足够",
            "public_reason": "停止不再需要的专业任务",
        },
    )


@pytest.mark.postgres
def test_progress_wakes_brain_before_task_terminal(
    loop_database, loop_repository, seeded_loop
) -> None:
    environment, *_unused = loop_database
    loop_id, _snapshot = seeded_loop
    task_id = stable_runtime_id(loop_id, 1, 0, "task")
    runtime = _runtime(
        loop_repository,
        [_delegate_response(), _await_response(task_id, "finding")],
    )

    assert runtime.advance_one() is True
    assert loop_repository.queued_step_count(loop_id) == 1
    assert runtime.advance_one() is True
    assert loop_repository.loop_status(loop_id) == "waiting_agents"
    assert runtime.dispatch_one() is True
    assert runtime.reconcile_one() is True
    assert loop_repository.queued_step_count(loop_id) == 1

    with psycopg.connect(environment["admin"]) as connection:
        event_types = tuple(
            row[0]
            for row in connection.execute(
                "select event_type from platform_brain.agent_task_events "
                "where task_id=%s order by seq",
                (task_id,),
            )
        )
        wait = connection.execute(
            "select status,triggered_event_seq from "
            "platform_brain.brain_wait_subscriptions where loop_id=%s",
            (loop_id,),
        ).fetchone()
    assert event_types[:2] == ("thinking_summary", "finding")
    assert wait == ("triggered", 2)


@pytest.mark.postgres
def test_followup_reuses_child_session_and_stop_is_delivered_once(
    loop_database, loop_repository, seeded_loop
) -> None:
    environment, *_unused = loop_database
    loop_id, _snapshot = seeded_loop
    task_id = stable_runtime_id(loop_id, 1, 0, "task")
    runtime = _runtime(
        loop_repository,
        [
            _delegate_response(),
            _await_response(task_id, "finding"),
            _send_response(task_id),
            _stop_response(task_id),
        ],
    )

    runtime.advance_one()
    runtime.advance_one()
    runtime.dispatch_one()
    runtime.reconcile_one()
    assert runtime.advance_one() is True
    assert runtime.dispatch_one() is True
    assert runtime.reconcile_one() is True
    assert runtime.advance_one() is True
    assert runtime.dispatch_one() is True

    with psycopg.connect(environment["admin"]) as connection:
        sessions = connection.execute(
            "select count(*),min(child_session_id),max(child_session_id) from "
            "platform_brain.agent_task_sessions where task_id=%s",
            (task_id,),
        ).fetchone()
        messages = connection.execute(
            "select seq,message_kind from platform_brain.agent_task_messages "
            "where task_id=%s order by seq",
            (task_id,),
        ).fetchall()
        deliveries = connection.execute(
            "select delivery_kind,count(*) from platform_brain.adapter_deliveries "
            "where task_id=%s group by delivery_kind order by delivery_kind",
            (task_id,),
        ).fetchall()
    assert sessions[0] == 1
    assert sessions[1] == sessions[2]
    assert messages == [(1, "initial"), (2, "followup")]
    assert deliveries == [("followup", 1), ("initial", 1), ("stop", 1)]


@pytest.mark.postgres
def test_replayed_delegate_keeps_one_session_message_and_delivery(
    loop_database, loop_repository, seeded_loop
) -> None:
    environment, *_unused = loop_database
    loop_id, _snapshot = seeded_loop
    runtime = _runtime(loop_repository, _delegate_response())
    assert runtime.advance_one() is True

    with psycopg.connect(environment["admin"]) as connection:
        counts = connection.execute(
            "select (select count(*) from platform_brain.agent_tasks where loop_id=%s),"
            "(select count(*) from platform_brain.agent_task_sessions session join "
            "platform_brain.agent_tasks task on task.task_id=session.task_id "
            "where task.loop_id=%s),(select count(*) from "
            "platform_brain.agent_task_messages message join platform_brain.agent_tasks "
            "task on task.task_id=message.task_id where task.loop_id=%s),"
            "(select count(*) from platform_brain.adapter_deliveries delivery join "
            "platform_brain.agent_tasks task on task.task_id=delivery.task_id "
            "where task.loop_id=%s)",
            (loop_id, loop_id, loop_id, loop_id),
        ).fetchone()
    assert counts == (1, 1, 1, 1)
