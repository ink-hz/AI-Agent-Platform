from __future__ import annotations

from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import psycopg
import pytest

from app.agent_brain.conversation_service import ConversationCommandService
from test_agent_brain_api import _credentials, _write_credentials
from test_agent_brain_conversation_api import _app, _post
from test_agent_brain_conversation_repository import (
    conversation_database,
    repository,
)
from test_control_plane_migration import control_database  # noqa: F401


def _v2_client(owner: UUID, repository):
    service = ConversationCommandService(repository, v2_enabled=True)
    app, auth, agent_use = _app(
        owner,
        repository,
        command_service=service,
    )
    return TestClient(app), auth, agent_use


def _loop_and_mission_counts(environment, turn_id: UUID) -> tuple[int, int, int]:
    with psycopg.connect(environment["admin"]) as connection:
        loop_count = connection.execute(
            "select count(*) from platform_brain.brain_loops where turn_id=%s",
            (turn_id,),
        ).fetchone()[0]
        step_count = connection.execute(
            "select count(*) from platform_brain.brain_steps step "
            "join platform_brain.brain_loops loop on loop.loop_id=step.loop_id "
            "where loop.turn_id=%s",
            (turn_id,),
        ).fetchone()[0]
        mission_count = connection.execute(
            "select count(*) from platform_control.missions where turn_id=%s",
            (turn_id,),
        ).fetchone()[0]
    return loop_count, step_count, mission_count


@pytest.mark.postgres
def test_v2_start_creates_turn_loop_and_first_step_without_mission(
    conversation_database,
    repository,
) -> None:
    environment, owner, _other = conversation_database
    client, auth, _agent_use = _v2_client(owner, repository)

    response = _post(client, auth, "/api/v1/conversations", "分析人才需求")

    assert response.status_code == 201
    turn = response.json()["turn"]
    assert "mission_id" not in turn
    assert turn["retry_of_turn_id"] is None
    assert _loop_and_mission_counts(environment, UUID(turn["turn_id"])) == (1, 1, 0)


@pytest.mark.postgres
def test_v2_start_replay_is_idempotent_and_payload_collision_is_409(
    conversation_database,
    repository,
) -> None:
    environment, owner, _other = conversation_database
    client, auth, _agent_use = _v2_client(owner, repository)
    request_id = uuid4()

    first = _post(client, auth, "/api/v1/conversations", "同一轮", request_id)
    replay = _post(client, auth, "/api/v1/conversations", "同一轮", request_id)
    collision = _post(client, auth, "/api/v1/conversations", "内容改变", request_id)

    assert first.status_code == 201
    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert collision.status_code == 409
    turn_id = UUID(first.json()["turn"]["turn_id"])
    assert _loop_and_mission_counts(environment, turn_id) == (1, 1, 0)


@pytest.mark.postgres
def test_v2_loop_insert_failure_rolls_back_conversation_message_and_turn(
    conversation_database,
    repository,
) -> None:
    environment, owner, _other = conversation_database
    client, auth, _agent_use = _v2_client(owner, repository)
    request_id = uuid4()
    with psycopg.connect(environment["admin"], autocommit=True) as connection:
        connection.execute(
            "revoke insert on platform_brain.brain_loops from platform_control_app"
        )
    try:
        response = _post(
            client,
            auth,
            "/api/v1/conversations",
            "必须整体回滚",
            request_id,
        )
    finally:
        with psycopg.connect(environment["admin"], autocommit=True) as connection:
            connection.execute(
                "grant insert on platform_brain.brain_loops to platform_control_app"
            )

    assert response.status_code == 503
    with psycopg.connect(environment["admin"]) as connection:
        counts = connection.execute(
            "select "
            "(select count(*) from platform_control.conversations "
            " where owner_internal_user_id=%s and started_by_client_request_id=%s),"
            "(select count(*) from platform_control.conversation_messages),"
            "(select count(*) from platform_control.conversation_turns)",
            (owner, request_id),
        ).fetchone()
    assert counts == (0, 0, 0)


@pytest.mark.postgres
def test_v2_append_reports_stable_turn_in_progress_and_rejects_archive(
    conversation_database,
    repository,
) -> None:
    environment, owner, _other = conversation_database
    client, auth, _agent_use = _v2_client(owner, repository)
    first = _post(client, auth, "/api/v1/conversations", "第一轮")
    conversation_id = first.json()["conversation"]["conversation_id"]

    overlap = _post(
        client,
        auth,
        f"/api/v1/conversations/{conversation_id}/messages",
        "并发追问",
    )
    assert overlap.status_code == 409
    assert overlap.json() == {
        "detail": {
            "code": "turn_in_progress",
            "message": "当前对话已有一轮正在执行",
        }
    }

    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_control.conversation_turns set status='failed' "
            "where turn_id=%s",
            (UUID(first.json()["turn"]["turn_id"]),),
        )
        connection.execute(
            "update platform_brain.brain_loops set status='failed',"
            "reason_code='test_failure',terminal_at=now() where turn_id=%s",
            (UUID(first.json()["turn"]["turn_id"]),),
        )
    archived = client.post(
        f"/api/v1/conversations/{conversation_id}/archive",
        **_write_credentials(auth),
    )
    assert archived.status_code == 200
    rejected = _post(
        client,
        auth,
        f"/api/v1/conversations/{conversation_id}/messages",
        "归档后不可继续",
    )
    assert rejected.status_code == 409


