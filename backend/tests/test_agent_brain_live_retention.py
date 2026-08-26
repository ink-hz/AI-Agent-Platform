from __future__ import annotations

import psycopg
import pytest

from app.agent_brain.collaboration_models import (
    AgentTaskPublicEventInput,
    BrainThinkingDelta,
)
from app.agent_brain.conversation_repository import message_subject
from app.execution_relay.content_crypto import SealedContent
from test_agent_brain_loop_repository import NOW, _commit, _lease, loop_database, loop_repository, seeded_loop
from test_control_plane_migration import control_database


@pytest.mark.postgres
def test_archived_conversation_content_is_tombstoned_only_after_365_days(
    loop_database,
    loop_repository,
    seeded_loop,
) -> None:
    environment, codec, _owner, conversation_id, _turn_id = loop_database
    _loop_id, snapshot_id = seeded_loop
    lease = _lease(loop_repository)
    committed = loop_repository.commit_model_step(
        lease.loop_id, lease.step_seq, "brain-worker-a", _commit(snapshot_id)
    )
    task_id = committed.task_ids[0]
    collaboration = loop_repository.collaboration_repository()
    collaboration.append_task_event_and_wake(AgentTaskPublicEventInput(
        task_id=task_id, seq=1, event_type="work_update",
        payload={"kind": "finding", "summary": "需要擦除的专业发现"}, created_at=NOW,
    ))
    collaboration.append_thinking_delta(BrainThinkingDelta(
        step_id=lease.step_id, block_index=0, delta_seq=1,
        text="需要擦除的思考摘要", provider_run_ref="provider-run-1",
    ))
    collaboration.finalize_thinking_summary(lease.step_id, 0)

    with psycopg.connect(environment["admin"]) as connection:
        before = connection.execute(
            "select message.content_sha256,event.payload_sha256,message.created_at,"
            "event.created_at,task.status from platform_brain.agent_tasks task "
            "join platform_brain.agent_task_messages message on message.task_id=task.task_id "
            "join platform_brain.agent_task_events event on event.task_id=task.task_id "
            "where task.task_id=%s",
            (task_id,),
        ).fetchone()
        connection.execute(
            "update platform_control.conversations set status='archived',"
            "archived_at=clock_timestamp()-interval '364 days' where conversation_id=%s",
            (conversation_id,),
        )

    assert loop_repository.erase_expired_conversations(limit=100) == 0

    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_control.conversations set "
            "archived_at=clock_timestamp()-interval '366 days' where conversation_id=%s",
            (conversation_id,),
        )

    assert loop_repository.erase_expired_conversations(limit=100) == 1
    assert loop_repository.erase_expired_conversations(limit=100) == 0

    with psycopg.connect(environment["admin"]) as connection:
        conversation = connection.execute(
            "select title,summary_ciphertext,summary_key_version from "
            "platform_control.conversations where conversation_id=%s",
            (conversation_id,),
        ).fetchone()
        source_message = connection.execute(
            "select message_id,content_ciphertext,encryption_key_version from "
            "platform_control.conversation_messages where conversation_id=%s order by seq limit 1",
            (conversation_id,),
        ).fetchone()
        child = connection.execute(
            "select message.content_ciphertext,message.content_key_version,"
            "message.content_sha256,event.payload_ciphertext,event.payload_key_version,"
            "event.payload_sha256,message.created_at,event.created_at,task.status "
            "from platform_brain.agent_tasks task "
            "join platform_brain.agent_task_messages message on message.task_id=task.task_id "
            "join platform_brain.agent_task_events event on event.task_id=task.task_id "
            "where task.task_id=%s",
            (task_id,),
        ).fetchone()
        thinking = connection.execute(
            "select summary_ciphertext,summary_key_version,status from "
            "platform_brain.brain_thinking_summaries where step_id=%s and block_index=0",
            (lease.step_id,),
        ).fetchone()

    assert conversation == ("[内容已按保留策略清除]", None, None)
    assert codec.unseal_json(
        message_subject(conversation_id, source_message[0]),
        SealedContent(bytes(source_message[1]), source_message[2]),
    ) == {"text": "[内容已按保留策略清除]"}
    assert codec.unseal_json(
        f"brain-task:{task_id}:message:1",
        SealedContent(bytes(child[0]), child[1]),
    ) == {"text": "[内容已按保留策略清除]"}
    assert codec.unseal_json(
        f"brain-task:{task_id}:event:1:payload",
        SealedContent(bytes(child[3]), child[4]),
    ) == {"status": "retention_erased"}
    assert codec.unseal_json(
        f"brain-step:{lease.step_id}:thinking:0",
        SealedContent(bytes(thinking[0]), thinking[1]),
    ) == {"text": "[内容已按保留策略清除]"}
    assert child[2] == before[0]
    assert child[5] == before[1]
    assert child[6:] == before[2:]
    assert thinking[2] == "completed"


def test_retention_limit_is_strict(loop_repository) -> None:
    for value in (0, 101, True):
        with pytest.raises(ValueError, match="retention limit invalid"):
            loop_repository.erase_expired_conversations(limit=value)
