from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from starlette.requests import ClientDisconnect

from app.agent_brain.models import AgentCapabilityCard
from app.agent_brain.repository import (
    MissionCreateResult,
    MissionEvent,
    MissionRecord,
    MissionRepositoryConflict,
    MissionRepositoryError,
    MissionRepositoryNotFound,
    TERMINAL_MISSION_STATUSES,
)
from app.agent_brain.routes import MissionCursorCodec, build_agent_brain_router
from app.control_plane.authorization import AuthorizationService
from app.control_plane.auth import AuthSecrets
from app.control_plane.middleware import IdentitySecurityMiddleware
from app.control_plane.models import AuthContext, Role


NOW = datetime(2026, 8, 22, 8, 0, tzinfo=UTC)


def _card(agent_id: str = "hr-bot") -> AgentCapabilityCard:
    return AgentCapabilityCard(
        agent_id=agent_id,
        display_name="HR",
        domain_group="hr",
        mission="寻找人才",
        capabilities=("人才定位",),
        exclusions=("不代替最终录用决定",),
        example_tasks=("定义候选人画像",),
        required_inputs=("岗位需求",),
        supports_evidence=False,
        supports_streaming=True,
        supports_cancellation=True,
        supports_idempotency=True,
        max_duration_seconds=300,
        adapter_id="metabot-core-chat",
        capability_version=1,
    )


class FakeAgentUse:
    def __init__(self, cards=(_card(),), *, unavailable: bool = False) -> None:
        self.cards = tuple(cards)
        self.unavailable = unavailable
        self.calls: list[UUID] = []

    def permitted_agents_for_user_id(self, owner: UUID):
        from app.agent_brain.authorization import AgentUseAuthorizationUnavailable

        self.calls.append(owner)
        if self.unavailable:
            raise AgentUseAuthorizationUnavailable()
        return self.cards


class FakeMissionRepository:
    def __init__(self) -> None:
        self.by_id: dict[UUID, MissionRecord] = {}
        self.events: dict[UUID, list[MissionEvent]] = {}
        self.by_request: dict[tuple[UUID, UUID], UUID] = {}
        self.unavailable = False
        self.create_calls: list[tuple] = []

    def _guard(self) -> None:
        if self.unavailable:
            raise MissionRepositoryError()

    def create_mission_for_api(
        self,
        owner: UUID,
        client_request_id: UUID,
        prompt: str,
        *,
        mode: str = "brain",
        direct_agent_id: str | None = None,
    ) -> MissionCreateResult:
        self._guard()
        self.create_calls.append(
            (owner, client_request_id, prompt, mode, direct_agent_id)
        )
        key = (owner, client_request_id)
        if key in self.by_request:
            existing = self.by_id[self.by_request[key]]
            if (
                existing.prompt != prompt
                or existing.mode != mode
                or existing.direct_agent_id != direct_agent_id
            ):
                raise MissionRepositoryConflict()
            return MissionCreateResult(existing, created=False)
        mission = MissionRecord(
            mission_id=uuid4(),
            owner_internal_user_id=owner,
            client_request_id=client_request_id,
            mode=mode,
            direct_agent_id=direct_agent_id,
            status="planning" if mode == "brain" else "delegated",
            cancel_requested=False,
            row_version=0,
            created_at=NOW + timedelta(seconds=len(self.by_id)),
            updated_at=NOW + timedelta(seconds=len(self.by_id)),
            terminal_at=None,
            prompt=prompt,
        )
        self.by_id[mission.mission_id] = mission
        self.by_request[key] = mission.mission_id
        self.events[mission.mission_id] = []
        return MissionCreateResult(mission, created=True)

    def mission_for_owner(self, owner: UUID, mission_id: UUID) -> MissionRecord:
        self._guard()
        mission = self.by_id.get(mission_id)
        if mission is None or mission.owner_internal_user_id != owner:
            raise MissionRepositoryNotFound()
        return mission

    def list_missions_for_owner(
        self,
        owner: UUID,
        *,
        limit: int,
        before: tuple[datetime, UUID] | None = None,
    ) -> tuple[MissionRecord, ...]:
        self._guard()
        if not 1 <= limit <= 101:
            raise ValueError("Mission list limit invalid")
        rows = sorted(
            (
                item
                for item in self.by_id.values()
                if item.owner_internal_user_id == owner
                and (
                    before is None
                    or (item.created_at, item.mission_id) < before
                )
            ),
            key=lambda item: (item.created_at, item.mission_id),
            reverse=True,
        )
        return tuple(rows[:limit])

    def request_cancel(self, owner: UUID, mission_id: UUID) -> MissionRecord:
        mission = self.mission_for_owner(owner, mission_id)
        if mission.status in TERMINAL_MISSION_STATUSES:
            return mission
        changed = replace(
            mission,
            cancel_requested=True,
            row_version=mission.row_version + (not mission.cancel_requested),
            updated_at=NOW + timedelta(minutes=1),
        )
        self.by_id[mission_id] = changed
        return changed

    def events_after(
        self, owner: UUID, mission_id: UUID, *, after: int, limit: int = 500
    ) -> tuple[MissionEvent, ...]:
        self.mission_for_owner(owner, mission_id)
        return tuple(event for event in self.events[mission_id] if event.seq > after)[
            :limit
        ]


