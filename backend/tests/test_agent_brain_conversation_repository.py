from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import get_type_hints
from uuid import UUID, uuid4

import psycopg
import pytest

from app.agent_brain.conversation_repository import (
    ConversationRepository,
    ConversationRepositoryConflict,
    ConversationRepositoryNotFound,
    ConversationRepositoryError,
)
from app.agent_brain.repository import MissionRepository
from app.control_plane.crypto import IdentityKeyring
from app.execution_relay.content_crypto import ContentCodec
from test_control_plane_migration import control_database


def _codec() -> ContentCodec:
    return ContentCodec(
        IdentityKeyring(
            active_version=4,
            purpose="platform-content-encryption",
            _keys={3: b"3" * 32, 4: b"4" * 32},
        )
    )


def _clear_conversations(connection) -> None:
    connection.execute("set constraints all deferred")
    connection.execute("delete from platform_control.conversation_events")
    connection.execute("delete from platform_control.mission_events")
    connection.execute("delete from platform_control.mission_runs")
    connection.execute("delete from platform_control.mission_tasks")
    connection.execute("delete from platform_control.mission_messages")
    connection.execute("delete from platform_control.missions")
    connection.execute("delete from platform_control.conversation_messages")
    connection.execute("delete from platform_control.conversation_turns")
    connection.execute("delete from platform_control.conversations")


@pytest.fixture()
def conversation_database(control_database):
    environment = control_database["environments"]["production"]
    owner_id = uuid4()
    other_owner_id = uuid4()
    with psycopg.connect(environment["admin"]) as connection:
        _clear_conversations(connection)
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id,display_name,status) values "
            "(%s,'Conversation Owner','active'),"
            "(%s,'Other Conversation Owner','active')",
            (owner_id, other_owner_id),
        )
    yield environment, owner_id, other_owner_id
    with psycopg.connect(environment["admin"]) as connection:
        _clear_conversations(connection)


@pytest.fixture()
def repository(conversation_database) -> ConversationRepository:
    environment, _owner_id, _other_owner_id = conversation_database
    codec = _codec()
    missions = MissionRepository(
        environment["urls"]["platform_control_app"],
        content_codec=codec,
    )
    return ConversationRepository(
        environment["urls"]["platform_control_app"],
        content_codec=codec,
        mission_repository=missions,
    )


def _complete_turn(environment, turn_id: UUID) -> None:
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_control.conversation_turns "
            "set status='completed',updated_at=now() where turn_id=%s",
            (turn_id,),
        )


@pytest.mark.postgres
def test_start_is_atomic_and_ciphertext_only(
    conversation_database,
    repository,
) -> None:
    environment, owner_id, _ = conversation_database
    result = repository.start(
        owner_id,
        uuid4(),
        "帮我找视觉算法候选人",
        mode="brain",
        direct_agent_id=None,
    )

    assert result.created is True
    assert result.conversation.owner_internal_user_id == owner_id
    assert result.message.content == "帮我找视觉算法候选人"
    assert result.message.seq == 1
    assert result.turn.mission_id == result.mission.mission_id
    assert result.mission.conversation_id == result.conversation.conversation_id
    assert result.mission.turn_id == result.turn.turn_id
    assert result.mission.triggering_message_id == result.message.message_id

    with psycopg.connect(environment["admin"]) as connection:
        counts = connection.execute(
            "select "
            "(select count(*) from platform_control.conversations),"
            "(select count(*) from platform_control.conversation_messages),"
            "(select count(*) from platform_control.conversation_turns),"
            "(select count(*) from platform_control.missions)"
        ).fetchone()
        ciphertext = bytes(
            connection.execute(
                "select content_ciphertext from "
                "platform_control.conversation_messages"
            ).fetchone()[0]
        )
    assert counts == (1, 1, 1, 1)
    assert "视觉算法".encode() not in ciphertext


@pytest.mark.postgres
def test_start_replays_same_request_and_rejects_changed_payload(
    conversation_database,
    repository,
) -> None:
    _environment, owner_id, _ = conversation_database
    request_id = uuid4()
    first = repository.start(owner_id, request_id, "定义候选人画像")
    replay = repository.start(owner_id, request_id, "定义候选人画像")

    assert replay.created is False
    assert replay.conversation.conversation_id == first.conversation.conversation_id
    assert replay.message.message_id == first.message.message_id
    assert replay.turn.turn_id == first.turn.turn_id
    assert replay.mission.mission_id == first.mission.mission_id

    with pytest.raises(ConversationRepositoryConflict):
        repository.start(owner_id, request_id, "换成另一个需求")


@pytest.mark.postgres
def test_concurrent_start_replays_one_atomic_result(
    conversation_database,
    repository,
) -> None:
    environment, owner_id, _ = conversation_database
    request_id = uuid4()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _index: repository.start(
                    owner_id, request_id, "并发也只能创建一次"
                ),
                range(2),
            )
        )

    assert sorted(result.created for result in results) == [False, True]
    assert len({result.conversation.conversation_id for result in results}) == 1
    assert len({result.turn.turn_id for result in results}) == 1
    with psycopg.connect(environment["admin"]) as connection:
        assert connection.execute(
            "select count(*) from platform_control.conversations "
            "where owner_internal_user_id=%s and started_by_client_request_id=%s",
            (owner_id, request_id),
        ).fetchone() == (1,)