@pytest.mark.postgres
def test_v2_retry_creates_linked_turn_and_preserves_failed_turn(
    conversation_database,
    repository,
) -> None:
    environment, owner, other = conversation_database
    client, auth, _agent_use = _v2_client(owner, repository)
    first = _post(client, auth, "/api/v1/conversations", "需要重试")
    conversation_id = first.json()["conversation"]["conversation_id"]
    failed_turn_id = UUID(first.json()["turn"]["turn_id"])
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_control.conversation_turns set status='failed' "
            "where turn_id=%s",
            (failed_turn_id,),
        )
        connection.execute(
            "update platform_brain.brain_loops set status='failed',"
            "reason_code='provider_failed',terminal_at=now() where turn_id=%s",
            (failed_turn_id,),
        )

    request_id = uuid4()
    retry = client.post(
        f"/api/v1/conversations/{conversation_id}/turns/{failed_turn_id}/retry",
        headers={
            **_write_credentials(auth)["headers"],
            "Idempotency-Key": str(request_id),
        },
        cookies=_credentials(auth)["cookies"],
    )
    replay = client.post(
        f"/api/v1/conversations/{conversation_id}/turns/{failed_turn_id}/retry",
        headers={
            **_write_credentials(auth)["headers"],
            "Idempotency-Key": str(request_id),
        },
        cookies=_credentials(auth)["cookies"],
    )

    assert retry.status_code == 201
    assert replay.status_code == 200 and replay.json() == retry.json()
    new_turn = retry.json()["turn"]
    assert new_turn["retry_of_turn_id"] == str(failed_turn_id)
    assert new_turn["turn_id"] != str(failed_turn_id)
    assert "mission_id" not in new_turn
    assert _loop_and_mission_counts(environment, UUID(new_turn["turn_id"])) == (1, 1, 0)
    with psycopg.connect(environment["admin"]) as connection:
        statuses = dict(connection.execute(
            "select status,retry_of_turn_id from platform_control.conversation_turns "
            "where turn_id in (%s,%s)",
            (failed_turn_id, UUID(new_turn["turn_id"])),
        ).fetchall())
    assert statuses == {
        "failed": None,
        "accepted": failed_turn_id,
    }

    other_app, other_auth, _ = _app(
        other,
        repository,
        command_service=ConversationCommandService(repository, v2_enabled=True),
    )
    denied = TestClient(other_app).post(
        f"/api/v1/conversations/{conversation_id}/turns/{failed_turn_id}/retry",
        headers={
            **_write_credentials(other_auth)["headers"],
            "Idempotency-Key": str(uuid4()),
        },
        cookies=_credentials(other_auth)["cookies"],
    )
    assert denied.status_code == 404


@pytest.mark.postgres
def test_v2_retry_rejects_active_and_successful_turns(
    conversation_database,
    repository,
) -> None:
    environment, owner, _other = conversation_database
    client, auth, _agent_use = _v2_client(owner, repository)
    first = _post(client, auth, "/api/v1/conversations", "不能乱重试")
    conversation_id = first.json()["conversation"]["conversation_id"]
    turn_id = UUID(first.json()["turn"]["turn_id"])

    def retry():
        return client.post(
            f"/api/v1/conversations/{conversation_id}/turns/{turn_id}/retry",
                headers={
                    **_write_credentials(auth)["headers"],
                "Idempotency-Key": str(uuid4()),
            },
            cookies=_credentials(auth)["cookies"],
        )

    assert retry().status_code == 409
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_control.conversation_turns set status='completed' "
            "where turn_id=%s",
            (turn_id,),
        )
        connection.execute(
            "update platform_brain.brain_loops set status='completed',"
            "outcome='resolved',terminal_at=now() where turn_id=%s",
            (turn_id,),
        )
    assert retry().status_code == 409


@pytest.mark.postgres
def test_direct_agent_conversation_still_uses_v1_mission_path(
    conversation_database,
    repository,
) -> None:
    environment, owner, _other = conversation_database
    client, auth, _agent_use = _v2_client(owner, repository)

    response = _post(
        client,
        auth,
        "/api/v1/agents/hr-bot/conversations",
        "直接评估简历",
    )

    assert response.status_code == 201
    turn = response.json()["turn"]
    assert "mission_id" not in turn
    assert _loop_and_mission_counts(environment, UUID(turn["turn_id"])) == (0, 0, 1)


@pytest.mark.postgres
def test_waiting_user_reply_resumes_same_turn_through_message_api(
    conversation_database,
    repository,
) -> None:
    environment, owner, _other = conversation_database
    client, auth, _agent_use = _v2_client(owner, repository)
    first = _post(client, auth, "/api/v1/conversations", "先问我岗位级别")
    conversation_id = UUID(first.json()["conversation"]["conversation_id"])
    turn_id = UUID(first.json()["turn"]["turn_id"])
    from app.agent_brain.loop_repository import BrainLoopRepository
    from test_agent_brain_loop_runtime import _request_user_response, _runtime

    loops = BrainLoopRepository(
        environment["urls"]["platform_brain_worker"],
        content_codec=repository.content_codec,
    )
    assert _runtime(loops, _request_user_response()).advance_one() is True
    request_id = uuid4()
    reply = _post(
        client,
        auth,
        f"/api/v1/conversations/{conversation_id}/messages",
        "高级工程师",
        request_id,
    )
    replay = _post(
        client,
        auth,
        f"/api/v1/conversations/{conversation_id}/messages",
        "高级工程师",
        request_id,
    )
    assert reply.status_code == 201
    assert replay.status_code == 200 and replay.json() == reply.json()
    assert UUID(reply.json()["turn"]["turn_id"]) == turn_id
    assert reply.json()["message"]["message_id"] == str(request_id)
