from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.agent_brain.conversation_projection import ConversationProjection
from app.agent_brain.conversation_models import ConversationEventRecord
from app.agent_brain.conversation_routes import (
    ConversationCursorCodec,
    build_conversation_router,
    conversation_event_stream,
)
from app.agent_brain.routes import MissionStreamLimiter
from app.control_plane.authorization import AuthorizationService
from app.control_plane.auth import AuthSecrets
from app.control_plane.middleware import IdentitySecurityMiddleware
from app.control_plane.models import AuthContext, Role
from test_agent_brain_api import (
    FakeAgentUse,
    FakeAuth,
    NoManagementGrants,
    _credentials,
    _write_credentials,
)
from test_agent_brain_conversation_repository import (
    conversation_database,
    repository,
)
from test_control_plane_migration import control_database


def _app(
    owner: UUID,
    conversations,
    *,
    role: Role = Role.MEMBER,
    agent_use: FakeAgentUse | None = None,
):
    context = AuthContext(owner, role, uuid4(), False)
    auth = FakeAuth(context)
    auth.hard_stale_audit = lambda *_args: None
    agent_use = agent_use or FakeAgentUse()
    app = FastAPI()
    app.include_router(
        build_conversation_router(
            conversations,
            agent_use,
            cursor_codec=ConversationCursorCodec(
                AuthSecrets(b"x" * 32, key_version=1)
            ),
            session_revalidator=auth.authenticate,
            session_cookie_name=auth.cookie_name,
            heartbeat_seconds=0.001,
            poll_seconds=0,
        )
    )
    app.add_middleware(
        IdentitySecurityMiddleware,
        auth=auth,
        public_assets=frozenset(),
        authorization=AuthorizationService(NoManagementGrants()),
        routes=tuple(app.router.routes),
    )
    return app, auth, agent_use


def _post(client, auth, path: str, text: str, request_id: UUID | None = None):
    return client.post(
        path,
        headers={
            **_write_credentials(auth)["headers"],
            "Idempotency-Key": str(request_id or uuid4()),
        },
        cookies=_credentials(auth)["cookies"],
        json={"text": text},
    )


def _fail_and_project(conversations, owner: UUID, mission_id: UUID) -> None:
    conversations._missions.terminate_mission(
        owner,
        mission_id,
        status="failed",
        event_type="mission.failed",
        event_payload={"text": "本轮执行失败", "reason_code": "test_failure"},
    )
    assert ConversationProjection(conversations).project_terminal(mission_id)


@pytest.mark.postgres
def test_follow_up_reuses_one_conversation_and_one_history_item(
    conversation_database,
    repository,
) -> None:
    _environment, owner, _ = conversation_database
    app, auth, _agent_use = _app(owner, repository)
    client = TestClient(app)
    first = _post(client, auth, "/api/v1/conversations", "定义候选人画像")

    assert first.status_code == 201
    conversation_id = UUID(first.json()["conversation"]["conversation_id"])
    mission_id = UUID(first.json()["turn"]["mission_id"])
    _fail_and_project(repository, owner, mission_id)
    second = _post(
        client,
        auth,
        f"/api/v1/conversations/{conversation_id}/messages",
        "继续，给出 GitHub 搜索式",
    )

    assert second.status_code == 201
    assert second.json()["conversation"]["conversation_id"] == str(
        conversation_id
    )
    history = client.get("/api/v1/conversations", **_credentials(auth))
    assert history.status_code == 200
    assert len(history.json()["items"]) == 1
    messages = client.get(
        f"/api/v1/conversations/{conversation_id}/messages",
        **_credentials(auth),
    )
    assert [item["content"] for item in messages.json()["items"]] == [
        "定义候选人画像",
        "本轮执行失败",
        "继续，给出 GitHub 搜索式",
    ]


@pytest.mark.postgres
def test_conversation_routes_enforce_auth_origin_csrf_and_owner(
    conversation_database,
    repository,
) -> None:
    _environment, owner, other = conversation_database
    foreign = repository.start(other, uuid4(), "其他人的对话")
    app, auth, _agent_use = _app(owner, repository)
    client = TestClient(app)

    assert client.get("/api/v1/conversations").status_code == 401
    assert client.post(
        "/api/v1/conversations",
        headers={"Idempotency-Key": str(uuid4())},
        json={"text": "无身份"},
    ).status_code == 401
    assert client.post(
        "/api/v1/conversations",
        headers={
            "Idempotency-Key": str(uuid4()),
            "Origin": "https://evil.example",
            "X-CSRF-Token": "csrf-value",
        },
        cookies=_credentials(auth)["cookies"],
        json={"text": "恶意来源"},
    ).status_code == 403
    assert client.get(
        f"/api/v1/conversations/{foreign.conversation.conversation_id}",
        **_credentials(auth),
    ).status_code == 404


