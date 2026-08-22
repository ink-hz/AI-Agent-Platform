from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import inspect
from threading import Barrier
from uuid import UUID, uuid4

import psycopg
import pytest

from app.agent_brain.repository import (
    MissionRepository,
    MissionRepositoryConflict,
    MissionRepositoryError,
    MissionRepositoryNotFound,
)
from app.execution_relay.models import RelayEvent
from app.control_plane.crypto import IdentityKeyring
from app.execution_relay.content_crypto import (
    ContentCodec,
    ContentCryptoError,
    SealedContent,
)
from test_control_plane_migration import control_database


def _codec() -> ContentCodec:
    return ContentCodec(
        IdentityKeyring(
            active_version=4,
            purpose="platform-content-encryption",
            _keys={3: b"3" * 32, 4: b"4" * 32},
        )
    )


@pytest.fixture()
def mission_database(control_database):
    environment = control_database["environments"]["production"]
    owner_id = uuid4()
    other_owner_id = uuid4()
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute("delete from platform_control.mission_events")
        connection.execute("delete from platform_control.mission_runs")
        connection.execute("delete from platform_control.mission_tasks")
        connection.execute("delete from platform_control.mission_messages")
        connection.execute("delete from platform_control.missions")
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id,display_name,status) values "
            "(%s,'Mission Owner','active'),(%s,'Other Owner','active')",
            (owner_id, other_owner_id),
        )
    yield environment, owner_id, other_owner_id
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute("delete from platform_control.mission_events")
        connection.execute("delete from platform_control.mission_runs")
        connection.execute("delete from platform_control.mission_tasks")
        connection.execute("delete from platform_control.mission_messages")
        connection.execute("delete from platform_control.missions")


@pytest.fixture()
def repository(mission_database) -> MissionRepository:
    environment, _owner_id, _other_owner_id = mission_database
    return MissionRepository(
        environment["urls"]["platform_control_app"], content_codec=_codec()
    )


def _rows(environment, query: str, params=()):
    with psycopg.connect(environment["admin"]) as connection:
        return connection.execute(query, params).fetchall()


def _advance_to_delegated(
    repository: MissionRepository, owner_id: UUID, mission_id: UUID
) -> None:
    planning = repository.create_run(
        owner_id,
        mission_id,
        phase="planning",
        agent_id="agent-brain-bot",
        input_payload={"prompt": "plan"},
        event_type="brain.responding",
        event_payload={"text": "working"},
    )
    repository.complete_run(
        owner_id,
        mission_id,
        planning.run_id,
        status="completed",
        output_payload={"decision": "delegate"},
        event_type="plan.created",
        event_payload={"text": "plan ready"},
        mission_status="delegated",
    )


def _advance_to_synthesis_ready(
    repository: MissionRepository, owner_id: UUID, mission_id: UUID
) -> None:
    _advance_to_delegated(repository, owner_id, mission_id)
    professional = repository.create_run(
        owner_id,
        mission_id,
        phase="professional",
        agent_id="hr-bot",
        input_payload={"prompt": "professional"},
        objective="candidate profile",
        event_type="task.dispatched",
        event_payload={"agent_id": "hr-bot"},
    )
    repository.complete_run(
        owner_id,
        mission_id,
        professional.run_id,
        status="completed",
        output_payload={"profile": "ready"},
        event_type="agent.result",
        event_payload={"text": "profile ready"},
    )


def _create_queued_professional(
    repository: MissionRepository, owner_id: UUID, mission_id: UUID
):
    _advance_to_delegated(repository, owner_id, mission_id)
    return repository.create_run(
        owner_id,
        mission_id,
        phase="professional",
        agent_id="hr-bot",
        input_payload={"prompt": "professional"},
        objective="candidate profile",
        event_type="task.dispatched",
        event_payload={"agent_id": "hr-bot"},
    )


@pytest.mark.postgres
def test_relay_events_bridge_once_and_move_professional_run_to_running(
    mission_database, repository
) -> None:
    environment, owner_id, _ = mission_database
    mission = repository.create_mission(owner_id, uuid4(), "bridge")
    professional = _create_queued_professional(
        repository, owner_id, mission.mission_id
    )
    now = datetime.now(timezone.utc)
    relay_events = (
        RelayEvent(
            run_id=professional.run_id,
            seq=1,
            event_type="agent.state",
            created_at=now,
            payload={
                "state": "running",
                "progress": 0.25,
                "current": 1,
                "total": 4,
                "text": "private state detail",
                "rawDebug": "must not persist",
            },
        ),
        RelayEvent(
            run_id=professional.run_id,
            seq=2,
            event_type="agent.log",
            created_at=now,
            payload={"text": "正在搜索候选人", "token": "must not persist"},
        ),
    )

    assert repository.apply_relay_events(
        owner_id, mission.mission_id, professional.run_id, relay_events
    ) == 2
    assert repository.apply_relay_events(
        owner_id, mission.mission_id, professional.run_id, relay_events
    ) == 0

    run = repository.runs_for_owner(owner_id, mission.mission_id)[-1]
    assert run.status == "running"
    assert run.relay_event_cursor == 2
    events = repository.events_after(owner_id, mission.mission_id)
    assert [event.event_type for event in events[-2:]] == [
        "agent.accepted",
        "agent.progress",
    ]
    assert events[-1].payload == {
        "agent_id": "hr-bot",
        "state": "running",
        "progress": 0.25,
        "current": 1,
        "total": 4,
    }
    assert b"must not persist" not in b"".join(
        bytes(row[0])
        for row in _rows(
            environment,
            "select payload_ciphertext from platform_control.mission_events "
            "where mission_id=%s",
            (mission.mission_id,),
        )
    )


@pytest.mark.postgres
def test_professional_result_does_not_fabricate_review_checkpoint(
    mission_database, repository
) -> None:
    environment, owner_id, _ = mission_database
    mission = repository.create_mission(owner_id, uuid4(), "review")
    _advance_to_synthesis_ready(repository, owner_id, mission.mission_id)
    events = repository.events_after(owner_id, mission.mission_id)
    assert [event.event_type for event in events].count("task.reviewed") == 0


@pytest.mark.postgres
def test_create_mission_generates_server_ids_and_stores_only_bound_ciphertext(
    mission_database, repository
) -> None:
    environment, owner_id, _ = mission_database
    request_id = uuid4()
    prompt = "请分析这个职位的候选人画像"

    mission = repository.create_mission(owner_id, request_id, prompt)

    assert isinstance(mission.mission_id, UUID)
    assert mission.owner_internal_user_id == owner_id
    assert mission.client_request_id == request_id
    assert mission.prompt == prompt
    assert mission.mode == "brain"
    assert mission.status == "planning"
    mission_rows = _rows(
        environment,
        "select mission_id,owner_internal_user_id,client_request_id,mode,status "
        "from platform_control.missions",
    )
    assert mission_rows == [
        (mission.mission_id, owner_id, request_id, "brain", "planning")
    ]
    message = _rows(
        environment,
        "select message_id,mission_id,content_ciphertext,encryption_key_version "
        "from platform_control.mission_messages",
    )[0]
    assert prompt.encode() not in bytes(message[2])
    assert message[3] == 4
    value = repository.content_codec.unseal_json(
        f"mission:{mission.mission_id}:message:{message[0]}:content",
        SealedContent(bytes(message[2]), message[3]),
    )
    assert value == {"text": prompt}
    with pytest.raises(ContentCryptoError):
        repository.content_codec.unseal_json(
            f"mission:{uuid4()}:message:{message[0]}:content",
            SealedContent(bytes(message[2]), message[3]),
        )