class FakeAuth:
    route_prefix = "/"
    cookie_name = "session"
    csrf_cookie_name = "csrf"
    public_base_url = "https://agent.example.test"
    trusted_proxy_networks = ()
    rate_limiter = None

    def __init__(self, context: AuthContext) -> None:
        self.context = context
        self.active = True
        self.authenticate_calls = 0

    def authenticate(self, token: str):
        self.authenticate_calls += 1
        return (
            (self.context, "csrf-value")
            if token == "valid" and self.active
            else None
        )

    def verify_csrf(self, submitted: str, expected: str) -> bool:
        return submitted == expected == "csrf-value"


class NoManagementGrants:
    def permits(self, *_args) -> bool:
        return False


def _app(
    owner: UUID,
    *,
    role: Role = Role.MEMBER,
    missions: FakeMissionRepository | None = None,
    agent_use: FakeAgentUse | None = None,
    heartbeat_seconds: float = 15,
    poll_seconds: float = 1,
):
    missions = missions or FakeMissionRepository()
    agent_use = agent_use or FakeAgentUse()
    context = AuthContext(owner, role, uuid4(), False)
    auth = FakeAuth(context)
    app = FastAPI()
    app.include_router(
        build_agent_brain_router(
            missions,
            agent_use,
            cursor_codec=MissionCursorCodec(AuthSecrets(b"x" * 32, key_version=1)),
            session_revalidator=auth.authenticate,
            session_cookie_name=auth.cookie_name,
            heartbeat_seconds=heartbeat_seconds,
            poll_seconds=poll_seconds,
        )
    )
    app.add_middleware(
        IdentitySecurityMiddleware,
        auth=auth,
        public_assets=frozenset(),
        authorization=AuthorizationService(NoManagementGrants()),
        routes=tuple(app.router.routes),
    )
    return app, auth, missions, agent_use


def _credentials(auth: FakeAuth) -> dict:
    return {
        "cookies": {auth.cookie_name: "valid", auth.csrf_cookie_name: "csrf-value"},
    }


def _write_credentials(auth: FakeAuth) -> dict:
    return {
        **_credentials(auth),
        "headers": {
            "Origin": auth.public_base_url,
            "X-CSRF-Token": "csrf-value",
        },
    }


def _valid_stream_session(owner: UUID, session_id: UUID):
    def revalidate(_token: str):
        return AuthContext(owner, Role.MEMBER, session_id, False), "csrf"

    return revalidate


def test_catalog_is_authenticated_no_store_and_contains_only_current_grants() -> None:
    owner = uuid4()
    app, auth, _missions, agent_use = _app(owner)
    client = TestClient(app)

    assert client.get("/api/v1/catalog/agents").status_code == 401
    response = client.get("/api/v1/catalog/agents", **_credentials(auth))

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert [item["agent_id"] for item in response.json()["agents"]] == ["hr-bot"]
    assert agent_use.calls == [owner]


def test_catalog_authorization_infrastructure_failure_is_explicit_503() -> None:
    owner = uuid4()
    app, auth, _missions, _agent_use = _app(
        owner, agent_use=FakeAgentUse(unavailable=True)
    )
    response = TestClient(app).get(
        "/api/v1/catalog/agents", **_credentials(auth)
    )
    assert response.status_code == 503
    assert response.json() == {"detail": "Agent catalog unavailable"}


