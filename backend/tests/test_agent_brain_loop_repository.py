from __future__ import annotations

import json

# Imported fixture names intentionally become pytest fixtures in this module.
# ruff: noqa: F401,F811
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import psycopg
import pytest
from app.agent_brain.conversation_repository import message_subject
from app.agent_brain.loop_models import NormalizedTaskResult
from app.agent_brain.loop_repository import (
    AgentTaskEventInput,
    BrainLoopRepository,
    BrainRepositoryConflict,
    ModelStepCommit,
    TaskDispatchSpec,
)
from app.agent_brain.tool_protocol import ToolLimits, parse_tool_batch
from app.control_plane.crypto import IdentityKeyring
from app.execution_relay.content_crypto import ContentCodec
from test_control_plane_migration import control_database

NOW = datetime(2026, 8, 24, 4, 0, tzinfo=timezone.utc)


def _codec() -> ContentCodec:
    return ContentCodec(
        IdentityKeyring(
            active_version=5,
            purpose="platform-content-encryption",
            _keys={4: b"4" * 32, 5: b"5" * 32},
        )
    )


def _delegate_block(
    *, provider_id: str = "toolu_1", objective: str = "分析候选人能力组合"
) -> dict[str, object]:
    return {
        "type": "tool_use",
        "id": provider_id,
        "name": "delegate_task",
        "input": {
            "agent_id": "hr-bot",
            "capability_version": 1,
            "objective": objective,
            "context_excerpt": ["岗位要求视觉和硬件经验"],
            "constraints": ["不联系候选人"],
            "attachment_refs": [],
            "expected_output": "判断、证据、风险和面试问题",
            "public_reason": "需要 HR Agent 做专业判断",
        },
    }


def _commit(
    snapshot_id: UUID,
    *,
    provider_id: str = "toolu_1",
    objective: str = "分析候选人能力组合",
    thinking: str = "private-reasoning-must-never-leak",
) -> ModelStepCommit:
    delegate = _delegate_block(provider_id=provider_id, objective=objective)
    batch = parse_tool_batch([delegate], ToolLimits())
    return ModelStepCommit(
        provider_request_id="msg_provider_1",
        content_blocks=(
            {
                "type": "thinking",
                "thinking": thinking,
                "signature": "signed-thinking-block",
            },
            delegate,
        ),
        usage={"input_tokens": 1200, "output_tokens": 80},
        cache_usage={"cache_read_input_tokens": 900},
        stop_reason="tool_use",
        batch=batch,
        task_specs=(
            TaskDispatchSpec(
                tool_index=0,
                adapter_kind="reference",
                capability_version=1,
                authorization_snapshot_id=snapshot_id,
                effective_deadline_at=NOW + timedelta(minutes=5),
            ),
        ),
    )


def _clear_v2(connection) -> None:
    connection.execute("set constraints all deferred")
    for table in (
        "agent_action_deliveries",
        "agent_task_actions",
        "brain_user_interventions",
        "brain_wait_subscriptions",
        "brain_task_event_cursors",
        "agent_runtime_health",
        "brain_thinking_summaries",
        "agent_task_messages",
        "agent_task_sessions",
        "brain_checkpoints",
        "adapter_deliveries",
        "agent_task_events",
        "agent_tasks",
        "brain_tool_calls",
        "brain_steps",
        "brain_loops",
        "authorization_snapshots",
    ):
        connection.execute(f"delete from platform_brain.{table}")
    for table in (
        "access_events",
        "task_grants",
        "derivatives",
        "artifact_versions",
        "artifacts",
        "processing_jobs",
        "erasure_jobs",
        "message_citations",
        "conversation_read_state",
        "bindings",
        "upload_write_attempts",
        "uploads",
        "attachments",
    ):
        connection.execute(f"delete from platform_attachments.{table}")
    connection.execute("delete from platform_control.conversation_feedback")
    connection.execute("delete from platform_control.conversation_events")
    connection.execute("delete from platform_control.execution_events")
    connection.execute("delete from platform_control.execution_jobs")
    connection.execute("delete from platform_control.mission_events")
    connection.execute("delete from platform_control.mission_runs")
    connection.execute("delete from platform_control.mission_tasks")
    connection.execute("delete from platform_control.mission_messages")
    connection.execute("delete from platform_control.missions")
    connection.execute("delete from platform_control.conversation_messages")
    connection.execute("delete from platform_control.conversation_turns")
    connection.execute("delete from platform_control.conversations")