@pytest.mark.postgres
def test_client_request_id_is_idempotent_per_owner_and_collision_safe(
    mission_database, repository
) -> None:
    _environment, owner_id, other_owner_id = mission_database
    request_id = uuid4()

    first = repository.create_mission(owner_id, request_id, "same prompt")
    replay = repository.create_mission(owner_id, request_id, "same prompt")
    other = repository.create_mission(other_owner_id, request_id, "other owner")

    assert replay == first
    assert other.mission_id != first.mission_id
    with pytest.raises(MissionRepositoryConflict):
        repository.create_mission(owner_id, request_id, "changed prompt")


@pytest.mark.postgres
def test_concurrent_idempotent_creates_return_one_server_mission(
    mission_database, repository
) -> None:
    environment, owner_id, _ = mission_database
    request_id = uuid4()

    with ThreadPoolExecutor(max_workers=8) as pool:
        missions = tuple(
            pool.map(
                lambda _index: repository.create_mission(
                    owner_id, request_id, "one logical request"
                ),
                range(16),
            )
        )

    assert len({mission.mission_id for mission in missions}) == 1
    assert _rows(
        environment,
        "select count(*) from platform_control.missions "
        "where owner_internal_user_id=%s and client_request_id=%s",
        (owner_id, request_id),
    ) == [(1,)]


@pytest.mark.postgres
def test_owner_reads_filter_before_decryption_and_cross_owner_is_not_found(
    mission_database, repository, monkeypatch
) -> None:
    _environment, owner_id, other_owner_id = mission_database
    mission = repository.create_mission(owner_id, uuid4(), "owner secret")
    repository.create_run(
        owner_id,
        mission.mission_id,
        phase="planning",
        agent_id="agent-brain-bot",
        input_payload={"prompt": "plan"},
        event_type="brain.responding",
        event_payload={"text": "safe event"},
    )
    calls = 0

    def forbidden_decrypt(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("cross-owner ciphertext was decrypted")

    monkeypatch.setattr(repository.content_codec, "unseal_json", forbidden_decrypt)

    with pytest.raises(MissionRepositoryNotFound):
        repository.mission_for_owner(other_owner_id, mission.mission_id)
    with pytest.raises(MissionRepositoryNotFound):
        repository.events_after(other_owner_id, mission.mission_id)
    assert calls == 0


@pytest.mark.postgres
def test_owner_list_detail_and_event_replay_decrypt_only_owned_rows(
    mission_database, repository
) -> None:
    _environment, owner_id, other_owner_id = mission_database
    older = repository.create_mission(owner_id, uuid4(), "older")
    newest = repository.create_mission(owner_id, uuid4(), "newest")
    repository.create_mission(other_owner_id, uuid4(), "not visible")
    run = repository.create_run(
        owner_id,
        newest.mission_id,
        phase="planning",
        agent_id="agent-brain-bot",
        input_payload={"prompt": "plan"},
        event_type="brain.responding",
        event_payload={"text": "one"},
    )
    repository.complete_run(
        owner_id,
        newest.mission_id,
        run.run_id,
        status="completed",
        output_payload={"decision": "direct"},
        event_type="mission.completed",
        event_payload={"text": "two"},
        mission_status="completed",
    )
    first, second = repository.events_after(owner_id, newest.mission_id)

    listed = repository.list_missions_for_owner(owner_id, limit=20)

    assert {item.mission_id for item in listed} == {
        older.mission_id,
        newest.mission_id,
    }
    assert all(item.owner_internal_user_id == owner_id for item in listed)
    assert repository.mission_for_owner(owner_id, newest.mission_id).prompt == "newest"
    assert repository.events_after(owner_id, newest.mission_id, after=1) == (
        second,
    )
    assert first.seq == 1 and second.seq == 2


@pytest.mark.postgres
def test_concurrent_event_writers_allocate_monotonic_unique_sequences(
    mission_database, repository
) -> None:
    _environment, owner_id, _ = mission_database
    mission = repository.create_mission(owner_id, uuid4(), "concurrent")
    professional = _create_queued_professional(
        repository, owner_id, mission.mission_id
    )

    with ThreadPoolExecutor(max_workers=8) as pool:
        events = tuple(
            pool.map(
                lambda index: repository.append_event(
                    owner_id,
                        mission.mission_id,
                        "agent.progress",
                        {"index": index},
                        run_id=professional.run_id,
                ),
                range(24),
            )
        )

    assert sorted(event.seq for event in events) == list(range(4, 28))
    replay = repository.events_after(owner_id, mission.mission_id)
    assert tuple(event.seq for event in replay) == tuple(range(1, 28))


@pytest.mark.postgres
def test_run_task_input_output_and_events_use_mission_and_row_bound_subjects(
    mission_database, repository
) -> None:
    environment, owner_id, _ = mission_database
    mission = repository.create_mission(owner_id, uuid4(), "delegate this")
    _advance_to_delegated(repository, owner_id, mission.mission_id)
    run = repository.create_run(
        owner_id,
        mission.mission_id,
        phase="professional",
        agent_id="hr-bot",
        input_payload={"prompt": "minimal input"},
        objective="define a candidate profile",
        event_type="task.dispatched",
        event_payload={"agent_id": "hr-bot"},
    )
    completed = repository.complete_run(
        owner_id,
        mission.mission_id,
        run.run_id,
        status="completed",
        output_payload={"answer": "professional result"},
        event_type="agent.result",
        event_payload={"text": "professional result"},
    )

    assert completed.status == "completed"
    task = _rows(
        environment,
        "select task_id,objective_ciphertext,encryption_key_version,status "
        "from platform_control.mission_tasks where mission_id=%s",
        (mission.mission_id,),
    )[0]
    stored_run = _rows(
        environment,
        "select run_id,input_ciphertext,encryption_key_version,output_ciphertext,"
        "output_encryption_key_version,status from platform_control.mission_runs "
        "where mission_id=%s and phase='professional'",
        (mission.mission_id,),
    )[0]
    assert b"minimal input" not in bytes(stored_run[1])
    assert b"professional result" not in bytes(stored_run[3])
    assert repository.content_codec.unseal_json(
        f"mission:{mission.mission_id}:task:{task[0]}:objective",
        SealedContent(bytes(task[1]), task[2]),
    ) == {"text": "define a candidate profile"}
    assert repository.content_codec.unseal_json(
        f"mission:{mission.mission_id}:run:{run.run_id}:input",
        SealedContent(bytes(stored_run[1]), stored_run[2]),
    ) == {"prompt": "minimal input"}
    assert repository.content_codec.unseal_json(
        f"mission:{mission.mission_id}:run:{run.run_id}:output",
        SealedContent(bytes(stored_run[3]), stored_run[4]),
    ) == {"answer": "professional result"}
    with pytest.raises(ContentCryptoError):
        repository.content_codec.unseal_json(
            f"mission:{mission.mission_id}:run:{uuid4()}:input",
            SealedContent(bytes(stored_run[1]), stored_run[2]),
        )
    events = repository.events_after(owner_id, mission.mission_id)
    assert [event.event_type for event in events] == [
        "brain.responding",
        "plan.created",
        "task.dispatched",
        "agent.result",
    ]
    event_rows = _rows(
        environment,
        "select event_id,payload_ciphertext,encryption_key_version "
        "from platform_control.mission_events where mission_id=%s order by seq",
        (mission.mission_id,),
    )
    with pytest.raises(ContentCryptoError):
        repository.content_codec.unseal_json(
            f"mission:{mission.mission_id}:event:{uuid4()}:payload",
            SealedContent(bytes(event_rows[0][1]), event_rows[0][2]),
        )
    assert all(
        b"professional result" not in bytes(row[0])
        for row in _rows(
            environment,
            "select payload_ciphertext from platform_control.mission_events "
            "where mission_id=%s",
            (mission.mission_id,),
        )
    )


@pytest.mark.postgres
def test_create_run_and_event_are_atomic_on_event_failure(
    mission_database, repository
) -> None:
    environment, owner_id, _ = mission_database
    mission = repository.create_mission(owner_id, uuid4(), "atomic create")

    with pytest.raises(
        MissionRepositoryError, match="^mission repository unavailable$"
    ):
        repository.create_run(
            owner_id,
            mission.mission_id,
            phase="planning",
            agent_id="agent-brain-bot",
            input_payload={"prompt": "plan"},
            event_type="not.allowed",
            event_payload={"text": "must roll back"},
        )

    assert _rows(
        environment,
        "select count(*) from platform_control.mission_runs where mission_id=%s",
        (mission.mission_id,),
    ) == [(0,)]
    assert repository.events_after(owner_id, mission.mission_id) == ()


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("phase", "agent_id", "objective", "event_type"),
    (
        ("planning", "agent-brain-bot", None, "brain.responding"),
        ("professional", "hr-bot", "candidate profile", "task.dispatched"),
    ),
)
def test_create_run_retry_recovers_one_server_run_and_event(
    mission_database,
    repository,
    phase: str,
    agent_id: str,
    objective: str | None,
    event_type: str,
) -> None:
    environment, owner_id, _ = mission_database
    mission = repository.create_mission(owner_id, uuid4(), f"retry {phase}")
    if phase == "professional":
        _advance_to_delegated(repository, owner_id, mission.mission_id)
    arguments = {
        "phase": phase,
        "agent_id": agent_id,
        "input_payload": {"prompt": f"run {phase}"},
        "objective": objective,
        "event_type": event_type,
        "event_payload": (
            {"agent_id": agent_id, "text": f"start {phase}"}
            if phase == "professional"
            else {"text": f"start {phase}"}
        ),
    }

    first = repository.create_run(owner_id, mission.mission_id, **arguments)
    recovered = repository.create_run(owner_id, mission.mission_id, **arguments)

    assert recovered == first
    assert _rows(
        environment,
        "select count(*) from platform_control.mission_runs "
        "where mission_id=%s and phase=%s",
        (mission.mission_id, phase),
    ) == [(1,)]
    assert [
        event.event_type
        for event in repository.events_after(owner_id, mission.mission_id)
    ][-1] == event_type
    with pytest.raises(MissionRepositoryConflict):
        repository.create_run(
            owner_id,
            mission.mission_id,
            phase=phase,
            agent_id=agent_id,
            input_payload={"prompt": "changed after retry"},
            objective=objective,
            event_type=event_type,
            event_payload=arguments["event_payload"],
        )