@pytest.mark.parametrize("role", list(Role))
def test_brain_creation_is_self_owned_and_uses_server_mission_id(role: Role) -> None:
    owner = uuid4()
    app, auth, missions, _agent_use = _app(owner, role=role)
    request_id = uuid4()
    response = TestClient(app).post(
        "/api/v1/brain/missions",
        headers={
            **_write_credentials(auth)["headers"],
            "Idempotency-Key": str(request_id),
        },
        cookies=_credentials(auth)["cookies"],
        json={"text": "请帮我分析需求"},
    )

    assert response.status_code == 201
    payload = response.json()
    assert UUID(payload["mission_id"]) != request_id
    assert missions.create_calls == [
        (owner, request_id, "请帮我分析需求", "brain", None)
    ]


def test_mutation_rejects_missing_auth_wrong_origin_and_csrf() -> None:
    owner = uuid4()
    app, auth, _missions, _agent_use = _app(owner)
    client = TestClient(app)
    path = "/api/v1/brain/missions"
    headers = {"Idempotency-Key": str(uuid4())}

    assert client.post(path, headers=headers, json={"text": "x"}).status_code == 401
    assert client.post(
        path,
        headers={**headers, "Origin": "https://evil.example", "X-CSRF-Token": "csrf-value"},
        cookies=_credentials(auth)["cookies"],
        json={"text": "x"},
    ).status_code == 403
    assert client.post(
        path,
        headers={**headers, "Origin": auth.public_base_url, "X-CSRF-Token": "wrong"},
        cookies=_credentials(auth)["cookies"],
        json={"text": "x"},
    ).status_code == 403


@pytest.mark.parametrize(
    ("headers", "body", "status"),
    [
        ({}, {"text": "valid"}, 422),
        ({"Idempotency-Key": "not-a-uuid"}, {"text": "valid"}, 422),
        ({"Idempotency-Key": str(uuid4())}, {"text": ""}, 422),
        ({"Idempotency-Key": str(uuid4())}, {"text": "x", "attachments": []}, 422),
        ({"Idempotency-Key": str(uuid4())}, {"text": "x", "agent_id": "hr-bot"}, 422),
        ({"Idempotency-Key": str(uuid4())}, {"text": "😀" * 8193}, 413),
    ],
)
def test_create_validates_exact_text_only_contract(headers, body, status) -> None:
    owner = uuid4()
    app, auth, _missions, _agent_use = _app(owner)
    response = TestClient(app).post(
        "/api/v1/brain/missions",
        headers={**_write_credentials(auth)["headers"], **headers},
        cookies=_credentials(auth)["cookies"],
        json=body,
    )
    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store"
    assert "traceback" not in response.text.lower()


def test_exact_32_kib_utf8_text_is_accepted_and_one_byte_more_is_413() -> None:
    owner = uuid4()
    app, auth, _missions, _agent_use = _app(owner)
    client = TestClient(app)
    credentials = _write_credentials(auth)

    accepted = client.post(
        "/api/v1/brain/missions",
        headers={**credentials["headers"], "Idempotency-Key": str(uuid4())},
        cookies=credentials["cookies"],
        json={"text": "a" * (32 * 1024)},
    )
    rejected = client.post(
        "/api/v1/brain/missions",
        headers={**credentials["headers"], "Idempotency-Key": str(uuid4())},
        cookies=credentials["cookies"],
        json={"text": "a" * (32 * 1024 + 1)},
    )
    assert accepted.status_code == 201
    assert rejected.status_code == 413


def test_idempotent_replay_is_200_and_collision_is_409() -> None:
    owner = uuid4()
    app, auth, _missions, _agent_use = _app(owner)
    client = TestClient(app)
    request_id = uuid4()
    arguments = {
        "headers": {
            **_write_credentials(auth)["headers"],
            "Idempotency-Key": str(request_id),
        },
        "cookies": _credentials(auth)["cookies"],
    }
    first = client.post("/api/v1/brain/missions", json={"text": "same"}, **arguments)
    replay = client.post("/api/v1/brain/missions", json={"text": "same"}, **arguments)
    collision = client.post("/api/v1/brain/missions", json={"text": "changed"}, **arguments)

    assert first.status_code == 201
    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert collision.status_code == 409
    assert collision.json() == {"detail": "idempotency conflict"}


@pytest.mark.parametrize("role", [Role.PLATFORM_OWNER, Role.PLATFORM_ADMIN])
def test_management_role_does_not_bypass_direct_agent_grant(role: Role) -> None:
    owner = uuid4()
    app, auth, missions, _agent_use = _app(
        owner, role=role, agent_use=FakeAgentUse(cards=())
    )
    response = TestClient(app).post(
        "/api/v1/agents/hr-bot/missions",
        headers={
            **_write_credentials(auth)["headers"],
            "Idempotency-Key": str(uuid4()),
        },
        cookies=_credentials(auth)["cookies"],
        json={"text": "找人"},
    )
    assert response.status_code == 403
    assert missions.create_calls == []