@pytest.mark.postgres
def test_conversations_are_owner_scoped_and_listed_by_recent_update(
    conversation_database,
    repository,
) -> None:
    _environment, owner_id, other_owner_id = conversation_database
    first = repository.start(owner_id, uuid4(), "第一条")
    second = repository.start(owner_id, uuid4(), "第二条")
    repository.start(other_owner_id, uuid4(), "其他人的秘密")

    assert repository.conversation_for_owner(
        owner_id, first.conversation.conversation_id
    ).title == "第一条"
    with pytest.raises(ConversationRepositoryNotFound):
        repository.conversation_for_owner(
            other_owner_id, first.conversation.conversation_id
        )
    assert [item.conversation_id for item in repository.list_for_owner(owner_id)] == [
        second.conversation.conversation_id,
        first.conversation.conversation_id,
    ]


@pytest.mark.postgres
def test_append_turn_is_monotonic_and_blocks_overlap_or_archive(
    conversation_database,
    repository,
) -> None:
    environment, owner_id, _ = conversation_database
    first = repository.start(owner_id, uuid4(), "第一轮")
    conversation_id = first.conversation.conversation_id

    with pytest.raises(ConversationRepositoryConflict):
        repository.append_turn(owner_id, conversation_id, uuid4(), "过早追问")

    _complete_turn(environment, first.turn.turn_id)
    second = repository.append_turn(owner_id, conversation_id, uuid4(), "继续")
    assert second.created is True
    assert second.message.seq == 2
    assert [message.content for message in repository.messages_after(owner_id, conversation_id)] == [
        "第一轮",
        "继续",
    ]

    _complete_turn(environment, second.turn.turn_id)
    archived = repository.archive(owner_id, conversation_id)
    assert archived.status == "archived"
    assert archived.archived_at is not None
    with pytest.raises(ConversationRepositoryConflict):
        repository.append_turn(owner_id, conversation_id, uuid4(), "不能继续")


@pytest.mark.postgres
def test_append_replay_is_idempotent_and_title_is_safely_truncated(
    conversation_database,
    repository,
) -> None:
    environment, owner_id, _ = conversation_database
    first = repository.start(owner_id, uuid4(), " 标题 " + "长" * 200)
    assert first.conversation.title.startswith("标题")
    assert len(first.conversation.title) == 160
    _complete_turn(environment, first.turn.turn_id)

    request_id = uuid4()
    appended = repository.append_turn(
        owner_id, first.conversation.conversation_id, request_id, "继续"
    )
    replay = repository.append_turn(
        owner_id, first.conversation.conversation_id, request_id, "继续"
    )
    assert replay.created is False
    assert replay.turn.turn_id == appended.turn.turn_id
    with pytest.raises(ConversationRepositoryConflict):
        repository.append_turn(
            owner_id, first.conversation.conversation_id, request_id, "内容变了"
        )


@pytest.mark.postgres
def test_direct_agent_replay_cannot_change_agent(
    conversation_database,
    repository,
) -> None:
    _environment, owner_id, _ = conversation_database
    request_id = uuid4()
    repository.start(
        owner_id,
        request_id,
        "评估简历",
        mode="direct_agent",
        direct_agent_id="hr-bot",
    )
    with pytest.raises(ConversationRepositoryConflict):
        repository.start(
            owner_id,
            request_id,
            "评估简历",
            mode="direct_agent",
            direct_agent_id="fae-bot",
        )


@pytest.mark.postgres
def test_mission_insert_failure_rolls_back_entire_start(
    conversation_database,
    repository,
) -> None:
    environment, owner_id, _ = conversation_database
    request_id = uuid4()
    with psycopg.connect(environment["admin"], autocommit=True) as connection:
        connection.execute(
            "revoke insert on platform_control.missions from platform_control_app"
        )
    try:
        with pytest.raises(ConversationRepositoryError):
            repository.start(owner_id, request_id, "必须整体回滚")
    finally:
        with psycopg.connect(environment["admin"], autocommit=True) as connection:
            connection.execute(
                "grant insert on platform_control.missions to platform_control_app"
            )

    with psycopg.connect(environment["admin"]) as connection:
        assert connection.execute(
            "select count(*) from platform_control.conversations "
            "where owner_internal_user_id=%s and started_by_client_request_id=%s",
            (owner_id, request_id),
        ).fetchone() == (0,)


def test_record_contracts_are_immutable_and_typed() -> None:
    from app.agent_brain.conversation_models import (
        ConversationMessageRecord,
        ConversationRecord,
        ConversationTurnRecord,
    )

    assert ConversationRecord.__dataclass_params__.frozen is True
    assert ConversationMessageRecord.__dataclass_params__.frozen is True
    assert ConversationTurnRecord.__dataclass_params__.frozen is True
    assert get_type_hints(ConversationRecord)["conversation_id"] is UUID
    assert get_type_hints(ConversationMessageRecord)["created_at"] is datetime
    assert get_type_hints(ConversationTurnRecord)["updated_at"] is datetime
    assert datetime.now(timezone.utc).tzinfo is not None