@pytest.mark.postgres
def test_complete_run_and_terminal_event_are_atomic_on_event_failure(
    mission_database, repository
) -> None:
    environment, owner_id, _ = mission_database
    mission = repository.create_mission(owner_id, uuid4(), "atomic complete")
    run = repository.create_run(
        owner_id,
        mission.mission_id,
        phase="planning",
        agent_id="agent-brain-bot",
        input_payload={"prompt": "plan"},
        event_type="brain.responding",
        event_payload={"text": "planning"},
    )

    with pytest.raises(MissionRepositoryError):
        repository.complete_run(
            owner_id,
            mission.mission_id,
            run.run_id,
            status="completed",
            output_payload={"answer": "done"},
            event_type="not.allowed",
            event_payload={"text": "must roll back"},
            mission_status="completed",
        )

    stored = _rows(
        environment,
        "select status,output_ciphertext,terminal_at from "
        "platform_control.mission_runs where run_id=%s",
        (run.run_id,),
    )[0]
    assert stored == ("queued", None, None)
    assert (
        repository.mission_for_owner(owner_id, mission.mission_id).status
        == "planning"
    )
    assert [
        event.event_type
        for event in repository.events_after(owner_id, mission.mission_id)
    ] == ["brain.responding"]


@pytest.mark.postgres
def test_run_transitions_support_owner_scoped_status_and_version_cas(
    mission_database, repository
) -> None:
    environment, owner_id, _ = mission_database
    mission = repository.create_mission(owner_id, uuid4(), "compare and set")

    with pytest.raises(MissionRepositoryConflict):
        repository.create_run(
            owner_id,
            mission.mission_id,
            phase="planning",
            agent_id="agent-brain-bot",
            input_payload={"prompt": "plan"},
            event_type="brain.responding",
            event_payload={"text": "working"},
            expected_mission_status="planning",
            expected_row_version=1,
        )
    assert _rows(
        environment,
        "select count(*) from platform_control.mission_runs where mission_id=%s",
        (mission.mission_id,),
    ) == [(0,)]

    run = repository.create_run(
        owner_id,
        mission.mission_id,
        phase="planning",
        agent_id="agent-brain-bot",
        input_payload={"prompt": "plan"},
        event_type="brain.responding",
        event_payload={"text": "working"},
        expected_mission_status="planning",
        expected_row_version=0,
    )
    with pytest.raises(MissionRepositoryConflict):
        repository.complete_run(
            owner_id,
            mission.mission_id,
            run.run_id,
            status="completed",
            output_payload={"answer": "done"},
            event_type="plan.created",
            event_payload={"text": "done"},
            expected_mission_status="planning",
            expected_row_version=0,
        )
    assert _rows(
        environment,
        "select status,output_ciphertext from platform_control.mission_runs "
        "where run_id=%s",
        (run.run_id,),
    ) == [("queued", None)]