def test_direct_agent_creation_uses_route_agent_only_after_fresh_grant() -> None:
    owner = uuid4()
    app, auth, missions, _agent_use = _app(owner)
    response = TestClient(app).post(
        "/api/v1/agents/hr-bot/missions",
        headers={
            **_write_credentials(auth)["headers"],
            "Idempotency-Key": str(uuid4()),
        },
        cookies=_credentials(auth)["cookies"],
        json={"text": "找视觉人才"},
    )
    assert response.status_code == 201
    assert missions.create_calls[0][3:] == ("direct_agent", "hr-bot")


def test_detail_and_cancel_hide_another_owners_mission_as_not_found() -> None:
    owner = uuid4()
    other = uuid4()
    missions = FakeMissionRepository()
    foreign = missions.create_mission_for_api(other, uuid4(), "secret").mission
    app, auth, _missions, _agent_use = _app(owner, missions=missions)
    client = TestClient(app)

    assert client.get(
        f"/api/v1/brain/missions/{foreign.mission_id}", **_credentials(auth)
    ).status_code == 404
    assert client.post(
        f"/api/v1/brain/missions/{foreign.mission_id}/cancel",
        **_write_credentials(auth),
    ).status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/brain/missions/not-a-uuid",
        "/api/v1/brain/missions/not-a-uuid/events",
        "/api/v1/brain/missions/not-a-uuid/cancel",
    ],
)
def test_invalid_mission_uuid_is_422(path: str) -> None:
    owner = uuid4()
    app, auth, _missions, _agent_use = _app(owner)
    client = TestClient(app)
    response = (
        client.post(path, **_write_credentials(auth))
        if path.endswith("cancel")
        else client.get(path, **_credentials(auth))
    )
    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"


def test_invalid_direct_agent_path_is_422_and_no_store() -> None:
    owner = uuid4()
    app, auth, _missions, _agent_use = _app(owner)
    response = TestClient(app).post(
        "/api/v1/agents/!bad/missions",
        headers={
            **_write_credentials(auth)["headers"],
            "Idempotency-Key": str(uuid4()),
        },
        cookies=_credentials(auth)["cookies"],
        json={"text": "x"},
    )
    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"


def test_cancel_is_owned_idempotent_and_no_store() -> None:
    owner = uuid4()
    missions = FakeMissionRepository()
    mission = missions.create_mission_for_api(owner, uuid4(), "stop").mission
    app, auth, _missions, _agent_use = _app(owner, missions=missions)
    client = TestClient(app)

    first = client.post(
        f"/api/v1/brain/missions/{mission.mission_id}/cancel",
        **_write_credentials(auth),
    )
    second = client.post(
        f"/api/v1/brain/missions/{mission.mission_id}/cancel",
        **_write_credentials(auth),
    )
    assert first.status_code == second.status_code == 200
    assert first.json()["cancel_requested"] is True
    assert second.json()["row_version"] == first.json()["row_version"]
    assert first.headers["cache-control"] == "no-store"


def test_owner_bound_opaque_newest_first_pagination_cannot_be_reused() -> None:
    owner = uuid4()
    other = uuid4()
    missions = FakeMissionRepository()
    for text in ("old", "middle", "new"):
        missions.create_mission_for_api(owner, uuid4(), text)
    app, auth, _missions, _agent_use = _app(owner, missions=missions)
    client = TestClient(app)

    first = client.get(
        "/api/v1/brain/missions?limit=2", **_credentials(auth)
    )
    assert [item["prompt"] for item in first.json()["items"]] == ["new", "middle"]
    cursor = first.json()["next_cursor"]
    assert cursor and "middle" not in cursor and str(owner) not in cursor
    second = client.get(
        f"/api/v1/brain/missions?limit=2&before={cursor}", **_credentials(auth)
    )
    assert [item["prompt"] for item in second.json()["items"]] == ["old"]
    assert second.json()["next_cursor"] is None

    other_app, other_auth, _missions, _agent_use = _app(other, missions=missions)
    reused = TestClient(other_app).get(
        f"/api/v1/brain/missions?before={cursor}", **_credentials(other_auth)
    )
    assert reused.status_code == 422