@pytest.fixture()
def loop_database(control_database):
    environment = control_database["environments"]["production"]
    codec = _codec()
    owner_id = uuid4()
    conversation_id = uuid4()
    turn_id = uuid4()
    message_id = uuid4()
    sealed = codec.seal_json(
        message_subject(conversation_id, message_id),
        {"text": "请分析这名候选人"},
    )
    with psycopg.connect(environment["admin"]) as connection:
        _clear_v2(connection)
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id,display_name,status) values (%s,'Brain Owner','active')",
            (owner_id,),
        )
        connection.execute(
            "insert into platform_control.conversations "
            "(conversation_id,owner_internal_user_id,started_by_client_request_id,"
            "mode,title,status) values (%s,%s,%s,'brain','人才判断','active')",
            (conversation_id, owner_id, uuid4()),
        )
        connection.execute("set constraints all deferred")
        connection.execute(
            "insert into platform_control.conversation_messages "
            "(message_id,conversation_id,seq,role,content_ciphertext,"
            "encryption_key_version,turn_id,delivery_status) "
            "values (%s,%s,1,'user',%s,%s,%s,'accepted')",
            (
                message_id,
                conversation_id,
                sealed.ciphertext,
                sealed.key_version,
                turn_id,
            ),
        )
        connection.execute(
            "insert into platform_control.conversation_turns "
            "(turn_id,conversation_id,user_message_id,client_request_id,status) "
            "values (%s,%s,%s,%s,'accepted')",
            (turn_id, conversation_id, message_id, uuid4()),
        )
    yield environment, codec, owner_id, conversation_id, turn_id
    with psycopg.connect(environment["admin"]) as connection:
        _clear_v2(connection)


@pytest.fixture()
def loop_repository(loop_database) -> BrainLoopRepository:
    environment, codec, *_unused = loop_database
    return BrainLoopRepository(
        environment["urls"]["platform_brain_worker"],
        content_codec=codec,
    )


@pytest.fixture()
def seeded_loop(loop_database, loop_repository):
    _environment, _codec_value, owner_id, conversation_id, turn_id = loop_database
    loop_id = uuid4()
    loop_repository.create_loop(
        loop_id=loop_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        model_config={"config_version": "brain-opus5-v1"},
        max_steps=12,
        max_tasks=8,
        max_duration_seconds=900,
    )
    snapshot_id = loop_repository.create_authorization_snapshot(
        internal_user_id=owner_id,
        agent_id="hr-bot",
        allowed=True,
        grant_ids=(),
        directory_generation_id=None,
        capability_version=1,
        effective_decision_hash=b"d" * 32,
    )
    return loop_id, snapshot_id


def _lease(loop_repository: BrainLoopRepository):
    lease = loop_repository.lease_step("brain-worker-a", lease_seconds=45)
    assert lease is not None
    return lease


@pytest.mark.postgres
def test_replayed_tool_call_creates_one_task(loop_repository, seeded_loop) -> None:
    loop_id, snapshot_id = seeded_loop
    lease = _lease(loop_repository)
    response = _commit(snapshot_id)

    first = loop_repository.commit_model_step(
        loop_id, lease.step_seq, "brain-worker-a", response
    )
    second = loop_repository.commit_model_step(
        loop_id, lease.step_seq, "brain-worker-a", response
    )

    assert first.task_ids == second.task_ids
    assert len(first.task_ids) == 1
    assert loop_repository.task_count(loop_id) == 1


@pytest.mark.postgres
def test_replayed_tool_call_with_changed_payload_conflicts(
    loop_repository, seeded_loop
) -> None:
    loop_id, snapshot_id = seeded_loop
    lease = _lease(loop_repository)
    loop_repository.commit_model_step(
        loop_id, lease.step_seq, "brain-worker-a", _commit(snapshot_id)
    )
    with pytest.raises(BrainRepositoryConflict):
        loop_repository.commit_model_step(
            loop_id,
            lease.step_seq,
            "brain-worker-a",
            _commit(snapshot_id, objective="另一个任务"),
        )