@pytest.mark.postgres
def test_terminal_missions_and_runs_are_immutable(
    mission_database, repository
) -> None:
    _environment, owner_id, _ = mission_database
    mission = repository.create_mission(owner_id, uuid4(), "finish")
    run = repository.create_run(
        owner_id,
        mission.mission_id,
        phase="planning",
        agent_id="agent-brain-bot",
        input_payload={"prompt": "finish"},
        event_type="brain.responding",
        event_payload={"text": "working"},
    )
    repository.complete_run(
        owner_id,
        mission.mission_id,
        run.run_id,
        status="completed",
        output_payload={"answer": "final"},
        event_type="mission.completed",
        event_payload={"text": "final"},
        mission_status="completed",
    )

    with pytest.raises(MissionRepositoryConflict):
        repository.append_event(
            owner_id, mission.mission_id, "agent.progress", {"text": "late"}
        )
    with pytest.raises(MissionRepositoryConflict):
        repository.create_run(
            owner_id,
            mission.mission_id,
            phase="synthesis",
            agent_id="agent-brain-bot",
            input_payload={"prompt": "late"},
            event_type="synthesis.started",
            event_payload={"text": "late"},
        )
    with pytest.raises(MissionRepositoryConflict):
        repository.complete_run(
            owner_id,
            mission.mission_id,
            run.run_id,
            status="failed",
            output_payload={"error": "late"},
            event_type="mission.failed",
            event_payload={"text": "late"},
            mission_status="failed",
        )


@pytest.mark.postgres
def test_terminal_run_rejects_late_events_while_mission_remains_active(
    mission_database, repository
) -> None:
    _environment, owner_id, _ = mission_database
    mission = repository.create_mission(owner_id, uuid4(), "keep Mission active")
    run = repository.create_run(
        owner_id,
        mission.mission_id,
        phase="planning",
        agent_id="agent-brain-bot",
        input_payload={"prompt": "plan"},
        event_type="brain.responding",
        event_payload={"text": "working"},
    )
    repository.complete_run(
        owner_id,
        mission.mission_id,
        run.run_id,
        status="completed",
        output_payload={"decision": "delegate"},
        event_type="plan.created",
        event_payload={"text": "plan ready"},
        mission_status="delegated",
    )

    with pytest.raises(MissionRepositoryConflict):
        repository.append_event(
            owner_id,
            mission.mission_id,
            "agent.progress",
            {"text": "late"},
            run_id=run.run_id,
        )

    assert [
        event.event_type
        for event in repository.events_after(owner_id, mission.mission_id)
    ] == ["brain.responding", "plan.created"]


@pytest.mark.postgres
def test_corrupt_ciphertext_and_database_errors_are_stable_and_non_secret(
    mission_database, repository
) -> None:
    environment, owner_id, _ = mission_database
    mission = repository.create_mission(owner_id, uuid4(), "top secret")
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_control.mission_messages set content_ciphertext=%s "
            "where mission_id=%s",
            (b"x" * 29, mission.mission_id),
        )

    tombstone = repository.mission_for_owner(owner_id, mission.mission_id)

    assert tombstone.content_available is False
    assert tombstone.prompt == "[任务内容不可用]"
    assert "top secret" not in repr(tombstone)


@pytest.mark.postgres
def test_nested_payload_serialization_is_a_stable_non_secret_error(
    mission_database, repository
) -> None:
    _environment, owner_id, _ = mission_database
    mission = repository.create_mission(owner_id, uuid4(), "nested payload")
    nested: dict[str, object] = {}
    current = nested
    for _index in range(2_000):
        child: dict[str, object] = {}
        current["child"] = child
        current = child

    with pytest.raises(MissionRepositoryError) as raised:
        repository.create_run(
            owner_id,
            mission.mission_id,
            phase="planning",
            agent_id="agent-brain-bot",
            input_payload=nested,
            event_type="brain.responding",
            event_payload={"text": "working"},
        )

    assert type(raised.value) is MissionRepositoryError
    assert str(raised.value) == "mission repository unavailable"


def test_repository_source_owner_scopes_reads_and_generates_ids_internally() -> None:
    source = inspect.getsource(MissionRepository)

    assert "uuid4()" in source
    assert "owner_internal_user_id=%s" in source
    assert " for update" in source.lower()
    assert "ContentCodec" in source


@pytest.mark.postgres
def test_safe_event_payload_accepts_bounded_user_visible_json(
    mission_database, repository
) -> None:
    _environment, owner_id, _ = mission_database
    mission = repository.create_mission(owner_id, uuid4(), "safe payload")
    professional = _create_queued_professional(
        repository, owner_id, mission.mission_id
    )
    payload = {
        "text": "正在整理候选人画像",
        "current": 1,
        "total": 2,
        "stage": "searching",
    }

    event = repository.append_event(
        owner_id,
        mission.mission_id,
        "agent.progress",
        payload,
        run_id=professional.run_id,
    )

    assert event.payload == payload
    assert repository.events_after(owner_id, mission.mission_id)[-1] == event


@pytest.mark.postgres
def test_event_payload_schemas_reject_freeform_nested_and_wrong_typed_fields(
    mission_database, repository
) -> None:
    environment, owner_id, _ = mission_database
    mission = repository.create_mission(owner_id, uuid4(), "strict UI schema")

    with pytest.raises(MissionRepositoryError):
        repository.create_run(
            owner_id,
            mission.mission_id,
            phase="planning",
            agent_id="agent-brain-bot",
            input_payload={"prompt": "plan"},
            event_type="brain.responding",
            event_payload={"text": "working", "details": {"label": "hidden"}},
        )
    assert _rows(
        environment,
        "select count(*) from platform_control.mission_runs where mission_id=%s",
        (mission.mission_id,),
    ) == [(0,)]

    professional = _create_queued_professional(
        repository, owner_id, mission.mission_id
    )
    with pytest.raises(MissionRepositoryError):
        repository.append_event(
            owner_id,
            mission.mission_id,
            "agent.progress",
            {"current": "one"},
            run_id=professional.run_id,
        )
    with pytest.raises(MissionRepositoryError):
        repository.complete_run(
            owner_id,
            mission.mission_id,
            professional.run_id,
            status="completed",
            output_payload={"profile": "ready"},
            event_type="agent.result",
            event_payload={"result": {"raw": "not UI-safe"}},
        )

    assert _rows(
        environment,
        "select status,output_ciphertext from platform_control.mission_runs "
        "where run_id=%s",
        (professional.run_id,),
    ) == [("queued", None)]


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("event_type", "payload"),
    (
        ("mission.started", {"text": "started"}),
        ("brain.responding", {"text": "working"}),
        ("plan.created", {"text": "ready"}),
        ("task.dispatched", {"agent_id": "hr-bot"}),
        ("agent.accepted", {"agent_id": "hr-bot"}),
        ("agent.result", {"text": "ready"}),
        ("task.reviewed", {"text": "accepted"}),
        ("synthesis.started", {"text": "working"}),
        ("mission.completed", {"text": "done"}),
        ("mission.failed", {"text": "failed"}),
        ("mission.cancelled", {"text": "cancelled"}),
        ("mission.interrupted", {"text": "interrupted"}),
    ),
)
def test_append_event_rejects_non_progress_transition_events(
    mission_database,
    repository,
    event_type: str,
    payload: dict[str, object],
) -> None:
    _environment, owner_id, _ = mission_database
    mission = repository.create_mission(owner_id, uuid4(), "append boundary")
    professional = _create_queued_professional(
        repository, owner_id, mission.mission_id
    )
    before = repository.events_after(owner_id, mission.mission_id)

    with pytest.raises(MissionRepositoryConflict):
        repository.append_event(
            owner_id,
            mission.mission_id,
            event_type,
            payload,
            run_id=professional.run_id,
        )

    assert repository.events_after(owner_id, mission.mission_id) == before


