from __future__ import annotations

from uuid import uuid4

import psycopg
import pytest

from app.agent_brain.conversation_projection import (
    ConversationProjection,
    PrivateBrainEvent,
)
from app.agent_brain.conversation_service import ConversationCommandService
from app.agent_brain.loop_repository import BrainLoopRepository
from test_agent_brain_conversation_context import _complete_mission
from test_agent_brain_conversation_repository import (
    conversation_database,
    repository,
)
from test_control_plane_migration import control_database


def test_public_projection_redacts_private_runtime_fields() -> None:
    private = PrivateBrainEvent(
        event_type="agent.task_completed",
        payload={
            "agent_id": "hr-bot",
            "agent_name": "HR Agent",
            "objective_summary": "评估候选人",
            "public_reason": "需要招聘专业判断",
            "status": "completed",
            "duration_ms": 1200,
            "attachment_refs": [],
            "reason_code": "completed",
            "thinking": "private chain",
            "provider_request_id": "provider-secret",
            "prompt": "private prompt",
            "internal_url": "http://127.0.0.1:9200",
            "grant_ids": ["private-grant"],
        },
    )

    projected = ConversationProjection.project(private)

    assert set(projected.payload) <= {
        "agent_id",
        "agent_name",
        "objective_summary",
        "public_reason",
        "status",
        "duration_ms",
        "attachment_refs",
        "reason_code",
    }
    serialized = str(projected.payload).lower()
    assert "thinking" not in serialized
    assert "provider" not in serialized
    assert "127.0.0.1" not in serialized
    assert "grant" not in serialized


def test_public_projection_rejects_non_allowlisted_brain_event() -> None:
    with pytest.raises(ValueError, match="public Brain event"):
        ConversationProjection.project(
            PrivateBrainEvent(event_type="provider.raw_response", payload={})
        )


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


@pytest.mark.postgres
def test_v2_projection_is_idempotent_and_separates_agent_from_brain_resume(
    conversation_database,
    repository,
) -> None:
    environment, owner, _other = conversation_database
    started = ConversationCommandService(repository, v2_enabled=True).start(
        owner, uuid4(), "验证协作时间线"
    )
    loops = BrainLoopRepository(
        environment["urls"]["platform_brain_worker"],
        content_codec=repository.content_codec,
    )
    from test_agent_brain_loop_runtime import _delegate_response, _runtime

    assert _runtime(loops, _delegate_response()).advance_one() is True
    runtime = _runtime(loops)
    assert runtime.dispatch_one() is True
    assert runtime.reconcile_one() is True
    projector = ConversationProjection(repository)

    assert projector.project_brain_pending(
        started.conversation.conversation_id
    ) > 0
    assert projector.project_brain_pending(
        started.conversation.conversation_id
    ) == 0
    events = repository.events_after(
        owner, started.conversation.conversation_id, after=0, limit=100
    )
    event_types = [item.event_type for item in events]
    assert "agent.task_completed" in event_types
    assert "brain.resumed" in event_types
    completed = next(
        item for item in events if item.event_type == "agent.task_completed"
    )
    assert set(completed.payload) <= {
        "agent_id", "agent_name", "objective_summary", "public_reason",
        "status", "duration_ms", "attachment_refs", "reason_code",
        "task_id", "child_session_id", "source", "source_ref", "kind",
        "summary", "evidence_refs", "artifact_refs", "created_at",
    }
