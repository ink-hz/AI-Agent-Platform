from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from uuid import UUID, uuid4

import psycopg
import pytest

from app.agent_brain.collaboration_models import (
    AgentTaskMessageInput,
    AgentTaskPublicEventInput,
    BrainThinkingDelta,
    WaitSubscriptionSpec,
)
from app.agent_brain.collaboration_repository import CollaborationRepository
from app.agent_brain.conversation_repository import message_subject
from app.agent_brain.loop_repository import (
    BrainLoopRepository,
    BrainRepositoryConflict,
    BrainRepositoryError,
    ModelStepCommit,
    TaskDispatchSpec,
)
from app.agent_brain.tool_protocol import ToolLimits, parse_tool_batch
from app.control_plane.crypto import IdentityKeyring
from app.execution_relay.content_crypto import ContentCodec
from test_control_plane_migration import control_database


NOW = datetime(2026, 8, 25, 2, 0, tzinfo=timezone.utc)


def _codec() -> ContentCodec:
    return ContentCodec(
        IdentityKeyring(
            active_version=5,
            purpose="platform-content-encryption",
            _keys={5: b"5" * 32},
        )
    )


def _delegate_block() -> dict[str, object]:
    return {
        "type": "tool_use",
        "id": "toolu_live_delegate",
        "name": "delegate_task",
        "input": {
            "agent_id": "hr-bot",
            "objective": "分析候选人的复合能力",
            "context_excerpt": ["视觉技术、英文和硬件产品经验"],
            "constraints": ["不联系候选人"],
            "attachment_refs": [],
            "expected_output": "证据、缺口和下一步",
            "public_reason": "需要 HR Agent 做专业判断",
        },
    }