@pytest.mark.postgres
def test_append_progress_requires_bound_active_professional_or_direct_run(
    mission_database, repository
) -> None:
    _environment, owner_id, _ = mission_database
    mission = repository.create_mission(owner_id, uuid4(), "progress boundary")
    planning = repository.create_run(
        owner_id,
        mission.mission_id,
        phase="planning",
        agent_id="agent-brain-bot",
        input_payload={"prompt": "plan"},
        event_type="brain.responding",
        event_payload={"text": "working"},
    )

    with pytest.raises(MissionRepositoryConflict):
        repository.append_event(
            owner_id,
            mission.mission_id,
            "agent.progress",
            {"text": "not bound"},
        )
    with pytest.raises(MissionRepositoryConflict):
        repository.append_event(
            owner_id,
            mission.mission_id,
            "agent.progress",
            {"text": "wrong phase"},
            run_id=planning.run_id,
        )

    repository.complete_run(
        owner_id,
        mission.mission_id,
        planning.run_id,
        status="completed",
        output_payload={"decision": "delegate"},
        event_type="plan.created",
        event_payload={"text": "ready"},
        mission_status="delegated",
    )
    professional = repository.create_run(
        owner_id,
        mission.mission_id,
        phase="professional",
        agent_id="hr-bot",
        input_payload={"prompt": "work"},
        objective="candidate profile",
        event_type="task.dispatched",
        event_payload={"agent_id": "hr-bot"},
    )

    event = repository.append_event(
        owner_id,
        mission.mission_id,
        "agent.progress",
        {"text": "搜索中", "current": 1, "total": 3},
        run_id=professional.run_id,
    )

    assert event.payload == {"text": "搜索中", "current": 1, "total": 3}


@pytest.mark.postgres
@pytest.mark.parametrize(
    "event_payload",
    (
        {"agent_id": "fae-bot"},
        {"agent_id": "hr-bot", "objective": "caller-controlled objective"},
    ),
)
def test_create_run_binds_dispatch_event_to_agent_and_derived_objective(
    mission_database,
    repository,
    event_payload: dict[str, object],
) -> None:
    environment, owner_id, _ = mission_database
    mission = repository.create_mission(owner_id, uuid4(), "bound dispatch")
    _advance_to_delegated(repository, owner_id, mission.mission_id)

    with pytest.raises(MissionRepositoryError):
        repository.create_run(
            owner_id,
            mission.mission_id,
            phase="professional",
            agent_id="hr-bot",
            input_payload={"prompt": "work"},
            objective="stored candidate profile",
            event_type="task.dispatched",
            event_payload=event_payload,
        )

    assert _rows(
        environment,
        "select count(*) from platform_control.mission_runs "
        "where mission_id=%s and phase='professional'",
        (mission.mission_id,),
    ) == [(0,)]


@pytest.mark.postgres
def test_progress_and_result_agent_id_must_match_locked_run(
    mission_database, repository
) -> None:
    environment, owner_id, _ = mission_database
    mission = repository.create_mission(owner_id, uuid4(), "bound provenance")
    professional = _create_queued_professional(
        repository, owner_id, mission.mission_id
    )
    before = repository.events_after(owner_id, mission.mission_id)

    with pytest.raises(MissionRepositoryConflict):
        repository.append_event(
            owner_id,
            mission.mission_id,
            "agent.progress",
            {"agent_id": "fae-bot", "text": "wrong agent"},
            run_id=professional.run_id,
        )
    with pytest.raises(MissionRepositoryConflict):
        repository.complete_run(
            owner_id,
            mission.mission_id,
            professional.run_id,
            status="completed",
            output_payload={"profile": "ready"},
            event_type="agent.result",
            event_payload={"agent_id": "fae-bot", "text": "wrong agent"},
        )

    assert repository.events_after(owner_id, mission.mission_id) == before
    assert _rows(
        environment,
        "select status,output_ciphertext from platform_control.mission_runs "
        "where run_id=%s",
        (professional.run_id,),
    ) == [("queued", None)]


@pytest.mark.postgres
@pytest.mark.parametrize(
    "payload",
    (
        {"current": 4, "total": 3},
        {"items": []},
    ),
)
def test_event_schema_rejects_contradictory_or_empty_business_content(
    mission_database, repository, payload: dict[str, object]
) -> None:
    _environment, owner_id, _ = mission_database
    mission = repository.create_mission(owner_id, uuid4(), "business content")
    professional = _create_queued_professional(
        repository, owner_id, mission.mission_id
    )

    if "current" in payload:
        with pytest.raises(MissionRepositoryError):
            repository.append_event(
                owner_id,
                mission.mission_id,
                "agent.progress",
                payload,
                run_id=professional.run_id,
            )
    else:
        with pytest.raises(MissionRepositoryError):
            repository.complete_run(
                owner_id,
                mission.mission_id,
                professional.run_id,
                status="completed",
                output_payload={"profile": "ready"},
                event_type="agent.result",
                event_payload=payload,
            )


class _NotJson:
    pass


@pytest.mark.postgres
@pytest.mark.parametrize(
    "payload",
    (
        {1: "non-string key"},
        {"items": ("tuple",)},
        {"items": {"set"}},
        {"blob": b"bytes"},
        {"custom": _NotJson()},
        {"score": float("nan")},
        {"score": float("inf")},
        {"text": "x" * (32 * 1024 + 1)},
        {"items": list(range(2_049))},
        {"first": "x" * (32 * 1024), "second": "y" * (32 * 1024)},
    ),
)
def test_payload_boundary_rejects_non_json_nonfinite_and_oversized_values(
    mission_database, repository, payload: object
) -> None:
    _environment, owner_id, _ = mission_database
    mission = repository.create_mission(owner_id, uuid4(), "invalid payload")

    with pytest.raises(
        MissionRepositoryError, match="^mission repository unavailable$"
    ):
        repository.create_run(
            owner_id,
            mission.mission_id,
            phase="planning",
            agent_id="agent-brain-bot",
            input_payload=payload,  # type: ignore[arg-type]
            event_type="brain.responding",
            event_payload={"text": "working"},
        )

    assert repository.events_after(owner_id, mission.mission_id) == ()


