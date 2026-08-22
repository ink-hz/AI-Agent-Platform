from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import inspect
from uuid import UUID, uuid4

import psycopg
import pytest

from app.agent_brain.repository import (
    MissionRepository,
    MissionRepositoryConflict,
    MissionRepositoryError,
    MissionRepositoryNotFound,
)
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
    repository.append_event(
        owner_id, mission.mission_id, "mission.started", {"text": "safe event"}
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
    first = repository.append_event(
        owner_id, newest.mission_id, "mission.started", {"text": "one"}
    )
    second = repository.append_event(
        owner_id, newest.mission_id, "brain.responding", {"text": "two"}
    )

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

    with ThreadPoolExecutor(max_workers=8) as pool:
        events = tuple(
            pool.map(
                lambda index: repository.append_event(
                    owner_id,
                    mission.mission_id,
                    "agent.progress",
                    {"index": index},
                ),
                range(24),
            )
        )

    assert sorted(event.seq for event in events) == list(range(1, 25))
    replay = repository.events_after(owner_id, mission.mission_id)
    assert tuple(event.seq for event in replay) == tuple(range(1, 25))


@pytest.mark.postgres
def test_run_task_input_output_and_events_use_mission_and_row_bound_subjects(
    mission_database, repository
) -> None:
    environment, owner_id, _ = mission_database
    mission = repository.create_mission(owner_id, uuid4(), "delegate this")
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
        "where mission_id=%s",
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
    assert [event.event_type for event in events] == ["task.dispatched", "agent.result"]
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
    arguments = {
        "phase": phase,
        "agent_id": agent_id,
        "input_payload": {"prompt": f"run {phase}"},
        "objective": objective,
        "event_type": event_type,
        "event_payload": {"text": f"start {phase}"},
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
    ] == [event_type]
    with pytest.raises(MissionRepositoryConflict):
        repository.create_run(
            owner_id,
            mission.mission_id,
            phase=phase,
            agent_id=agent_id,
            input_payload={"prompt": "changed after retry"},
            objective=objective,
            event_type=event_type,
            event_payload={"text": f"start {phase}"},
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

    with pytest.raises(MissionRepositoryError) as raised:
        repository.mission_for_owner(owner_id, mission.mission_id)

    assert type(raised.value) is MissionRepositoryError
    assert str(raised.value) == "mission repository unavailable"
    assert "top secret" not in repr(raised.value)


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
        repository.append_event(
            owner_id, mission.mission_id, "agent.progress", nested
        )

    assert type(raised.value) is MissionRepositoryError
    assert str(raised.value) == "mission repository unavailable"


def test_repository_source_owner_scopes_reads_and_generates_ids_internally() -> None:
    source = inspect.getsource(MissionRepository)

    assert "uuid4()" in source
    assert "owner_internal_user_id=%s" in source
    assert " for update" in source.lower()
    assert "ContentCodec" in source