def _clear_live(connection) -> None:
    connection.execute("set constraints all deferred")
    for table in (
        "brain_user_interventions",
        "brain_wait_subscriptions",
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
    connection.execute("delete from platform_control.conversation_events")
    connection.execute("delete from platform_control.conversation_messages")
    connection.execute("delete from platform_control.conversation_turns")
    connection.execute("delete from platform_control.conversations")


@pytest.fixture()
def live_database(control_database):
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
        _clear_live(connection)
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
        _clear_live(connection)


@pytest.fixture()
def seeded_live_task(live_database):
    environment, codec, owner_id, conversation_id, turn_id = live_database
    loop_repository = BrainLoopRepository(
        environment["urls"]["platform_brain_worker"], content_codec=codec
    )
    repository = CollaborationRepository(
        environment["urls"]["platform_brain_worker"], content_codec=codec
    )
    loop_id = uuid4()
    loop_repository.create_loop(
        loop_id=loop_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        model_config={"config_version": "brain-opus5-live-v1"},
        max_steps=24,
        max_tasks=8,
        max_duration_seconds=1800,
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
    lease = loop_repository.lease_step("brain-worker-live", lease_seconds=45)
    assert lease is not None
    delegate = _delegate_block()
    task_id = loop_repository.commit_model_step(
        loop_id,
        lease.step_seq,
        "brain-worker-live",
        ModelStepCommit(
            provider_request_id="msg_live_delegate",
            content_blocks=(delegate,),
            usage={"input_tokens": 100, "output_tokens": 20},
            cache_usage={},
            stop_reason="tool_use",
            batch=parse_tool_batch([delegate], ToolLimits()),
            task_specs=(
                TaskDispatchSpec(
                    tool_index=0,
                    adapter_kind="reference",
                    capability_version=1,
                    authorization_snapshot_id=snapshot_id,
                    effective_deadline_at=NOW + timedelta(minutes=10),
                ),
            ),
        ),
    ).task_ids[0]
    repository.create_task_session(
        task_id=task_id,
        child_session_id=f"child-{task_id}",
        adapter_kind="reference",
        adapter_session_ref={"remote_session_id": "remote-hr-1"},
        capability_snapshot={"supports_followup_message": True},
    )
    yield repository, loop_repository, loop_id, task_id, conversation_id


def _seed_wait_step(
    live_database, loop_id: UUID, *, task_id: UUID
) -> UUID:
    environment, codec, *_unused = live_database
    wait_step_id = uuid4()
    wait_tool_call_id = uuid4()
    arguments = codec.seal_json(
        f"brain-tool-call:{wait_tool_call_id}:arguments",
        {
            "task_ids": [str(task_id)],
            "wake_on": ["finding", "result"],
            "public_reason": "等待专业 Agent 的发现",
        },
    )
    immediate = codec.seal_json(
        "brain-tool-call:placeholder:result", {"status": "dispatched"}
    )
    with psycopg.connect(environment["admin"]) as connection:
        first = connection.execute(
            "select step_id from platform_brain.brain_steps "
            "where loop_id=%s and step_seq=1",
            (loop_id,),
        ).fetchone()[0]
        connection.execute(
            "update platform_brain.brain_tool_calls set status='result_ready',"
            "result_ciphertext=%s,result_key_version=%s,result_sha256=%s "
            "where step_id=%s",
            (immediate.ciphertext, immediate.key_version, b"r" * 32, first),
        )
        connection.execute(
            "update platform_brain.brain_steps set status='completed',"
            "terminal_at=clock_timestamp() where step_id=%s",
            (first,),
        )
        connection.execute(
            "insert into platform_brain.brain_steps "
            "(step_id,loop_id,step_seq,status) values (%s,%s,2,'waiting_tool_results')",
            (wait_step_id, loop_id),
        )
        connection.execute(
            "insert into platform_brain.brain_tool_calls "
            "(brain_tool_call_id,step_id,tool_index,provider_tool_call_id,"
            "tool_name,arguments_ciphertext,arguments_key_version,public_reason,status) "
            "values (%s,%s,0,'toolu_live_wait','await_agent_events',%s,%s,"
            "'等待专业 Agent 的发现','waiting_result')",
            (
                wait_tool_call_id,
                wait_step_id,
                arguments.ciphertext,
                arguments.key_version,
            ),
        )
    return wait_tool_call_id


@pytest.mark.postgres
def test_task_session_and_messages_are_encrypted_and_round_trip(
    live_database, seeded_live_task
) -> None:
    environment, *_unused = live_database
    repository, _loop_repository, _loop_id, task_id, _conversation_id = (
        seeded_live_task
    )
    session = repository.task_session(task_id)
    assert session.adapter_session_ref == {"remote_session_id": "remote-hr-1"}

    first = repository.append_task_message(
        AgentTaskMessageInput(
            task_id=task_id,
            seq=1,
            sender="brain",
            message_kind="initial",
            text="分析候选人的复合能力",
            created_at=NOW,
        )
    )
    replay = repository.append_task_message(first.input)
    assert replay.replayed is True
    assert repository.task_messages(task_id)[0].text == "分析候选人的复合能力"
    with psycopg.connect(environment["admin"]) as connection:
        raw = bytes(
            connection.execute(
                "select content_ciphertext from platform_brain.agent_task_messages "
                "where task_id=%s and seq=1",
                (task_id,),
            ).fetchone()[0]
        )
    assert "分析候选人".encode() not in raw


@pytest.mark.postgres
def test_messages_are_monotonic_conflict_safe_and_followups_are_capped(
    seeded_live_task,
) -> None:
    repository, _loop_repository, _loop_id, task_id, _conversation_id = (
        seeded_live_task
    )
    with pytest.raises(BrainRepositoryConflict):
        repository.append_task_message(
            AgentTaskMessageInput(
                task_id=task_id,
                seq=2,
                sender="brain",
                message_kind="followup",
                text="跳过第一条",
                created_at=NOW,
            )
        )
    for seq in range(1, 6):
        kind = "initial" if seq == 1 else "followup"
        repository.append_task_message(
            AgentTaskMessageInput(
                task_id=task_id,
                seq=seq,
                sender="brain",
                message_kind=kind,
                text=f"message-{seq}",
                created_at=NOW + timedelta(seconds=seq),
            )
        )
    with pytest.raises(BrainRepositoryConflict):
        repository.append_task_message(
            AgentTaskMessageInput(
                task_id=task_id,
                seq=6,
                sender="brain",
                message_kind="followup",
                text="第五次追问",
                created_at=NOW + timedelta(seconds=6),
            )
        )
    with pytest.raises(BrainRepositoryConflict):
        repository.append_task_message(
            AgentTaskMessageInput(
                task_id=task_id,
                seq=1,
                sender="brain",
                message_kind="initial",
                text="冲突重放",
                created_at=NOW,
            )
        )


@pytest.mark.postgres
def test_event_wakes_one_subscription_once(live_database, seeded_live_task) -> None:
    repository, _loop_repository, loop_id, task_id, _conversation_id = (
        seeded_live_task
    )
    tool_call_id = _seed_wait_step(live_database, loop_id, task_id=task_id)
    wait = repository.create_wait_subscription(
        WaitSubscriptionSpec(
            tool_call_id=tool_call_id,
            loop_id=loop_id,
            task_ids=(task_id,),
            wake_on=("finding", "result"),
            cursors={task_id: 0},
        )
    )
    event = AgentTaskPublicEventInput(
        task_id=task_id,
        seq=1,
        event_type="finding",
        payload={"summary": "发现跨公司能力组合"},
        created_at=NOW,
    )
    first = repository.append_task_event_and_wake(event)
    replay = repository.append_task_event_and_wake(event)
    assert first.woken_wait_id == wait.wait_id
    assert first.events[0].payload["summary"] == "发现跨公司能力组合"
    assert replay.replayed is True
    assert replay.woken_wait_id is None
    assert repository.queued_step_count(loop_id) == 1
    with pytest.raises(BrainRepositoryConflict):
        repository.append_task_event_and_wake(
            AgentTaskPublicEventInput(
                task_id=task_id,
                seq=1,
                event_type="finding",
                payload={"summary": "同序号但内容不同"},
                created_at=NOW,
            )
        )


@pytest.mark.postgres
def test_wait_rejects_foreign_task(live_database, seeded_live_task) -> None:
    repository, _loop_repository, loop_id, task_id, _conversation_id = (
        seeded_live_task
    )
    tool_call_id = _seed_wait_step(live_database, loop_id, task_id=task_id)
    with pytest.raises(BrainRepositoryConflict):
        repository.create_wait_subscription(
            WaitSubscriptionSpec(
                tool_call_id=tool_call_id,
                loop_id=uuid4(),
                task_ids=(task_id,),
                wake_on=("result",),
                cursors={task_id: 0},
            )
        )


@pytest.mark.postgres
def test_thinking_deltas_are_ordered_and_can_be_interrupted(
    live_database, seeded_live_task
) -> None:
    environment, *_unused = live_database
    repository, _loop_repository, loop_id, _task_id, _conversation_id = (
        seeded_live_task
    )
    with psycopg.connect(environment["admin"]) as connection:
        step_id = connection.execute(
            "select step_id from platform_brain.brain_steps "
            "where loop_id=%s and step_seq=1",
            (loop_id,),
        ).fetchone()[0]
    first = repository.append_thinking_delta(
        BrainThinkingDelta(step_id, 0, 1, "先比较能力", "msg_provider_live")
    )
    second = repository.append_thinking_delta(
        BrainThinkingDelta(step_id, 0, 2, "，再核验证据", "msg_provider_live")
    )
    assert first.text == "先比较能力"
    assert second.text == "先比较能力，再核验证据"
    with pytest.raises(BrainRepositoryConflict):
        repository.append_thinking_delta(
            BrainThinkingDelta(step_id, 0, 4, "跳号", "msg_provider_live")
        )
    interrupted = repository.finalize_thinking_summary(
        step_id, 0, interrupted=True
    )
    assert interrupted.status == "interrupted"


@pytest.mark.postgres
def test_intervention_is_claimed_once(live_database, seeded_live_task) -> None:
    environment, codec, _owner_id, conversation_id, _turn_id = live_database
    repository, _loop_repository, loop_id, _task_id, _conversation_id = (
        seeded_live_task
    )
    message_id = uuid4()
    sealed = codec.seal_json(
        message_subject(conversation_id, message_id), {"text": "只看深圳"}
    )
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.conversation_messages "
            "(message_id,conversation_id,seq,role,content_ciphertext,"
            "encryption_key_version,delivery_status) "
            "values (%s,%s,2,'user',%s,%s,'accepted')",
            (message_id, conversation_id, sealed.ciphertext, sealed.key_version),
        )
    intervention_id = uuid4()
    intervention = codec.seal_json(
        f"brain-intervention:{intervention_id}", {"text": "只看深圳"}
    )
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_brain.brain_user_interventions "
            "(intervention_id,loop_id,message_id,content_ciphertext,"
            "content_key_version,content_sha256,status,created_at) "
            "values (%s,%s,%s,%s,%s,%s,'pending',%s)",
            (
                intervention_id,
                loop_id,
                message_id,
                intervention.ciphertext,
                intervention.key_version,
                hashlib.sha256("只看深圳".encode()).digest(),
                NOW,
            ),
        )
        step_id = connection.execute(
            "select step_id from platform_brain.brain_steps "
            "where loop_id=%s and step_seq=1",
            (loop_id,),
        ).fetchone()[0]
    claimed = repository.claim_intervention(loop_id, step_id)
    assert claimed is not None
    assert claimed.text == "只看深圳"
    assert repository.claim_intervention(loop_id, step_id) is None