@pytest.mark.postgres
def test_payload_boundary_rejects_excessive_depth(
    mission_database, repository
) -> None:
    _environment, owner_id, _ = mission_database
    mission = repository.create_mission(owner_id, uuid4(), "deep payload")
    payload: dict[str, object] = {}
    current = payload
    for _index in range(17):
        child: dict[str, object] = {}
        current["child"] = child
        current = child

    with pytest.raises(MissionRepositoryError):
        repository.create_run(
            owner_id,
            mission.mission_id,
            phase="planning",
            agent_id="agent-brain-bot",
            input_payload=payload,
            event_type="brain.responding",
            event_payload={"text": "working"},
        )


@pytest.mark.postgres
def test_run_payloads_and_events_share_strict_canonical_boundary(
    mission_database, repository
) -> None:
    environment, owner_id, _ = mission_database
    mission = repository.create_mission(owner_id, uuid4(), "strict run payloads")

    with pytest.raises(MissionRepositoryError):
        repository.create_run(
            owner_id,
            mission.mission_id,
            phase="planning",
            agent_id="agent-brain-bot",
            input_payload={"items": ("coerced",)},
            event_type="brain.responding",
            event_payload={"text": "start"},
        )
    with pytest.raises(MissionRepositoryError):
        repository.create_run(
            owner_id,
            mission.mission_id,
            phase="planning",
            agent_id="agent-brain-bot",
            input_payload={"prompt": "valid"},
            event_type="brain.responding",
            event_payload={"nested": {"systemPrompt": "hidden"}},
        )
    assert _rows(
        environment,
        "select count(*) from platform_control.mission_runs where mission_id=%s",
        (mission.mission_id,),
    ) == [(0,)]

    run = repository.create_run(
        owner_id,
        mission.mission_id,
        phase="planning",
        agent_id="agent-brain-bot",
        input_payload={"b": [2, 1], "a": "value"},
        event_type="brain.responding",
        event_payload={"text": "start", "stage": "planning"},
    )
    replay = repository.create_run(
        owner_id,
        mission.mission_id,
        phase="planning",
        agent_id="agent-brain-bot",
        input_payload={"a": "value", "b": [2, 1]},
        event_type="brain.responding",
        event_payload={"stage": "planning", "text": "start"},
    )
    assert replay.run_id == run.run_id

    with pytest.raises(MissionRepositoryError):
        repository.complete_run(
            owner_id,
            mission.mission_id,
            run.run_id,
            status="completed",
            output_payload={"score": float("nan")},
            event_type="plan.created",
            event_payload={"text": "done"},
            mission_status="delegated",
        )
    with pytest.raises(MissionRepositoryError):
        repository.complete_run(
            owner_id,
            mission.mission_id,
            run.run_id,
            status="completed",
            output_payload={"answer": "safe result"},
            event_type="plan.created",
            event_payload={"nested": {"rawResponse": "hidden"}},
            mission_status="delegated",
        )
    assert _rows(
        environment,
        "select status,output_ciphertext from platform_control.mission_runs "
        "where run_id=%s",
        (run.run_id,),
    ) == [("queued", None)]


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("run_status", "mission_status"),
    (
        ("failed", None),
        ("failed", "completed"),
        ("failed", "cancelled"),
        ("cancelled", None),
        ("cancelled", "failed"),
        ("interrupted", None),
        ("interrupted", "completed"),
        ("completed", "failed"),
        ("completed", "cancelled"),
        ("completed", "interrupted"),
    ),
)
def test_complete_run_rejects_contradictory_outcome_combinations(
    mission_database, repository, run_status: str, mission_status: str | None
) -> None:
    environment, owner_id, _ = mission_database
    mission = repository.create_mission(owner_id, uuid4(), "outcome matrix")
    run = repository.create_run(
        owner_id,
        mission.mission_id,
        phase="planning",
        agent_id="agent-brain-bot",
        input_payload={"prompt": "plan"},
        event_type="brain.responding",
        event_payload={"text": "working"},
    )

    with pytest.raises(MissionRepositoryConflict):
        repository.complete_run(
            owner_id,
            mission.mission_id,
            run.run_id,
            status=run_status,  # type: ignore[arg-type]
            output_payload={"text": "terminal"},
            event_type="mission.failed",
            event_payload={"text": "terminal"},
            mission_status=mission_status,
        )

    assert _rows(
        environment,
        "select status,output_ciphertext from platform_control.mission_runs "
        "where run_id=%s",
        (run.run_id,),
    ) == [("queued", None)]


@pytest.mark.postgres
def test_plan_created_accepts_explicit_selected_agent_delegate_payload(
    mission_database, repository
) -> None:
    _environment, owner_id, _ = mission_database
    mission = repository.create_mission(owner_id, uuid4(), "delegate plan")
    planning = repository.create_run(
        owner_id,
        mission.mission_id,
        phase="planning",
        agent_id="agent-brain-bot",
        input_payload={"prompt": "plan"},
        event_type="brain.responding",
        event_payload={"text": "working"},
    )
    payload = {
        "text": "已选择招聘 Agent",
        "selected_agent_id": "hr-bot",
        "objective": "定义候选人画像",
        "rationale_summary": "需要招聘领域能力",
    }

    completed = repository.complete_run(
        owner_id,
        mission.mission_id,
        planning.run_id,
        status="completed",
        output_payload={"decision": "delegate", "agent_id": "hr-bot"},
        event_type="plan.created",
        event_payload=payload,
        mission_status="delegated",
    )

    assert completed.agent_id == "agent-brain-bot"
    assert repository.events_after(owner_id, mission.mission_id)[-1].payload == payload