@pytest.mark.postgres
def test_idempotency_overlap_archive_and_input_boundaries_are_explicit(
    conversation_database,
    repository,
) -> None:
    _environment, owner, _ = conversation_database
    app, auth, _agent_use = _app(owner, repository)
    client = TestClient(app)
    request_id = uuid4()
    first = _post(client, auth, "/api/v1/conversations", "同一请求", request_id)
    replay = _post(client, auth, "/api/v1/conversations", "同一请求", request_id)
    collision = _post(client, auth, "/api/v1/conversations", "内容改变", request_id)
    conversation_id = first.json()["conversation"]["conversation_id"]

    assert first.status_code == 201
    assert replay.status_code == 200 and replay.json() == first.json()
    assert collision.status_code == 409
    assert _post(
        client,
        auth,
        f"/api/v1/conversations/{conversation_id}/messages",
        "并发追问",
    ).status_code == 409
    assert client.post(
        f"/api/v1/conversations/{conversation_id}/archive",
        **_write_credentials(auth),
    ).status_code == 409
    assert _post(
        client,
        auth,
        "/api/v1/conversations",
        "😀" * 8193,
    ).status_code == 413


@pytest.mark.postgres
def test_direct_agent_authorization_is_rechecked_for_every_turn(
    conversation_database,
    repository,
) -> None:
    _environment, owner, _ = conversation_database
    grants = FakeAgentUse()
    app, auth, _agent_use = _app(owner, repository, agent_use=grants)
    client = TestClient(app)
    first = _post(
        client,
        auth,
        "/api/v1/agents/hr-bot/conversations",
        "评估简历",
    )
    assert first.status_code == 201
    _fail_and_project(
        repository, owner, UUID(first.json()["turn"]["mission_id"])
    )

    grants.cards = ()
    denied = _post(
        client,
        auth,
        "/api/v1/conversations/"
        f"{first.json()['conversation']['conversation_id']}/messages",
        "继续评估",
    )
    assert denied.status_code == 403


@pytest.mark.postgres
def test_history_is_newest_first_paginated_and_cursor_is_owner_bound(
    conversation_database,
    repository,
) -> None:
    _environment, owner, other = conversation_database
    app, auth, _agent_use = _app(owner, repository)
    client = TestClient(app)
    for text in ("旧对话", "中间对话", "新对话"):
        assert _post(client, auth, "/api/v1/conversations", text).status_code == 201

    first = client.get("/api/v1/conversations?limit=2", **_credentials(auth))
    assert [item["title"] for item in first.json()["items"]] == [
        "新对话",
        "中间对话",
    ]
    cursor = first.json()["next_cursor"]
    second = client.get(
        f"/api/v1/conversations?limit=2&before={cursor}",
        **_credentials(auth),
    )
    assert [item["title"] for item in second.json()["items"]] == ["旧对话"]

    other_app, other_auth, _ = _app(other, repository)
    reused = TestClient(other_app).get(
        f"/api/v1/conversations?before={cursor}",
        **_credentials(other_auth),
    )
    assert reused.status_code == 422