@pytest.mark.postgres
def test_crash_after_event_before_step_rolls_back_atomic_wake(
    live_database, seeded_live_task
) -> None:
    environment, *_unused = live_database
    repository, _loop_repository, loop_id, task_id, _conversation_id = (
        seeded_live_task
    )
    tool_call_id = _seed_wait_step(live_database, loop_id, task_id=task_id)
    wait = repository.create_wait_subscription(
        WaitSubscriptionSpec(
            tool_call_id=tool_call_id,
            loop_id=loop_id,
            task_ids=(task_id,),
            wake_on=("finding",),
            cursors={task_id: 0},
        )
    )
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "create function platform_brain.fail_live_step_insert() returns trigger "
            "language plpgsql as $$ begin raise exception 'simulated crash'; end $$"
        )
        connection.execute(
            "create trigger fail_live_step_insert before insert on "
            "platform_brain.brain_steps for each row execute function "
            "platform_brain.fail_live_step_insert()"
        )
    try:
        with pytest.raises(BrainRepositoryError):
            repository.append_task_event_and_wake(
                AgentTaskPublicEventInput(
                    task_id=task_id,
                    seq=1,
                    event_type="finding",
                    payload={"summary": "必须整体回滚"},
                    created_at=NOW,
                )
            )
    finally:
        with psycopg.connect(environment["admin"]) as connection:
            connection.execute(
                "drop trigger fail_live_step_insert on platform_brain.brain_steps"
            )
            connection.execute(
                "drop function platform_brain.fail_live_step_insert()"
            )
    with psycopg.connect(environment["admin"]) as connection:
        assert connection.execute(
            "select count(*) from platform_brain.agent_task_events "
            "where task_id=%s",
            (task_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "select status from platform_brain.brain_wait_subscriptions "
            "where wait_id=%s",
            (wait.wait_id,),
        ).fetchone()[0] == "active"