@pytest.mark.postgres
def test_plan_created_rejects_agent_id_as_planning_run_producer_field(
    mission_database, repository
) -> None:
    environment, owner_id, _ = mission_database
    mission = repository.create_mission(owner_id, uuid4(), "producer boundary")
    planning = repository.create_run(
        owner_id,
        mission.mission_id,
        phase="planning",
        agent_id="agent-brain-bot",
        input_payload={"prompt": "plan"},
        event_type="brain.responding",
        event_payload={"text": "working"},
    )

    with pytest.raises(MissionRepositoryError):
        repository.complete_run(
            owner_id,
            mission.mission_id,
            planning.run_id,
            status="completed",
            output_payload={"decision": "delegate", "agent_id": "hr-bot"},
            event_type="plan.created",
            event_payload={
                "text": "plan ready",
                "agent_id": "agent-brain-bot",
                "objective": "candidate profile",
                "rationale_summary": "needs recruiting capability",
            },
            mission_status="delegated",
        )

    assert _rows(
        environment,
        "select status,output_ciphertext from platform_control.mission_runs "
        "where run_id=%s",
        (planning.run_id,),
    ) == [("queued", None)]


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("mode", "phase", "run_status", "mission_status", "event_type"),
    (
        ("brain", "planning", "failed", "failed", "mission.completed"),
        ("brain", "planning", "completed", "delegated", "agent.result"),
        ("brain", "planning", "completed", None, "plan.created"),
        ("brain", "professional", "completed", "completed", "mission.completed"),
        ("direct_agent", "direct", "completed", None, "agent.result"),
    ),
)
def test_complete_run_rejects_phase_mode_and_event_mismatches(
    mission_database,
    repository,
    mode: str,
    phase: str,
    run_status: str,
    mission_status: str | None,
    event_type: str,
) -> None:
    environment, owner_id, _ = mission_database
    direct_agent_id = "hr-bot" if mode == "direct_agent" else None
    mission = repository.create_mission(
        owner_id,
        uuid4(),
        "full completion tuple",
        mode=mode,  # type: ignore[arg-type]
        direct_agent_id=direct_agent_id,
    )
    if phase == "professional":
        _advance_to_delegated(repository, owner_id, mission.mission_id)
    agent_id = "agent-brain-bot" if phase == "planning" else "hr-bot"
    run = repository.create_run(
        owner_id,
        mission.mission_id,
        phase=phase,  # type: ignore[arg-type]
        agent_id=agent_id,
        input_payload={"prompt": "run"},
        objective="candidate profile" if phase in {"professional", "direct"} else None,
        event_type="brain.responding" if phase == "planning" else "task.dispatched",
        event_payload=(
            {"text": "working"}
            if phase == "planning"
            else {"agent_id": agent_id, "text": "working"}
        ),
    )

    with pytest.raises(MissionRepositoryConflict):
        repository.complete_run(
            owner_id,
            mission.mission_id,
            run.run_id,
            status=run_status,  # type: ignore[arg-type]
            output_payload={"text": "terminal"},
            event_type=event_type,
            event_payload={"text": "terminal"},
            mission_status=mission_status,
        )

    assert _rows(
        environment,
        "select status,output_ciphertext from platform_control.mission_runs "
        "where run_id=%s",
        (run.run_id,),
    ) == [("queued", None)]


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("mode", "phase", "event_type"),
    (
        ("direct_agent", "planning", "brain.responding"),
        ("brain", "synthesis", "synthesis.started"),
        ("brain", "professional", "task.dispatched"),
        ("brain", "planning", "task.dispatched"),
        ("direct_agent", "direct", "brain.responding"),
    ),
)
def test_create_run_rejects_invalid_locked_creation_tuple(
    mission_database,
    repository,
    mode: str,
    phase: str,
    event_type: str,
) -> None:
    environment, owner_id, _ = mission_database
    mission = repository.create_mission(
        owner_id,
        uuid4(),
        "invalid creation tuple",
        mode=mode,  # type: ignore[arg-type]
        direct_agent_id="hr-bot" if mode == "direct_agent" else None,
    )
    agent_id = "agent-brain-bot" if phase in {"planning", "synthesis"} else "hr-bot"

    with pytest.raises(MissionRepositoryConflict):
        repository.create_run(
            owner_id,
            mission.mission_id,
            phase=phase,  # type: ignore[arg-type]
            agent_id=agent_id,
            input_payload={"prompt": "run"},
            objective=(
                "candidate profile"
                if phase in {"professional", "direct"}
                else None
            ),
            event_type=event_type,
            event_payload=(
                {"agent_id": agent_id}
                if event_type == "task.dispatched"
                else {"text": "working"}
            ),
        )

    assert _rows(
        environment,
        "select count(*) from platform_control.mission_runs where mission_id=%s",
        (mission.mission_id,),
    ) == [(0,)]


@pytest.mark.postgres
def test_create_synthesis_requires_completed_professional_predecessor(
    mission_database, repository
) -> None:
    environment, owner_id, _ = mission_database
    mission = repository.create_mission(owner_id, uuid4(), "ordered phases")
    planning = repository.create_run(
        owner_id,
        mission.mission_id,
        phase="planning",
        agent_id="agent-brain-bot",
        input_payload={"prompt": "plan"},
        event_type="brain.responding",
        event_payload={"text": "working"},
    )
    repository.complete_run(
        owner_id,
        mission.mission_id,
        planning.run_id,
        status="completed",
        output_payload={"decision": "delegate"},
        event_type="plan.created",
        event_payload={"text": "plan ready"},
        mission_status="delegated",
    )
    repository.create_run(
        owner_id,
        mission.mission_id,
        phase="professional",
        agent_id="hr-bot",
        input_payload={"prompt": "professional"},
        objective="candidate profile",
        event_type="task.dispatched",
        event_payload={"agent_id": "hr-bot"},
    )

    with pytest.raises(MissionRepositoryConflict):
        repository.create_run(
            owner_id,
            mission.mission_id,
            phase="synthesis",
            agent_id="agent-brain-bot",
            input_payload={"prompt": "synthesize"},
            event_type="synthesis.started",
            event_payload={"text": "synthesizing"},
        )

    assert _rows(
        environment,
        "select phase,status from platform_control.mission_runs "
        "where mission_id=%s order by created_at",
        (mission.mission_id,),
    ) == [("planning", "completed"), ("professional", "queued")]


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("mode", "phase", "run_status", "mission_status", "event_type"),
    (
        ("brain", "planning", "completed", "delegated", "plan.created"),
        ("brain", "planning", "completed", "completed", "mission.completed"),
        ("brain", "planning", "failed", "failed", "mission.failed"),
        ("brain", "planning", "cancelled", "cancelled", "mission.cancelled"),
        (
            "brain",
            "planning",
            "interrupted",
            "interrupted",
            "mission.interrupted",
        ),
        ("brain", "professional", "completed", None, "agent.result"),
        (
            "brain",
            "professional",
            "failed",
            "partially_completed",
            "mission.failed",
        ),
        (
            "brain",
            "professional",
            "interrupted",
            "partially_completed",
            "mission.interrupted",
        ),
        ("brain", "synthesis", "completed", "completed", "mission.completed"),
        (
            "brain",
            "synthesis",
            "failed",
            "partially_completed",
            "mission.failed",
        ),
        (
            "direct_agent",
            "direct",
            "completed",
            "completed",
            "mission.completed",
        ),
        ("direct_agent", "direct", "failed", "failed", "mission.failed"),
        (
            "direct_agent",
            "direct",
            "cancelled",
            "cancelled",
            "mission.cancelled",
        ),
        (
            "direct_agent",
            "direct",
            "interrupted",
            "interrupted",
            "mission.interrupted",
        ),
    ),
)
def test_complete_run_accepts_explicit_consistent_outcomes(
    mission_database,
    repository,
    mode: str,
    phase: str,
    run_status: str,
    mission_status: str | None,
    event_type: str,
) -> None:
    _environment, owner_id, _ = mission_database
    direct_agent_id = "hr-bot" if mode == "direct_agent" else None
    mission = repository.create_mission(
        owner_id,
        uuid4(),
        "valid outcome",
        mode=mode,  # type: ignore[arg-type]
        direct_agent_id=direct_agent_id,
    )
    if phase == "professional":
        _advance_to_delegated(repository, owner_id, mission.mission_id)
    elif phase == "synthesis":
        _advance_to_synthesis_ready(repository, owner_id, mission.mission_id)
    agent_id = (
        "agent-brain-bot" if phase in {"planning", "synthesis"} else "hr-bot"
    )
    start_event_type = {
        "planning": "brain.responding",
        "professional": "task.dispatched",
        "synthesis": "synthesis.started",
        "direct": "task.dispatched",
    }[phase]
    run = repository.create_run(
        owner_id,
        mission.mission_id,
        phase=phase,  # type: ignore[arg-type]
        agent_id=agent_id,
        input_payload={"prompt": "run"},
        objective=(
            "candidate profile" if phase in {"professional", "direct"} else None
        ),
        event_type=start_event_type,
        event_payload=(
            {"agent_id": agent_id, "text": "working"}
            if start_event_type == "task.dispatched"
            else {"text": "working"}
        ),
    )

    completed = repository.complete_run(
        owner_id,
        mission.mission_id,
        run.run_id,
        status=run_status,  # type: ignore[arg-type]
        output_payload={"text": "terminal"},
        event_type=event_type,
        event_payload={"text": "terminal"},
        mission_status=mission_status,
    )

    assert completed.status == run_status