@pytest.mark.postgres
def test_terminal_sse_replays_monotonic_conversation_events_and_closes(
    conversation_database,
    repository,
) -> None:
    _environment, owner, _ = conversation_database
    app, auth, _agent_use = _app(owner, repository)
    client = TestClient(app)
    started = _post(client, auth, "/api/v1/conversations", "事件流")
    conversation_id = started.json()["conversation"]["conversation_id"]
    mission_id = UUID(started.json()["turn"]["mission_id"])
    planning = repository._missions.create_run(
        owner,
        mission_id,
        phase="planning",
        agent_id="agent-brain-bot",
        input_payload={"prompt": "plan"},
        event_type="brain.responding",
        event_payload={"text": "正在分析"},
    )
    repository._missions.complete_run(
        owner,
        mission_id,
        planning.run_id,
        status="failed",
        output_payload={"reason": "test"},
        event_type="mission.failed",
        event_payload={"text": "本轮执行失败", "reason_code": "test_failure"},
        mission_status="failed",
    )
    assert ConversationProjection(repository).project_terminal(mission_id)

    response = client.get(
        f"/api/v1/conversations/{conversation_id}/events?after=1",
        **_credentials(auth),
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    frames = [line for line in response.text.splitlines() if line.startswith("id: ")]
    sequences = [int(line.removeprefix("id: ")) for line in frames]
    assert sequences == sorted(sequences)
    assert sequences and min(sequences) > 1
    assert "event: conversation" in response.text
    data = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert all(item["conversation_id"] == conversation_id for item in data)
    assert all("content" not in item["payload"] for item in data)
    assert any(item["event_type"] == "brain.responding" for item in data)


@pytest.mark.anyio
async def test_terminal_sse_projects_every_batch_before_closing() -> None:
    owner = uuid4()
    session_id = uuid4()
    conversation_id = uuid4()
    turn_id = uuid4()
    mission_id = uuid4()
    all_events = tuple(
        ConversationEventRecord(
            event_id=uuid4(),
            conversation_id=conversation_id,
            seq=sequence,
            turn_id=turn_id,
            mission_id=mission_id,
            event_type="agent.progress",
            created_at=datetime.now(timezone.utc),
            payload={"text": f"进度 {sequence}"},
        )
        for sequence in range(1, 206)
    )

    class BatchedTerminalRepository:
        def __init__(self) -> None:
            self.projected = 0

        def conversation_for_owner(self, selected_owner, selected_conversation):
            assert (selected_owner, selected_conversation) == (owner, conversation_id)
            return object()

        def sync_mission_events(self, selected_owner, selected_conversation, *, limit):
            assert (selected_owner, selected_conversation) == (owner, conversation_id)
            added = min(limit, len(all_events) - self.projected)
            self.projected += added
            return added

        def events_after(self, selected_owner, selected_conversation, *, after, limit):
            assert (selected_owner, selected_conversation) == (owner, conversation_id)
            return tuple(
                event
                for event in all_events[: self.projected]
                if event.seq > after
            )[:limit]

        def active_turn_for_owner(self, selected_owner, selected_conversation):
            assert (selected_owner, selected_conversation) == (owner, conversation_id)
            return None

    async def connected() -> bool:
        return False

    repository = BatchedTerminalRepository()
    context = AuthContext(owner, Role.MEMBER, session_id, False)
    stream = conversation_event_stream(
        repository,
        owner,
        conversation_id,
        after=0,
        is_disconnected=connected,
        limiter=MissionStreamLimiter(max_per_owner=1, max_per_mission=1),
        session_revalidator=lambda _token: (context, None),
        session_token="valid",
        expected_session_id=session_id,
        poll_seconds=0,
    )

    frames = [frame async for frame in stream]

    assert repository.projected == 205
    assert len(frames) == 205
    assert frames[-1].startswith("id: 205\n")


@pytest.mark.postgres
def test_hard_stale_owner_can_read_but_cannot_mutate(
    conversation_database,
    repository,
) -> None:
    _environment, owner, _ = conversation_database
    app, auth, _agent_use = _app(
        owner, repository, role=Role.PLATFORM_OWNER
    )
    auth.context = replace(auth.context, hard_stale_read_only=True)
    client = TestClient(app)

    assert client.get("/api/v1/conversations", **_credentials(auth)).status_code == 200
    blocked = _post(client, auth, "/api/v1/conversations", "禁止写入")
    assert blocked.status_code == 503
    assert blocked.headers["cache-control"] == "no-store"


@pytest.mark.postgres
def test_feedback_api_binds_only_the_owned_assistant_message(
    conversation_database,
    repository,
) -> None:
    environment, owner, other = conversation_database
    from test_agent_brain_conversation_context import _complete_mission

    started = repository.start(owner, uuid4(), "给出候选人搜索方案")
    _complete_mission(
        environment,
        repository,
        started.mission.mission_id,
        "最终候选人搜索方案",
    )
    assert ConversationProjection(repository).project_terminal(
        started.mission.mission_id
    )
    assistant = repository.messages_after(
        owner, started.conversation.conversation_id
    )[-1]
    app, auth, _agent_use = _app(owner, repository)
    client = TestClient(app)

    response = client.post(
        f"/api/v1/messages/{assistant.message_id}/feedback",
        **_write_credentials(auth),
        json={"rating": "helpful"},
    )

    assert response.status_code == 201
    assert response.json() == {
        "feedback_id": response.json()["feedback_id"],
        "conversation_id": str(started.conversation.conversation_id),
        "message_id": str(assistant.message_id),
        "turn_id": str(started.turn.turn_id),
        "mission_id": str(started.mission.mission_id),
        "rating": "helpful",
        "created_at": response.json()["created_at"],
    }
    assert "最终候选人搜索方案" not in response.text
    assert client.post(
        f"/api/v1/messages/{assistant.message_id}/feedback",
        cookies=_credentials(auth)["cookies"],
        json={"rating": "helpful"},
    ).status_code == 403

    foreign_app, foreign_auth, _ = _app(other, repository)
    denied = TestClient(foreign_app).post(
        f"/api/v1/messages/{assistant.message_id}/feedback",
        **_write_credentials(foreign_auth),
        json={"rating": "helpful"},
    )
    assert denied.status_code == 404
