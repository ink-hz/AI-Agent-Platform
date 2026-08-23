from __future__ import annotations

from uuid import uuid4

import psycopg
import pytest

from app.agent_brain.conversation_backfill import ConversationBackfill
from app.agent_brain.conversation_repository import ConversationRepository
from app.agent_brain.repository import MissionRepository
from test_agent_brain_conversation_repository import (
    _codec,
    conversation_database,
)
from test_control_plane_migration import control_database


def _resources(conversation_database):
    environment, owner, other = conversation_database
    codec = _codec()
    missions = MissionRepository(
        environment["urls"]["platform_control_app"], content_codec=codec
    )
    conversations = ConversationRepository(
        environment["urls"]["platform_control_app"],
        content_codec=codec,
        mission_repository=missions,
    )
    backfill = ConversationBackfill(
        environment["urls"]["platform_control_maintenance"],
        content_codec=codec,
    )
    return environment, owner, other, missions, conversations, backfill


@pytest.mark.postgres
def test_backfill_creates_one_conversation_per_legacy_mission_idempotently(
    conversation_database,
) -> None:
    environment, owner, other, missions, conversations, backfill = _resources(
        conversation_database
    )
    completed = missions.create_mission(
        owner,
        uuid4(),
        "请给出候选人搜索方案",
        mode="direct_agent",
        direct_agent_id="hr-bot",
    )
    run = missions.create_run(
        owner,
        completed.mission_id,
        phase="direct",
        agent_id="hr-bot",
        input_payload={"capability_card": {"agent_id": "hr-bot"}},
        objective="搜索候选人",
        event_type="task.dispatched",
        event_payload={"agent_id": "hr-bot"},
    )
    missions.complete_run(
        owner,
        completed.mission_id,
        run.run_id,
        status="completed",
        output_payload={"result": "ready"},
        event_type="mission.completed",
        event_payload={"text": "## 搜索方案\n\n从 GitHub 开始。"},
        mission_status="completed",
    )
    incomplete = missions.create_mission(other, uuid4(), "尚未完成的历史任务")
    with psycopg.connect(
        environment["admin"], row_factory=psycopg.rows.dict_row
    ) as connection:
        original = {
            row["mission_id"]: bytes(row["content_ciphertext"])
            for row in connection.execute(
                "select message.mission_id,message.content_ciphertext "
                "from platform_control.mission_messages message "
                "where message.mission_id=any(%s) and message.seq=1",
                ([completed.mission_id, incomplete.mission_id],),
            ).fetchall()
        }

    first = backfill.run(batch_size=1)
    second = backfill.run(batch_size=10)

    assert (first.scanned, first.created, first.quarantined) == (2, 2, 0)
    assert (second.scanned, second.created, second.quarantined) == (0, 0, 0)
    with psycopg.connect(
        environment["admin"], row_factory=psycopg.rows.dict_row
    ) as connection:
        rows = connection.execute(
            "select mission_id,owner_internal_user_id,conversation_id,turn_id,"
            "triggering_message_id from platform_control.missions "
            "where mission_id=any(%s) order by mission_id",
            ([completed.mission_id, incomplete.mission_id],),
        ).fetchall()
        assert all(
            row["conversation_id"]
            and row["turn_id"]
            and row["triggering_message_id"]
            for row in rows
        )
        assert {row["owner_internal_user_id"] for row in rows} == {owner, other}
        after = {
            row["mission_id"]: bytes(row["content_ciphertext"])
            for row in connection.execute(
                "select message.mission_id,message.content_ciphertext "
                "from platform_control.mission_messages message "
                "where message.mission_id=any(%s) and message.seq=1",
                ([completed.mission_id, incomplete.mission_id],),
            ).fetchall()
        }
    assert after == original

    completed_row = next(
        row for row in rows if row["mission_id"] == completed.mission_id
    )
    completed_messages = conversations.messages_after(
        owner, completed_row["conversation_id"]
    )
    assert [(message.role, message.content) for message in completed_messages] == [
        ("user", "请给出候选人搜索方案"),
        ("assistant", "## 搜索方案\n\n从 GitHub 开始。"),
    ]
    incomplete_row = next(
        row for row in rows if row["mission_id"] == incomplete.mission_id
    )
    incomplete_messages = conversations.messages_after(
        other, incomplete_row["conversation_id"]
    )
    assert incomplete_messages[0].content == "尚未完成的历史任务"
    assert incomplete_messages[1].role == "system"
    assert "升级前未完成" in incomplete_messages[1].content


@pytest.mark.postgres
def test_backfill_quarantines_corrupt_content_but_continues_other_owners(
    conversation_database,
) -> None:
    environment, owner, other, missions, _conversations, backfill = _resources(
        conversation_database
    )
    corrupt = missions.create_mission(owner, uuid4(), "损坏内容")
    healthy = missions.create_mission(other, uuid4(), "健康内容")
    with psycopg.connect(
        environment["admin"], row_factory=psycopg.rows.dict_row
    ) as connection:
        connection.execute(
            "update platform_control.mission_messages set content_ciphertext=%s "
            "where mission_id=%s and seq=1",
            (b"x" * 29, corrupt.mission_id),
        )

    report = backfill.run(batch_size=10)

    assert (report.scanned, report.created, report.quarantined) == (2, 1, 1)
    with psycopg.connect(environment["admin"]) as connection:
        links = dict(
            connection.execute(
                "select mission_id,conversation_id from platform_control.missions "
                "where mission_id=any(%s)",
                ([corrupt.mission_id, healthy.mission_id],),
            ).fetchall()
        )
    assert links[corrupt.mission_id] is None
    assert links[healthy.mission_id] is not None


@pytest.mark.postgres
def test_backfill_uses_stable_ids_after_a_committed_batch_retry(
    conversation_database,
) -> None:
    environment, owner, _other, missions, _conversations, backfill = _resources(
        conversation_database
    )
    legacy = [
        missions.create_mission(owner, uuid4(), f"历史任务 {index}")
        for index in range(3)
    ]

    first = backfill.run(batch_size=1, max_batches=1)
    with psycopg.connect(environment["admin"]) as connection:
        first_ids = connection.execute(
            "select mission_id,conversation_id from platform_control.missions "
            "where conversation_id is not null"
        ).fetchall()
    second = backfill.run(batch_size=1)
    third = backfill.run(batch_size=10)

    assert first.created == 1
    assert second.created == 2
    assert third.created == 0
    with psycopg.connect(environment["admin"]) as connection:
        final_ids = dict(
            connection.execute(
                "select mission_id,conversation_id from platform_control.missions "
                "where mission_id=any(%s)",
                ([mission.mission_id for mission in legacy],),
            ).fetchall()
        )
    assert dict(first_ids).items() <= final_ids.items()