@pytest.mark.postgres
def test_completion_always_advances_row_version_and_rejects_stale_cas(
    mission_database, repository
) -> None:
    environment, owner_id, _ = mission_database
    mission = repository.create_mission(owner_id, uuid4(), "completion version")
    _advance_to_delegated(repository, owner_id, mission.mission_id)
    run = repository.create_run(
        owner_id,
        mission.mission_id,
        phase="professional",
        agent_id="hr-bot",
        input_payload={"prompt": "professional"},
        objective="candidate profile",
        event_type="task.dispatched",
        event_payload={"agent_id": "hr-bot"},
        expected_mission_status="delegated",
        expected_row_version=2,
    )

    repository.complete_run(
        owner_id,
        mission.mission_id,
        run.run_id,
        status="completed",
        output_payload={"profile": "ready"},
        event_type="agent.result",
        event_payload={"text": "profile ready"},
        expected_mission_status="delegated",
        expected_row_version=3,
    )

    assert _rows(
        environment,
        "select status,row_version from platform_control.missions "
        "where mission_id=%s",
        (mission.mission_id,),
    ) == [("delegated", 4)]
    with pytest.raises(MissionRepositoryConflict):
        repository.create_run(
            owner_id,
            mission.mission_id,
            phase="synthesis",
            agent_id="agent-brain-bot",
            input_payload={"prompt": "synthesize"},
            event_type="synthesis.started",
            event_payload={"text": "synthesizing"},
            expected_mission_status="delegated",
            expected_row_version=3,
        )


@pytest.mark.postgres
def test_concurrent_completion_and_transition_use_one_mission_cas_winner(
    mission_database, repository
) -> None:
    environment, owner_id, _ = mission_database
    mission = repository.create_mission(owner_id, uuid4(), "concurrent CAS")
    planning = repository.create_run(
        owner_id,
        mission.mission_id,
        phase="planning",
        agent_id="agent-brain-bot",
        input_payload={"prompt": "plan"},
        event_type="brain.responding",
        event_payload={"text": "working"},
    )
    barrier = Barrier(2)

    def complete_planning():
        barrier.wait()
        return repository.complete_run(
            owner_id,
            mission.mission_id,
            planning.run_id,
            status="completed",
            output_payload={"decision": "delegate"},
            event_type="plan.created",
            event_payload={"text": "plan ready"},
            mission_status="delegated",
            expected_mission_status="planning",
            expected_row_version=1,
        )

    def dispatch_professional():
        barrier.wait()
        return repository.create_run(
            owner_id,
            mission.mission_id,
            phase="professional",
            agent_id="hr-bot",
            input_payload={"prompt": "professional"},
            objective="candidate profile",
            event_type="task.dispatched",
            event_payload={"agent_id": "hr-bot"},
            expected_mission_status="planning",
            expected_row_version=1,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        completion_future = pool.submit(complete_planning)
        dispatch_future = pool.submit(dispatch_professional)
        completed = completion_future.result()
        with pytest.raises(MissionRepositoryConflict):
            dispatch_future.result()

    assert completed.status == "completed"
    assert _rows(
        environment,
        "select status,row_version from platform_control.missions "
        "where mission_id=%s",
        (mission.mission_id,),
    ) == [("delegated", 2)]
    assert len(repository.events_after(owner_id, mission.mission_id)) == 2


@pytest.mark.postgres
def test_concurrent_identical_phase_retries_share_one_server_run_id(
    mission_database, repository
) -> None:
    environment, owner_id, _ = mission_database
    mission = repository.create_mission(owner_id, uuid4(), "concurrent phase")
    barrier = Barrier(8)

    def create_phase(_index: int):
        barrier.wait()
        return repository.create_run(
            owner_id,
            mission.mission_id,
            phase="planning",
            agent_id="agent-brain-bot",
            input_payload={"prompt": "same"},
            event_type="brain.responding",
            event_payload={"text": "working"},
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        runs = tuple(pool.map(create_phase, range(8)))

    assert len({run.run_id for run in runs}) == 1
    assert _rows(
        environment,
        "select count(*) from platform_control.mission_runs where mission_id=%s",
        (mission.mission_id,),
    ) == [(1,)]
    with pytest.raises(MissionRepositoryConflict):
        repository.create_run(
            owner_id,
            mission.mission_id,
            phase="planning",
            agent_id="agent-brain-bot",
            input_payload={"prompt": "collision"},
            event_type="brain.responding",
            event_payload={"text": "working"},
        )


@pytest.mark.postgres
@pytest.mark.parametrize(
    "operation",
    ("mission_prompt", "event_value", "event_key"),
)
def test_unpaired_surrogate_errors_collapse_to_stable_repository_error(
    mission_database, repository, operation: str
) -> None:
    _environment, owner_id, _ = mission_database

    if operation == "mission_prompt":
        invoke = lambda: repository.create_mission(owner_id, uuid4(), "\ud800")
    else:
        mission = repository.create_mission(owner_id, uuid4(), "surrogate")
        payload = {"text": "\ud800"} if operation == "event_value" else {"\ud800": "x"}
        invoke = lambda: repository.create_run(
            owner_id,
            mission.mission_id,
            phase="planning",
            agent_id="agent-brain-bot",
            input_payload={"prompt": "plan"},
            event_type="brain.responding",
            event_payload=payload,
        )

    with pytest.raises(MissionRepositoryError) as raised:
        invoke()

    assert type(raised.value) is MissionRepositoryError
    assert str(raised.value) == "mission repository unavailable"