@pytest.mark.postgres
def test_task_event_same_seq_is_idempotent_but_conflict_fails(
    loop_repository, seeded_loop
) -> None:
    loop_id, snapshot_id = seeded_loop
    lease = _lease(loop_repository)
    task_id = loop_repository.commit_model_step(
        loop_id, lease.step_seq, "brain-worker-a", _commit(snapshot_id)
    ).task_ids[0]
    event = AgentTaskEventInput(
        task_id=task_id,
        seq=1,
        event_type="agent.progress",
        created_at=NOW,
        payload={"message": "正在分析"},
    )

    assert loop_repository.append_task_event(event) is True
    assert loop_repository.append_task_event(event) is False
    with pytest.raises(BrainRepositoryConflict):
        loop_repository.append_task_event(
            AgentTaskEventInput(
                task_id=task_id,
                seq=1,
                event_type="agent.progress",
                created_at=NOW,
                payload={"message": "内容冲突"},
            )
        )


@pytest.mark.postgres
def test_terminal_task_rejects_new_events(loop_repository, seeded_loop) -> None:
    loop_id, snapshot_id = seeded_loop
    lease = _lease(loop_repository)
    task_id = loop_repository.commit_model_step(
        loop_id, lease.step_seq, "brain-worker-a", _commit(snapshot_id)
    ).task_ids[0]
    result = NormalizedTaskResult(
        status="completed",
        summary="候选人匹配",
        deliverables=("人才判断",),
        evidence=("视觉项目",),
        limitations=(),
        attachment_refs=(),
    )
    assert loop_repository.append_task_event(
        AgentTaskEventInput(
            task_id=task_id,
            seq=1,
            event_type="agent.completed",
            created_at=NOW,
            payload={"status": "completed"},
            terminal_status="completed",
            result=result,
        )
    ) is True
    with pytest.raises(BrainRepositoryConflict):
        loop_repository.append_task_event(
            AgentTaskEventInput(
                task_id=task_id,
                seq=2,
                event_type="agent.progress",
                created_at=NOW + timedelta(seconds=1),
                payload={"message": "不应接受"},
            )
        )


@pytest.mark.postgres
def test_active_step_lease_excludes_and_expired_lease_is_reclaimed(
    loop_database, loop_repository, seeded_loop
) -> None:
    environment, *_unused = loop_database
    loop_id, _snapshot_id = seeded_loop
    first = _lease(loop_repository)
    assert loop_repository.lease_step("brain-worker-b", lease_seconds=45) is None
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_brain.brain_steps "
            "set lease_expires_at=now()-interval '1 second' where step_id=%s",
            (first.step_id,),
        )

    reclaimed = loop_repository.lease_step("brain-worker-b", lease_seconds=45)
    assert reclaimed is not None
    assert reclaimed.loop_id == loop_id
    assert reclaimed.attempt == first.attempt + 1
    assert reclaimed.lease_worker_id == "brain-worker-b"


@pytest.mark.postgres
def test_stale_loop_row_version_is_rejected(loop_repository, seeded_loop) -> None:
    loop_id, _snapshot_id = seeded_loop
    changed = loop_repository.request_cancel(loop_id, expected_row_version=0)
    assert changed.row_version == 1
    with pytest.raises(BrainRepositoryConflict):
        loop_repository.request_cancel(loop_id, expected_row_version=0)


