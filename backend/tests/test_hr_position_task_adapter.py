from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from app.agent_brain.conversation_service import ConversationCommandService
from app.hr.context import HrPositionScope
from app.hr.models import CreateManualPosition
from app.hr.position_intelligence_models import PositionTaskRequest
from app.hr.position_intelligence_repository import PositionIntelligenceRepository
from app.hr.position_intelligence_service import PositionIntelligenceService
from app.hr.repository import HrPositionRepository
from app.hr.task_repository import PostgresHrPositionTaskRepository
from app.hr.task_routes import build_hr_position_task_router
from app.hr.task_service import (
    HrPositionTask,
    HrPositionTaskConflict,
    HrPositionTaskNotFound,
    HrPositionTaskService,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_agent_brain_conversation_repository import (
    conversation_database,  # noqa: F401
    repository,  # noqa: F401
)
from test_control_plane_migration import control_database  # noqa: F401

OWNER = UUID("00000000-0000-4000-8000-000000000001")
POSITION = UUID("00000000-0000-4000-8000-000000000002")
CONVERSATION = UUID("00000000-0000-4000-8000-000000000003")
CONTEXT = UUID("00000000-0000-4000-8000-000000000004")
CANDIDATE = UUID("00000000-0000-4000-8000-000000000005")
RELATION = UUID("00000000-0000-4000-8000-000000000006")
MATERIAL = UUID("00000000-0000-4000-8000-000000000007")


class Intelligence:
    def __init__(self) -> None:
        self.records: dict[UUID, PositionTaskRequest] = {}
        self.calls: list[tuple[str, dict[str, object]]] = []

    def create_task_request(self, **values):
        self.calls.append(("persist", values))
        request_id = values["request_id"]
        assert isinstance(request_id, UUID)
        existing = self.records.get(request_id)
        if existing is not None:
            if existing.canonical_payload_sha256 != values["canonical_payload_sha256"]:
                raise HrPositionTaskConflict("payload mismatch")
            return existing
        record = PositionTaskRequest(
            uuid4(),
            values["owner_id"],
            values["position_id"],
            request_id,
            values["canonical_payload_sha256"],
            values["task_kind"],
            values["expected_context_version_id"],
            values["material_attachment_ids"],
            values["candidate_id"],
            values["position_candidate_id"],
            "active",
            datetime.now(UTC),
        )
        self.records[request_id] = record
        return record


class Scope:
    def __init__(self, calls: list[tuple[str, object]]) -> None:
        self.calls = calls
        self.bound_position = POSITION

    def for_conversation(self, owner_id, conversation_id):
        self.calls.append(("binding", (owner_id, conversation_id)))
        return self.bound_position

    def bind_new_conversation_locked(self, *_args, **_kwargs):
        return True


class Commands:
    def __init__(self, calls: list[tuple[str, object]]) -> None:
        self.calls = calls
        self.results: dict[UUID, object] = {}
        self.created_side_effects = 0

    def _result(self, request_id, conversation_id=CONVERSATION):
        if request_id not in self.results:
            self.created_side_effects += 1
            self.results[request_id] = SimpleNamespace(
                conversation=SimpleNamespace(conversation_id=conversation_id),
                turn=SimpleNamespace(turn_id=uuid4(), status="accepted"),
                mission=SimpleNamespace(status="planning"),
                created=True,
            )
        return self.results[request_id]

    def start(self, owner_id, request_id, submission, **kwargs):
        self.calls.append(("start", (owner_id, request_id, submission, kwargs)))
        return self._result(request_id)

    def append_turn(self, owner_id, conversation_id, request_id, submission):
        self.calls.append(
            ("append", (owner_id, conversation_id, request_id, submission))
        )
        return self._result(request_id, conversation_id)


class Projection:
    def __init__(self) -> None:
        self.exists = True
        self.rows: tuple[HrPositionTask, ...] = ()

    def position_exists(self, owner_id, position_id):
        return self.exists and (owner_id, position_id) == (OWNER, POSITION)

    def recoverable_tasks(self, owner_id, position_id):
        assert (owner_id, position_id) == (OWNER, POSITION)
        return self.rows

    def task(self, owner_id, position_id, task_id):
        assert (owner_id, position_id) == (OWNER, POSITION)
        return next((item for item in self.rows if item.task_id == task_id), None)


def dependencies():
    calls: list[tuple[str, object]] = []
    intelligence = Intelligence()
    commands = Commands(calls)
    scope = Scope(calls)
    projection = Projection()
    service = HrPositionTaskService(intelligence, commands, scope, projection)
    return service, intelligence, commands, scope, projection, calls


def test_new_position_task_persists_before_start_and_atomically_scopes_conversation():
    service, intelligence, commands, _, _, calls = dependencies()
    request_id = uuid4()

    task = service.start(
        owner_id=OWNER,
        position_id=POSITION,
        request_id=request_id,
        task_kind="talent_profile",
        context_version_id=CONTEXT,
        material_ids=(MATERIAL,),
        conversation_id=None,
        candidate_id=None,
        position_candidate_id=None,
    )

    assert intelligence.calls[0][0] == "persist"
    assert calls[0][0] == "start"
    _, _, submission, options = calls[0][1]
    assert submission.active_attachment_ids == ()
    assert submission.attachment_ids == ()
    assert intelligence.calls[0][1]["material_attachment_ids"] == (MATERIAL,)
    assert options == {
        "mode": "direct_agent",
        "direct_agent_id": "hr-bot",
        "hr_position_scope": service.position_scope,
        "position_id": POSITION,
    }
    assert task.task_id == intelligence.records[request_id].task_request_id
    assert task.turn_id == commands.results[request_id].turn.turn_id


def test_existing_conversation_is_checked_before_persistence_and_replays_without_duplicate_turn():
    service, intelligence, commands, _, _, calls = dependencies()
    request_id = uuid4()
    values = {
        "owner_id": OWNER,
        "position_id": POSITION,
        "request_id": request_id,
        "task_kind": "jd",
        "context_version_id": CONTEXT,
        "material_ids": (),
        "conversation_id": CONVERSATION,
        "candidate_id": None,
        "position_candidate_id": None,
    }

    first = service.start(**values)
    replay = service.start(**values)

    assert [name for name, _ in calls[:2]] == ["binding", "append"]
    assert len(intelligence.calls) == 2
    assert first.task_id == replay.task_id
    assert first.turn_id == replay.turn_id
    assert commands.created_side_effects == 1


def test_cross_position_conversation_is_concealed_without_persisting_ghost_task():
    service, intelligence, commands, scope, _, _ = dependencies()
    scope.bound_position = uuid4()

    with pytest.raises(HrPositionTaskNotFound):
        service.start(
            owner_id=OWNER,
            position_id=POSITION,
            request_id=uuid4(),
            task_kind="jd",
            context_version_id=CONTEXT,
            material_ids=(),
            conversation_id=CONVERSATION,
            candidate_id=None,
            position_candidate_id=None,
        )

    assert intelligence.records == {}
    assert commands.created_side_effects == 0


@pytest.mark.parametrize(
    "values",
    [
        {
            "task_kind": "candidate_match",
            "candidate_id": CANDIDATE,
            "position_candidate_id": None,
        },
        {
            "task_kind": "jd",
            "candidate_id": CANDIDATE,
            "position_candidate_id": RELATION,
        },
        {
            "task_kind": "candidate_comparison",
            "candidate_id": None,
            "position_candidate_id": None,
        },
    ],
)
def test_service_rejects_invalid_candidate_and_comparison_envelopes(values):
    service, intelligence, *_ = dependencies()
    with pytest.raises(ValueError):
        service.start(
            owner_id=OWNER,
            position_id=POSITION,
            request_id=uuid4(),
            context_version_id=CONTEXT,
            material_ids=(),
            conversation_id=None,
            **values,
        )
    assert intelligence.records == {}


def test_get_recovery_conceals_missing_position_and_returns_durable_states():
    service, _, _, _, projection, _ = dependencies()
    projection.rows = (
        HrPositionTask(
            uuid4(),
            "candidate_match",
            "running",
            None,
            CONVERSATION,
            uuid4(),
            CANDIDATE,
            RELATION,
        ),
        HrPositionTask(
            uuid4(),
            "jd",
            "failed",
            "execution_failed",
            CONVERSATION,
            uuid4(),
            None,
            None,
        ),
    )

    assert [item.status for item in service.recoverable(OWNER, POSITION)] == [
        "running",
        "failed",
    ]
    projection.exists = False
    with pytest.raises(HrPositionTaskNotFound):
        service.recoverable(OWNER, POSITION)


def test_get_one_task_returns_exact_durable_projection_and_conceals_missing():
    service, _, _, _, projection, _ = dependencies()
    task = HrPositionTask(
        uuid4(),
        "candidate_match",
        "completed",
        None,
        CONVERSATION,
        uuid4(),
        CANDIDATE,
        RELATION,
    )
    projection.rows = (task,)

    assert service.get(OWNER, POSITION, task.task_id) == task
    with pytest.raises(HrPositionTaskNotFound):
        service.get(OWNER, POSITION, uuid4())


class RouteService:
    def __init__(self) -> None:
        self.calls = []
        self.error = None
        self.task = HrPositionTask(
            uuid4(),
            "candidate_match",
            "accepted",
            None,
            CONVERSATION,
            uuid4(),
            CANDIDATE,
            RELATION,
        )

    def start(self, **kwargs):
        self.calls.append(("start", kwargs))
        if self.error:
            raise self.error
        return self.task

    def recoverable(self, *args):
        self.calls.append(("recoverable", args))
        if self.error:
            raise self.error
        return (self.task,)

    def get(self, *args):
        self.calls.append(("get", args))
        if self.error:
            raise self.error
        return self.task


def route_client():
    service = RouteService()

    async def require_hr_access(_request, *, writable=False):
        service.calls.append(("access", writable))
        return OWNER

    app = FastAPI()
    app.include_router(build_hr_position_task_router(service, require_hr_access))
    return TestClient(app), service


def test_task_routes_validate_candidate_pair_and_project_recoverable_status():
    client, service = route_client()
    request_id = uuid4()
    response = client.post(
        f"/api/hr/positions/{POSITION}/tasks",
        headers={"Idempotency-Key": str(request_id)},
        json={
            "task_kind": "candidate_match",
            "context_version_id": str(CONTEXT),
            "candidate_id": str(CANDIDATE),
            "position_candidate_id": str(RELATION),
            "material_ids": [str(MATERIAL)],
            "conversation_id": str(CONVERSATION),
        },
    )
    recovered = client.get(f"/api/hr/positions/{POSITION}/tasks?status=active")

    assert response.status_code == 202
    assert response.json()["task_id"] == str(service.task.task_id)
    assert response.json()["candidate_id"] == str(CANDIDATE)
    assert recovered.status_code == 200
    assert recovered.json()["items"][0]["status"] == "accepted"
    assert service.calls[0] == ("access", True)


def test_task_detail_route_returns_authoritative_terminal_candidate_binding():
    client, service = route_client()
    service.task = HrPositionTask(
        service.task.task_id,
        "candidate_match",
        "failed",
        "execution_failed",
        CONVERSATION,
        service.task.turn_id,
        CANDIDATE,
        RELATION,
    )

    response = client.get(
        f"/api/hr/positions/{POSITION}/tasks/{service.task.task_id}"
    )

    assert response.status_code == 200
    assert response.json() == {
        "task_id": str(service.task.task_id),
        "task_kind": "candidate_match",
        "status": "failed",
        "error": "execution_failed",
        "conversation_id": str(CONVERSATION),
        "turn_id": str(service.task.turn_id),
        "candidate_id": str(CANDIDATE),
        "position_candidate_id": str(RELATION),
    }
    assert service.calls == [
        ("access", False),
        ("get", (OWNER, POSITION, service.task.task_id)),
    ]


@pytest.mark.parametrize(
    "body",
    [
        {
            "task_kind": "candidate_match",
            "context_version_id": str(CONTEXT),
            "candidate_id": str(CANDIDATE),
            "position_candidate_id": None,
        },
        {
            "task_kind": "jd",
            "context_version_id": str(CONTEXT),
            "candidate_id": str(CANDIDATE),
            "position_candidate_id": str(RELATION),
        },
        {"task_kind": "candidate_comparison", "context_version_id": str(CONTEXT)},
    ],
)
def test_task_routes_reject_invalid_envelopes_before_service(body):
    client, service = route_client()
    response = client.post(
        f"/api/hr/positions/{POSITION}/tasks",
        headers={"Idempotency-Key": str(uuid4())},
        json=body,
    )
    assert response.status_code == 422
    assert not any(call[0] == "start" for call in service.calls)


def test_task_routes_conceal_cross_scope_and_return_private_no_store():
    client, service = route_client()
    service.error = HrPositionTaskNotFound("internal detail")
    response = client.get(f"/api/hr/positions/{POSITION}/tasks?status=active")
    assert response.status_code == 404
    assert "internal detail" not in response.text
    assert response.headers["cache-control"] == "private, no-store"


class QueryResult:
    def __init__(self, *, one=None, rows=()):
        self.one = one
        self.rows = rows

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.rows


class Connection:
    def __init__(self, results):
        self.results = list(results)
        self.queries = []

    def execute(self, query, params):
        self.queries.append((query, params))
        return self.results.pop(0)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


def test_postgres_projection_joins_turn_mission_and_execution_status_with_exact_scope():
    task_id, turn_id = uuid4(), uuid4()
    connection = Connection(
        [
            QueryResult(one={"exists": 1}),
            QueryResult(
                rows=[
                    {
                        "task_id": task_id,
                        "task_kind": "candidate_match",
                        "status": "failed",
                        "error": "execution_failed",
                        "conversation_id": CONVERSATION,
                        "turn_id": turn_id,
                        "candidate_id": CANDIDATE,
                        "position_candidate_id": RELATION,
                    }
                ]
            ),
        ]
    )
    task_repository = PostgresHrPositionTaskRepository(
        "postgresql://platform_control_app@localhost/control",
        connect=lambda *_args, **_kwargs: connection,
    )

    assert task_repository.position_exists(OWNER, POSITION) is True
    tasks = task_repository.recoverable_tasks(OWNER, POSITION)

    assert tasks == (
        HrPositionTask(
            task_id,
            "candidate_match",
            "failed",
            "execution_failed",
            CONVERSATION,
            turn_id,
            CANDIDATE,
            RELATION,
        ),
    )
    query, params = connection.queries[1]
    assert "platform_hr.position_task_requests" in query
    assert "platform_control.conversation_turns" in query
    assert "platform_control.missions" in query
    assert "platform_control.execution_jobs" in query
    assert "direct_agent_id='hr-bot'" in query
    assert params == (OWNER, POSITION)


def test_postgres_projection_fails_closed_on_duplicate_turns_for_one_task_request():
    task_id = uuid4()
    row = {
        "task_id": task_id,
        "task_kind": "jd",
        "status": "running",
        "error": None,
        "conversation_id": CONVERSATION,
        "turn_id": uuid4(),
        "candidate_id": None,
        "position_candidate_id": None,
    }
    connection = Connection([QueryResult(rows=[row, {**row, "turn_id": uuid4()}])])
    task_repository = PostgresHrPositionTaskRepository(
        "postgresql://platform_control_app@localhost/control",
        connect=lambda *_args, **_kwargs: connection,
    )

    with pytest.raises(Exception, match="position tasks unavailable"):
        task_repository.recoverable_tasks(OWNER, POSITION)


def test_postgres_projection_reads_one_exact_owner_position_task():
    task_id, turn_id = uuid4(), uuid4()
    row = {
        "task_id": task_id,
        "task_kind": "candidate_match",
        "status": "completed",
        "error": None,
        "conversation_id": CONVERSATION,
        "turn_id": turn_id,
        "candidate_id": CANDIDATE,
        "position_candidate_id": RELATION,
    }
    connection = Connection([QueryResult(rows=[row])])
    task_repository = PostgresHrPositionTaskRepository(
        "postgresql://platform_control_app@localhost/control",
        connect=lambda *_args, **_kwargs: connection,
    )

    assert task_repository.task(OWNER, POSITION, task_id) == HrPositionTask(
        task_id,
        "candidate_match",
        "completed",
        None,
        CONVERSATION,
        turn_id,
        CANDIDATE,
        RELATION,
    )
    query, params = connection.queries[0]
    assert "where task_id=%s" in query
    assert params == (OWNER, POSITION, task_id)


@pytest.mark.postgres
def test_postgres_adapter_replays_one_position_bound_turn_and_recovers_status(
    conversation_database,  # noqa: F811
    repository,  # noqa: F811
    request,
):
    environment, owner_id, _ = conversation_database
    positions = HrPositionRepository(environment["urls"]["platform_control_app"])
    position = positions.create_manual(
        CreateManualPosition(owner_id, uuid4(), uuid4(), "高级结构工程师")
    )
    intelligence = PositionIntelligenceService(
        PositionIntelligenceRepository(environment["urls"]["platform_control_app"])
    )
    projection = PostgresHrPositionTaskRepository(
        environment["urls"]["platform_control_app"]
    )
    service = HrPositionTaskService(
        intelligence,
        ConversationCommandService(repository, v2_enabled=True),
        HrPositionScope(positions),
        projection,
    )
    request_id = uuid4()

    def cleanup():
        import psycopg

        with psycopg.connect(environment["admin"]) as connection:
            connection.execute(
                "delete from platform_hr.position_task_records where "
                "owner_internal_user_id=%s and position_id=%s",
                (owner_id, position.position_id),
            )
            connection.execute(
                "delete from platform_hr.position_task_requests where "
                "owner_internal_user_id=%s and position_id=%s",
                (owner_id, position.position_id),
            )
            connection.execute(
                "delete from platform_hr.position_binding_events where "
                "owner_internal_user_id=%s",
                (owner_id,),
            )
            connection.execute(
                "delete from platform_hr.position_conversations where "
                "owner_internal_user_id=%s and position_id=%s",
                (owner_id, position.position_id),
            )
            connection.execute(
                "delete from platform_hr.positions where "
                "owner_internal_user_id=%s and position_id=%s",
                (owner_id, position.position_id),
            )

    request.addfinalizer(cleanup)
    values = {
        "owner_id": owner_id,
        "position_id": position.position_id,
        "request_id": request_id,
        "task_kind": "jd",
        "context_version_id": None,
        "material_ids": (),
        "conversation_id": None,
        "candidate_id": None,
        "position_candidate_id": None,
    }

    first = service.start(**values)
    replay = service.start(**values)
    recovered = service.recoverable(owner_id, position.position_id)

    assert first.task_id == replay.task_id
    assert first.turn_id == replay.turn_id
    assert first.conversation_id == replay.conversation_id
    assert len(recovered) == 1
    assert recovered[0].task_id == first.task_id
    assert recovered[0].status in {"accepted", "running"}
    assert (
        positions.position_for_conversation(owner_id, first.conversation_id)
        == position.position_id
    )