def test_maximum_page_size_remains_bounded_and_returns_a_cursor() -> None:
    owner = uuid4()
    missions = FakeMissionRepository()
    for index in range(101):
        missions.create_mission_for_api(owner, uuid4(), f"mission-{index}")
    app, auth, _missions, _agent_use = _app(owner, missions=missions)

    response = TestClient(app).get(
        "/api/v1/brain/missions?limit=100", **_credentials(auth)
    )

    assert response.status_code == 200
    assert len(response.json()["items"]) == 100
    assert response.json()["next_cursor"]


def test_tampered_or_malformed_cursor_is_422_without_internal_error() -> None:
    owner = uuid4()
    app, auth, _missions, _agent_use = _app(owner)
    for cursor in ("opaque?no", "A" * 12):
        response = TestClient(app).get(
            f"/api/v1/brain/missions?before={cursor}", **_credentials(auth)
        )
        assert response.status_code == 422
        assert response.json() == {"detail": "Mission cursor invalid"}


def test_repository_failures_are_stable_503_without_internal_text() -> None:
    owner = uuid4()
    missions = FakeMissionRepository()
    missions.unavailable = True
    app, auth, _missions, _agent_use = _app(owner, missions=missions)
    response = TestClient(app).get(
        "/api/v1/brain/missions", **_credentials(auth)
    )
    assert response.status_code == 503
    assert response.json() == {"detail": "Mission service unavailable"}


def _event(mission_id: UUID, seq: int, text: str) -> MissionEvent:
    return MissionEvent(
        event_id=uuid4(),
        mission_id=mission_id,
        run_id=None,
        seq=seq,
        event_type="agent.progress",
        payload={"text": text},
        created_at=NOW + timedelta(seconds=seq),
    )