@pytest.mark.postgres
def test_checkpoint_deletion_does_not_change_message_reconstruction(
    loop_database, loop_repository, seeded_loop
) -> None:
    environment, *_unused = loop_database
    loop_id, snapshot_id = seeded_loop
    lease = _lease(loop_repository)
    task_id = loop_repository.commit_model_step(
        loop_id, lease.step_seq, "brain-worker-a", _commit(snapshot_id)
    ).task_ids[0]
    result = NormalizedTaskResult(
        status="completed",
        summary="候选人具备所需组合",
        deliverables=("人才判断",),
        evidence=("外企英文环境", "视觉项目"),
        limitations=("需面试核实",),
        attachment_refs=(),
    )
    loop_repository.append_task_event(
        AgentTaskEventInput(
            task_id=task_id,
            seq=1,
            event_type="agent.completed",
            created_at=NOW,
            payload={"status": "completed"},
            terminal_status="completed",
            result=result,
        )
    )
    assert loop_repository.queued_step_count(loop_id) == 1
    before = loop_repository.reconstruct_messages(loop_id)
    loop_repository.put_checkpoint(
        loop_id,
        through_step_seq=1,
        source_hash=b"s" * 32,
        value={"cached": True},
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "delete from platform_brain.brain_checkpoints where loop_id=%s",
            (loop_id,),
        )
    after = loop_repository.reconstruct_messages(loop_id)

    assert before == after
    assert before[0] == {"role": "user", "content": "请分析这名候选人"}
    assert before[1]["role"] == "assistant"
    assert before[2]["role"] == "user"
    tool_result = before[2]["content"][0]
    assert tool_result["type"] == "tool_result"
    assert tool_result["tool_use_id"] == "toolu_1"
    assert json.loads(tool_result["content"])["summary"] == "候选人具备所需组合"


@pytest.mark.postgres
def test_thinking_is_encrypted_and_never_appears_in_repr_or_errors(
    loop_database, loop_repository, seeded_loop
) -> None:
    environment, *_unused = loop_database
    loop_id, snapshot_id = seeded_loop
    lease = _lease(loop_repository)
    commit = _commit(snapshot_id, thinking="top-secret-thinking")

    assert "top-secret-thinking" not in repr(commit)
    assert "top-secret-thinking" not in repr(loop_repository)
    loop_repository.commit_model_step(
        loop_id, lease.step_seq, "brain-worker-a", commit
    )
    with psycopg.connect(environment["admin"]) as connection:
        ciphertext = bytes(
            connection.execute(
                "select model_response_ciphertext from platform_brain.brain_steps "
                "where loop_id=%s",
                (loop_id,),
            ).fetchone()[0]
        )
    assert b"top-secret-thinking" not in ciphertext

    with pytest.raises(BrainRepositoryConflict) as raised:
        loop_repository.commit_model_step(
            loop_id,
            lease.step_seq,
            "brain-worker-a",
            _commit(snapshot_id, thinking="different-secret-thinking"),
        )
    assert "secret" not in repr(raised.value)


@pytest.mark.postgres
def test_seven_day_erasure_keeps_normalized_records_and_usage(
    loop_database, loop_repository, seeded_loop
) -> None:
    environment, *_unused = loop_database
    loop_id, snapshot_id = seeded_loop
    lease = _lease(loop_repository)
    task_id = loop_repository.commit_model_step(
        loop_id, lease.step_seq, "brain-worker-a", _commit(snapshot_id)
    ).task_ids[0]
    result = NormalizedTaskResult(
        status="completed",
        summary="已完成",
        deliverables=(),
        evidence=(),
        limitations=(),
        attachment_refs=(),
    )
    loop_repository.append_task_event(
        AgentTaskEventInput(
            task_id=task_id,
            seq=1,
            event_type="agent.completed",
            created_at=NOW,
            payload={"status": "completed"},
            terminal_status="completed",
            result=result,
        )
    )
    loop_repository.settle_batch(loop_id)
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_brain.brain_loops set status='completed',"
            "terminal_at=now()-interval '8 days',active_started_at=null,"
            "active_deadline_at=null where loop_id=%s",
            (loop_id,),
        )
        connection.execute(
            "update platform_brain.brain_steps set response_retention_until="
            "now()-interval '1 second' where loop_id=%s",
            (loop_id,),
        )

    assert loop_repository.erase_expired_model_responses(limit=10) == 1
    with psycopg.connect(environment["admin"]) as connection:
        row = connection.execute(
            "select model_response_ciphertext,response_erased_at,usage "
            "from platform_brain.brain_steps where loop_id=%s and step_seq=1",
            (loop_id,),
        ).fetchone()
        counts = connection.execute(
            "select (select count(*) from platform_brain.brain_tool_calls "
            "where step_id in (select step_id from platform_brain.brain_steps "
            "where loop_id=%s)),"
            "(select count(*) from platform_brain.agent_task_events where task_id=%s)",
            (loop_id, task_id),
        ).fetchone()
    assert row[0] is None
    assert row[1] is not None
    assert row[2]["input_tokens"] == 1200
    assert counts == (1, 1)