def test_terminal_sse_replays_after_sequence_as_safe_one_line_json_and_closes() -> None:
    owner = uuid4()
    missions = FakeMissionRepository()
    created = missions.create_mission_for_api(owner, uuid4(), "stream").mission
    missions.by_id[created.mission_id] = replace(
        created, status="completed", terminal_at=NOW
    )
    missions.events[created.mission_id] = [
        _event(created.mission_id, 1, "skip"),
        _event(created.mission_id, 2, "line one\nline two"),
    ]
    app, auth, _missions, _agent_use = _app(owner, missions=missions)
    response = TestClient(app).get(
        f"/api/v1/brain/missions/{created.mission_id}/events?after=1",
        **_credentials(auth),
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-store"
    assert "id: 1" not in response.text
    assert "id: 2\nevent: mission\ndata: " in response.text
    data_lines = [
        line.removeprefix("data: ")
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert len(data_lines) == 1
    assert json.loads(data_lines[0])["payload"]["text"] == "line one\nline two"


@pytest.mark.anyio
async def test_active_stream_uses_heartbeat_and_releases_concurrency_slot() -> None:
    from app.agent_brain.routes import MissionStreamLimiter, mission_event_stream

    owner = uuid4()
    missions = FakeMissionRepository()
    mission = missions.create_mission_for_api(owner, uuid4(), "active").mission
    limiter = MissionStreamLimiter(max_per_owner=1, max_per_mission=1)
    disconnected = False

    async def is_disconnected() -> bool:
        nonlocal disconnected
        return disconnected

    stream = mission_event_stream(
        missions,
        owner,
        mission.mission_id,
        after=0,
        is_disconnected=is_disconnected,
        limiter=limiter,
        session_revalidator=_valid_stream_session(owner, owner),
        session_token="valid",
        expected_session_id=owner,
        heartbeat_seconds=0.001,
        revalidate_seconds=0.001,
        poll_seconds=0,
    )
    heartbeat = await anext(stream)
    assert heartbeat.startswith(": heartbeat ") and heartbeat.endswith("\n\n")
    assert limiter.active(owner) == 1
    disconnected = True
    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    assert limiter.active(owner) == 0


@pytest.mark.anyio
async def test_terminal_transition_between_poll_queries_does_not_drop_final_event() -> None:
    from app.agent_brain.routes import MissionStreamLimiter, mission_event_stream

    owner = uuid4()
    missions = FakeMissionRepository()
    active = missions.create_mission_for_api(owner, uuid4(), "race").mission
    terminal_event = _event(active.mission_id, 1, "finished")

    class TerminalRaceRepository:
        def __init__(self) -> None:
            self.event_reads = 0

        def events_after(self, selected_owner, mission_id, *, after, limit):
            assert selected_owner == owner
            assert mission_id == active.mission_id
            self.event_reads += 1
            return () if self.event_reads == 1 else (terminal_event,)

        def mission_for_owner(self, selected_owner, mission_id):
            assert selected_owner == owner
            assert mission_id == active.mission_id
            return replace(active, status="completed", terminal_at=NOW)

    async def connected() -> bool:
        return False

    stream = mission_event_stream(
        TerminalRaceRepository(),
        owner,
        active.mission_id,
        after=0,
        is_disconnected=connected,
        limiter=MissionStreamLimiter(max_per_owner=1, max_per_mission=1),
        session_revalidator=_valid_stream_session(owner, owner),
        session_token="valid",
        expected_session_id=owner,
        poll_seconds=0,
    )
    frames = [frame async for frame in stream]

    assert len(frames) == 1
    assert "id: 1\nevent: mission\n" in frames[0]


@pytest.mark.anyio
async def test_stream_concurrency_is_bounded_without_unbounded_queue() -> None:
    from app.agent_brain.routes import MissionStreamBusy, MissionStreamLimiter

    owner = uuid4()
    mission_id = uuid4()
    limiter = MissionStreamLimiter(
        max_per_owner=1, max_per_mission=1, max_global=2
    )
    async with limiter.slot(owner, mission_id):
        assert limiter.active(owner) == 1
        with pytest.raises(MissionStreamBusy):
            async with limiter.slot(owner, mission_id):
                raise AssertionError("unreachable")
    assert limiter.active(owner) == 0


def test_stream_concurrency_caps_owner_mission_and_global_counts() -> None:
    from app.agent_brain.routes import MissionStreamLimiter

    owner = uuid4()
    other_owner = uuid4()
    first_mission = uuid4()
    second_mission = uuid4()
    other_mission = uuid4()
    limiter = MissionStreamLimiter(
        max_per_owner=3, max_per_mission=2, max_global=4
    )

    assert limiter.acquire(owner, first_mission) is True
    assert limiter.acquire(owner, first_mission) is True
    assert limiter.acquire(owner, first_mission) is False
    assert limiter.acquire(owner, second_mission) is True
    assert limiter.acquire(owner, second_mission) is False
    assert limiter.acquire(other_owner, other_mission) is True
    assert limiter.acquire(uuid4(), uuid4()) is False
    assert limiter.active(owner) == 3
    assert limiter.active_mission(owner, first_mission) == 2
    assert limiter.active_total() == 4

    limiter.release(owner, first_mission)
    limiter.release(owner, first_mission)
    limiter.release(owner, second_mission)
    limiter.release(other_owner, other_mission)
    assert limiter.active_total() == 0


def test_rejected_global_streams_do_not_accumulate_counter_keys() -> None:
    from app.agent_brain.routes import MissionStreamLimiter

    limiter = MissionStreamLimiter(
        max_per_owner=1, max_per_mission=1, max_global=1
    )
    owner = uuid4()
    mission_id = uuid4()
    assert limiter.acquire(owner, mission_id) is True

    for _index in range(100):
        assert limiter.acquire(uuid4(), uuid4()) is False

    assert len(limiter._owner_counts) == 1
    assert len(limiter._mission_counts) == 1
    limiter.release(owner, mission_id)


@pytest.mark.anyio
async def test_slow_stream_consumer_prefetch_is_bounded_to_one_hundred_events() -> None:
    from app.agent_brain.routes import MissionStreamLimiter, mission_event_stream

    owner = uuid4()
    missions = FakeMissionRepository()
    active = missions.create_mission_for_api(owner, uuid4(), "slow").mission

    class BoundedRepository:
        def __init__(self) -> None:
            self.calls: list[int] = []

        def events_after(self, _owner, _mission_id, *, after, limit):
            self.calls.append(limit)
            return tuple(
                _event(active.mission_id, seq, f"progress-{seq}")
                for seq in range(after + 1, after + limit + 1)
            )

        def mission_for_owner(self, _owner, _mission_id):
            return active

    repository = BoundedRepository()

    async def connected() -> bool:
        return False

    limiter = MissionStreamLimiter(max_per_owner=1, max_per_mission=1)
    stream = mission_event_stream(
        repository,
        owner,
        active.mission_id,
        after=0,
        is_disconnected=connected,
        limiter=limiter,
        session_revalidator=_valid_stream_session(owner, owner),
        session_token="valid",
        expected_session_id=owner,
        poll_seconds=0,
    )

    assert "id: 1\n" in await anext(stream)
    assert repository.calls == [100]
    assert limiter.active(owner) == 1
    await stream.aclose()
    assert limiter.active(owner) == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    "invalidated",
    ["logout", "member_inactive", "directory_hard_expiry", "session_replaced"],
)
async def test_stream_revalidates_before_events_and_releases_slot_when_identity_invalidates(
    invalidated: str,
) -> None:
    from app.agent_brain.routes import MissionStreamLimiter, mission_event_stream

    owner = uuid4()
    session_id = uuid4()
    missions = FakeMissionRepository()
    mission = missions.create_mission_for_api(owner, uuid4(), "live auth").mission
    current_session = [
        (AuthContext(owner, Role.MEMBER, session_id, False), "csrf")
    ]

    def revalidate(_token: str):
        return current_session[0]

    async def connected() -> bool:
        return False

    limiter = MissionStreamLimiter(max_per_owner=1, max_per_mission=1)
    stream = mission_event_stream(
        missions,
        owner,
        mission.mission_id,
        after=0,
        is_disconnected=connected,
        limiter=limiter,
        session_revalidator=revalidate,
        session_token="valid",
        expected_session_id=session_id,
        heartbeat_seconds=0.001,
        revalidate_seconds=0.001,
        poll_seconds=0,
    )
    assert (await anext(stream)).startswith(": heartbeat ")
    missions.events[mission.mission_id].append(
        _event(mission.mission_id, 1, "must not leak")
    )
    current_session[0] = (
        (
            AuthContext(owner, Role.MEMBER, uuid4(), False),
            "csrf",
        )
        if invalidated == "session_replaced"
        else None
    )
    await asyncio.sleep(0.002)

    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    assert limiter.active(owner) == 0
    assert limiter.active_total() == 0


@pytest.mark.anyio
async def test_stream_rejects_hard_stale_session_before_new_event() -> None:
    from app.agent_brain.routes import MissionStreamLimiter, mission_event_stream

    owner = uuid4()
    session_id = uuid4()
    missions = FakeMissionRepository()
    mission = missions.create_mission_for_api(owner, uuid4(), "hard stale").mission
    hard_stale = [False]

    def revalidate(_token: str):
        return (
            AuthContext(owner, Role.PLATFORM_OWNER, session_id, hard_stale[0]),
            "csrf",
        )

    async def connected() -> bool:
        return False

    limiter = MissionStreamLimiter(max_per_owner=1, max_per_mission=1)
    stream = mission_event_stream(
        missions,
        owner,
        mission.mission_id,
        after=0,
        is_disconnected=connected,
        limiter=limiter,
        session_revalidator=revalidate,
        session_token="valid",
        expected_session_id=session_id,
        heartbeat_seconds=0.001,
        revalidate_seconds=0.001,
        poll_seconds=0,
    )
    assert (await anext(stream)).startswith(": heartbeat ")
    missions.events[mission.mission_id].append(
        _event(mission.mission_id, 1, "must not leak")
    )
    hard_stale[0] = True
    await asyncio.sleep(0.002)

    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    assert limiter.active_total() == 0


@pytest.mark.anyio
async def test_stream_rechecks_ownership_before_yielding_fetched_events() -> None:
    from app.agent_brain.routes import MissionStreamLimiter, mission_event_stream

    owner = uuid4()
    session_id = uuid4()
    missions = FakeMissionRepository()
    mission = missions.create_mission_for_api(owner, uuid4(), "ownership").mission
    owns_mission = [True]
    original_lookup = missions.mission_for_owner

    def lookup(selected_owner: UUID, mission_id: UUID):
        if not owns_mission[0]:
            raise MissionRepositoryNotFound()
        return original_lookup(selected_owner, mission_id)

    missions.mission_for_owner = lookup

    async def connected() -> bool:
        return False

    limiter = MissionStreamLimiter(max_per_owner=1, max_per_mission=1)
    stream = mission_event_stream(
        missions,
        owner,
        mission.mission_id,
        after=0,
        is_disconnected=connected,
        limiter=limiter,
        session_revalidator=_valid_stream_session(owner, session_id),
        session_token="valid",
        expected_session_id=session_id,
        heartbeat_seconds=0.001,
        revalidate_seconds=0.001,
        poll_seconds=0,
    )
    assert (await anext(stream)).startswith(": heartbeat ")
    missions.events[mission.mission_id].append(
        _event(mission.mission_id, 1, "must not leak")
    )
    owns_mission[0] = False
    await asyncio.sleep(0.002)

    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    assert limiter.active_total() == 0


@pytest.mark.anyio
async def test_slow_consumer_is_revalidated_between_prefetched_events() -> None:
    from app.agent_brain.routes import MissionStreamLimiter, mission_event_stream

    owner = uuid4()
    session_id = uuid4()
    active = MissionRecord(
        mission_id=uuid4(),
        owner_internal_user_id=owner,
        client_request_id=uuid4(),
        mode="brain",
        direct_agent_id=None,
        status="planning",
        cancel_requested=False,
        row_version=0,
        created_at=NOW,
        updated_at=NOW,
        terminal_at=None,
        prompt="slow auth",
    )

    class PrefetchedRepository:
        def events_after(self, _owner, _mission_id, *, after, limit):
            if after:
                return ()
            return (
                _event(active.mission_id, 1, "first"),
                _event(active.mission_id, 2, "must not leak"),
            )

        def mission_for_owner(self, _owner, _mission_id):
            return active

    current_session = [
        (AuthContext(owner, Role.MEMBER, session_id, False), "csrf")
    ]

    async def connected() -> bool:
        return False

    limiter = MissionStreamLimiter(max_per_owner=1, max_per_mission=1)
    stream = mission_event_stream(
        PrefetchedRepository(),
        owner,
        active.mission_id,
        after=0,
        is_disconnected=connected,
        limiter=limiter,
        session_revalidator=lambda _token: current_session[0],
        session_token="valid",
        expected_session_id=session_id,
        revalidate_seconds=0.001,
        poll_seconds=0,
    )
    assert "id: 1\n" in await anext(stream)
    current_session[0] = None
    await asyncio.sleep(0.002)

    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    assert limiter.active_total() == 0


@pytest.mark.anyio
async def test_revoked_slow_terminal_stream_does_not_fetch_or_decrypt_tail() -> None:
    from app.agent_brain.routes import MissionStreamLimiter, mission_event_stream

    owner = uuid4()
    session_id = uuid4()
    terminal = MissionRecord(
        mission_id=uuid4(),
        owner_internal_user_id=owner,
        client_request_id=uuid4(),
        mode="brain",
        direct_agent_id=None,
        status="completed",
        cancel_requested=False,
        row_version=1,
        created_at=NOW,
        updated_at=NOW,
        terminal_at=NOW,
        prompt="terminal tail",
    )

    class TerminalTailRepository:
        def __init__(self) -> None:
            self.event_reads = 0

        def events_after(self, _owner, _mission_id, *, after, limit):
            self.event_reads += 1
            return (
                (_event(terminal.mission_id, 1, "first"),)
                if self.event_reads == 1
                else (_event(terminal.mission_id, 2, "must not decrypt"),)
            )

        def mission_for_owner(self, _owner, _mission_id):
            return terminal

    repository = TerminalTailRepository()
    current_session = [
        (AuthContext(owner, Role.MEMBER, session_id, False), "csrf")
    ]

    async def connected() -> bool:
        return False

    limiter = MissionStreamLimiter(max_per_owner=1, max_per_mission=1)
    stream = mission_event_stream(
        repository,
        owner,
        terminal.mission_id,
        after=0,
        is_disconnected=connected,
        limiter=limiter,
        session_revalidator=lambda _token: current_session[0],
        session_token="valid",
        expected_session_id=session_id,
        revalidate_seconds=0.001,
        poll_seconds=0,
    )
    assert "id: 1\n" in await anext(stream)
    current_session[0] = None
    await asyncio.sleep(0.002)

    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    assert repository.event_reads == 1
    assert limiter.active_total() == 0


@pytest.mark.anyio
async def test_reserved_stream_slot_releases_when_response_start_disconnects() -> None:
    from app.agent_brain.routes import (
        MissionStreamLimiter,
        _ReservedStreamingResponse,
    )

    owner = uuid4()
    limiter = MissionStreamLimiter(max_per_owner=1, max_per_mission=1)
    assert limiter.acquire(owner, owner) is True

    async def body():
        yield "never sent"

    async def receive():
        return {"type": "http.disconnect"}

    async def send(_message):
        raise OSError("client disconnected")

    response = _ReservedStreamingResponse(
        body(), limiter=limiter, owner=owner, mission_id=owner
    )
    scope = {
        "type": "http",
        "asgi": {"spec_version": "2.4"},
        "method": "GET",
        "path": "/events",
        "headers": [],
    }
    with pytest.raises(ClientDisconnect):
        await response(scope, receive, send)
    assert limiter.active(owner) == 0
