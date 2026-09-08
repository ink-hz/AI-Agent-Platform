from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier, Event
from time import monotonic
from uuid import uuid4

import psycopg
import pytest
from test_agent_brain_conversation_context import _complete_mission
from test_agent_brain_orchestrator import ScriptedRelay
from test_turn_attempts_database import (
    attempt_repository as attempt_repository,  # noqa: PLC0414 - pytest fixture export
)
from test_turn_attempts_database import (
    control_database as control_database,  # noqa: PLC0414 - pytest fixture export
)
from test_turn_attempts_database import (
    conversation_database as conversation_database,  # noqa: PLC0414 - pytest fixture export
)
from test_turn_attempts_database import (
    repository as repository,  # noqa: PLC0414 - pytest fixture export
)

from app.agent_brain.conversation_context import ConversationContextBuilder
from app.agent_brain.conversation_projection import ConversationProjection
from app.agent_brain.conversation_repository import ConversationRepositoryError
from app.agent_brain.models import load_capability_cards
from app.agent_brain.orchestrator import MissionOrchestrator
from app.agent_brain.repository import MissionRepositoryNotFound
from app.agent_brain.turn_attempts import AttemptNotFound, LeaseRejected

pytestmark = pytest.mark.postgres
DRAFT = Path(__file__).parents[1] / "control_migrations/pending/hr_direct_dispatch.sql"


@pytest.fixture()
def direct_database(attempt_repository, conversation_database):
    environment, _, _ = conversation_database
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute("set local role platform_control_owner")
        connection.execute(DRAFT.read_text())
    yield conversation_database
    with psycopg.connect(environment["admin"]) as connection:
        _drop_direct_draft(connection)


def _drop_direct_draft(connection):
    connection.execute("drop table if exists platform_control.v5_source_events")
    connection.execute("drop table if exists platform_control.direct_command_bindings")
    connection.execute("drop function if exists platform_control.preserve_direct_command_binding()")
    connection.execute("delete from platform_control.execution_jobs where job_kind='worker_direct_v5'")
    connection.execute("alter table platform_control.execution_jobs drop constraint execution_jobs_job_kind_v42")
    connection.execute(
        "alter table platform_control.execution_jobs add constraint execution_jobs_job_kind_v42 "
        "check(job_kind in ('legacy_brain','direct_agent','metabot_local'))"
    )
    connection.execute(
        "drop trigger if exists pin_turn_execution_origin "
        "on platform_control.conversation_turns"
    )
    connection.execute(
        "drop function if exists platform_control.pin_turn_execution_origin()"
    )
    connection.execute(
        "alter table platform_control.conversation_turns "
        "drop column if exists execution_owner, "
        "drop column if exists origin_route_epoch"
    )


@pytest.fixture()
def worker_conversation(direct_database, repository):
    environment, owner_id, _ = direct_database
    conversation = repository.ensure_direct_conversation_shell(
        owner_id, uuid4(), direct_agent_id="hr-bot", title="P03 disposable conversation"
    )
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_control.conversations set execution_owner='worker_direct', "
            "route_epoch=7 where conversation_id=%s",
            (conversation.conversation_id,),
        )
    return conversation


@pytest.fixture()
def worker_turn(worker_conversation, repository):
    return repository.append_turn(
        worker_conversation.owner_internal_user_id,
        worker_conversation.conversation_id,
        uuid4(),
        "P03 disposable input",
    )


def test_new_worker_turn_pins_current_routing(worker_turn, attempt_repository):
    with attempt_repository.transaction() as connection:
        turn = connection.execute(
            "select to_jsonb(t)->>'execution_owner' as execution_owner, "
            "to_jsonb(t)->>'origin_route_epoch' as origin_route_epoch "
            "from platform_control.conversation_turns t where turn_id=%s",
            (worker_turn.turn.turn_id,),
        ).fetchone()
    assert turn == {"execution_owner": "worker_direct", "origin_route_epoch": "7"}


def test_real_direct_worker_prepares_existing_turn_without_model_call(worker_turn, attempt_repository, repository, direct_database):
    from app.agent_brain.direct_command_binding import DirectCommandBindingRepository
    from app.agent_brain.direct_mission_adapter import DirectMissionAdapter
    from app.agent_brain.direct_worker import DirectWorker
    from app.agent_brain.turn_result_projection import TurnResultProjector
    from app.execution_relay.repository import ExecutionRelayRepository
    environment, _, _ = direct_database
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute("set local role platform_control_owner")
        connection.execute(DRAFT.with_name("hr_web_result_recovery.sql").read_text())
    bindings = DirectCommandBindingRepository(ExecutionRelayRepository(environment["urls"]["platform_control_app"], content_codec=repository.content_codec))
    adapter = DirectMissionAdapter(attempt_repository, bindings, ConversationContextBuilder(repository), TurnResultProjector(attempt_repository, bindings))
    worker = DirectWorker(attempt_repository, adapter)
    from tests.helpers.hr_web_loop import WebLoop
    identity = WebLoop(environment, worker_turn.conversation.owner_internal_user_id, worker_turn.conversation.conversation_id)
    try:
        assert worker.tick() == 1
    finally:
        worker.close()
        identity.close()
    with attempt_repository.transaction() as connection:
        row = connection.execute("select j.run_id,mt.task_id,mr.run_id as compatibility_run from platform_control.direct_command_bindings b join platform_control.execution_jobs j using(job_id) join platform_control.mission_tasks mt on mt.task_id=j.run_id join platform_control.mission_runs mr on mr.run_id=j.run_id where b.attempt_id=(select attempt_id from platform_control.turn_attempts where turn_id=%s)", (worker_turn.turn.turn_id,)).fetchone()
    assert row and row["run_id"] == row["task_id"] == row["compatibility_run"]


def test_legacy_claim_excludes_worker_owned_turn(repository, worker_turn):
    claimed = repository._missions.claim_pending(limit=50)

    assert worker_turn.mission.mission_id not in {row.mission_id for row in claimed}


def test_new_worker_intake_creates_one_queued_attempt(worker_turn, attempt_repository):
    with attempt_repository.transaction() as connection:
        attempts = connection.execute(
            "select executor_kind,status,attempt_no,transport_run_id from "
            "platform_control.turn_attempts where turn_id=%s",
            (worker_turn.turn.turn_id,),
        ).fetchall()
    assert attempts == [
        {
            "executor_kind": "worker_direct",
            "status": "queued",
            "attempt_no": 1,
            "transport_run_id": None,
        }
    ]


@pytest.mark.parametrize(
    "column,value",
    [
        ("execution_owner", "legacy_api_v1"),
        ("origin_route_epoch", 8),
    ],
)
def test_turn_origin_cannot_be_rewritten(worker_turn, direct_database, column, value):
    environment, _, _ = direct_database
    with (
        psycopg.connect(environment["admin"]) as connection,
        pytest.raises(psycopg.errors.CheckViolation),
    ):
        connection.execute(
            psycopg.sql.SQL(
                "update platform_control.conversation_turns set {}=%s where turn_id=%s"
            ).format(psycopg.sql.Identifier(column)),
            (value, worker_turn.turn.turn_id),
        )


def test_pinned_legacy_turn_cannot_be_adopted_after_routing_changes(
    direct_database, repository, attempt_repository
):
    environment, owner_id, _ = direct_database
    legacy = repository.start(
        owner_id, uuid4(), "Legacy input", mode="direct_agent", direct_agent_id="hr-bot"
    )
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_control.conversations set execution_owner='worker_direct',route_epoch=1 "
            "where conversation_id=%s",
            (legacy.conversation.conversation_id,),
        )
    with pytest.raises(AttemptNotFound):
        attempt_repository.create_queued(legacy.turn.turn_id, "worker_direct")


def test_worker_intake_missing_provenance_schema_rolls_back(
    attempt_repository, conversation_database, repository
):
    environment, owner_id, _ = conversation_database
    shell = repository.ensure_direct_conversation_shell(
        owner_id, uuid4(), direct_agent_id="hr-bot", title="Missing draft"
    )
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_control.conversations set execution_owner='worker_direct' "
            "where conversation_id=%s",
            (shell.conversation_id,),
        )
    with pytest.raises(ConversationRepositoryError):
        repository.append_turn(
            owner_id, shell.conversation_id, uuid4(), "Must not execute"
        )
    assert repository.latest_turn_for_owner(owner_id, shell.conversation_id) is None


def test_attempt_insert_failure_rolls_back_whole_intake(
    worker_conversation, direct_database, repository
):
    environment, owner_id, _ = direct_database
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "revoke insert on platform_control.turn_attempts from platform_control_app"
        )
    try:
        with pytest.raises(ConversationRepositoryError):
            repository.append_turn(
                owner_id, worker_conversation.conversation_id, uuid4(), "Rollback input"
            )
        with psycopg.connect(environment["admin"]) as connection:
            counts = connection.execute(
                "select (select count(*) from platform_control.conversation_turns),"
                "(select count(*) from platform_control.conversation_messages),"
                "(select count(*) from platform_control.conversation_events),"
                "(select count(*) from platform_control.missions),"
                "(select count(*) from platform_control.turn_attempts)"
            ).fetchone()
        assert counts == (0, 0, 0, 0, 0)
    finally:
        with psycopg.connect(environment["admin"]) as connection:
            connection.execute(
                "grant insert on platform_control.turn_attempts to platform_control_app"
            )


def test_api_orchestration_cannot_read_worker_mission(worker_turn, repository):
    with pytest.raises(MissionRepositoryNotFound):
        repository._missions.mission_for_orchestration(
            worker_turn.conversation.owner_internal_user_id,
            worker_turn.mission.mission_id,
        )


def test_stale_claim_cannot_create_legacy_run_for_worker_turn(worker_turn, repository):
    # An already-read/retained Mission object cannot bypass a fresh locked write check.
    mission = worker_turn.mission
    with pytest.raises(MissionRepositoryNotFound):
        repository._missions.create_run(
            mission.owner_internal_user_id,
            mission.mission_id,
            phase="direct",
            agent_id="hr-bot",
            input_payload={"prompt": "Synthetic input"},
            objective="Synthetic objective",
            event_type="task.dispatched",
            event_payload={"agent_id": "hr-bot"},
            expected_mission_status=mission.status,
            expected_row_version=mission.row_version,
        )


@pytest.mark.parametrize("pending", [False, True])
def test_api_terminal_projection_excludes_worker_turn(
    worker_turn, direct_database, repository, pending
):
    environment, owner_id, _ = direct_database
    # Adversarial historical Mission evidence, not a v5 Result-completion fixture.
    _complete_mission(
        environment,
        repository,
        worker_turn.mission.mission_id,
        "Untrusted legacy result",
    )
    projector = ConversationProjection(repository)
    projected = (
        projector.project_pending()
        if pending
        else projector.project_terminal(worker_turn.mission.mission_id)
    )
    assert not projected
    assert (
        repository.latest_turn_for_owner(
            owner_id, worker_turn.conversation.conversation_id
        ).status
        == "accepted"
    )


def test_api_progress_projection_excludes_worker_turn(
    worker_turn, direct_database, repository
):
    environment, owner_id, _ = direct_database
    _complete_mission(
        environment,
        repository,
        worker_turn.mission.mission_id,
        "Untrusted legacy progress",
    )
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_control.mission_events set event_type='task.dispatched' "
            "where mission_id=%s and seq=2",
            (worker_turn.mission.mission_id,),
        )
    assert (
        repository.sync_mission_events(
            owner_id, worker_turn.conversation.conversation_id
        )
        == 0
    )


def test_old_inflight_legacy_mission_finishes_after_conversation_route_change(
    direct_database, repository
):
    environment, owner_id, _ = direct_database
    old = repository.start(
        owner_id,
        uuid4(),
        "Legacy in-flight input",
        mode="direct_agent",
        direct_agent_id="hr-bot",
    )
    relay = ScriptedRelay()
    service = MissionOrchestrator(
        repository._missions,
        relay,
        capability_provider=lambda _owner: load_capability_cards(),
        conversation_context_builder=ConversationContextBuilder(repository),
        conversation_projection=ConversationProjection(repository),
    )
    service.advance_pending(limit=50)
    (run_id,) = relay.payloads
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_control.conversations set execution_owner='worker_direct',route_epoch=7 "
            "where conversation_id=%s",
            (old.conversation.conversation_id,),
        )
    assert old.mission.mission_id in {
        row.mission_id for row in repository._missions.claim_pending(50)
    }
    relay.terminal(run_id, "completed", "Legacy original result")
    service.advance_pending(limit=50)
    service.advance_pending(limit=50)
    assert (
        repository.latest_turn_for_owner(
            owner_id, old.conversation.conversation_id
        ).status
        == "completed"
    )
    assert (
        repository.messages_after(owner_id, old.conversation.conversation_id)[
            -1
        ].content
        == "Legacy original result"
    )
    assert len(relay.payloads) == 1
    with psycopg.connect(environment["admin"]) as connection:
        assert connection.execute(
            "select execution_owner,origin_route_epoch from platform_control.conversation_turns where turn_id=%s",
            (old.turn.turn_id,),
        ).fetchone() == ("legacy_api_v1", 0)


def test_current_lease_renewal_extends_same_attempt(worker_turn, attempt_repository):
    lease = attempt_repository.claim_due(uuid4(), 60)
    renewed = attempt_repository.renew(lease, 120)
    assert renewed.expires_at > lease.expires_at
    assert renewed.attempt_id == lease.attempt_id
    assert renewed.executor_id == lease.executor_id
    assert renewed.lease_epoch == lease.lease_epoch
    row = attempt_repository.get_for_owner(
        worker_turn.conversation.owner_internal_user_id, lease.attempt_id
    )
    assert row.lease_expires_at == renewed.expires_at
    assert row.attempt_no == 1
    assert row.transport_run_id is None


@pytest.mark.parametrize(
    "mutation",
    [
        "old_epoch",
        "future_epoch",
        "wrong_process",
        "wrong_kind",
        "expired",
        "route_changed",
        "owner_changed",
    ],
)
def test_renewal_rejects_noncurrent_ownership(
    worker_turn, direct_database, attempt_repository, mutation
):
    environment, _, _ = direct_database
    lease = attempt_repository.claim_due(uuid4(), 60)
    if mutation in {"old_epoch", "future_epoch"}:
        lease = replace(
            lease,
            lease_epoch=lease.lease_epoch + (-1 if mutation == "old_epoch" else 1),
        )
    elif mutation == "wrong_process":
        lease = replace(lease, executor_id=uuid4())
    elif mutation == "wrong_kind":
        lease = replace(lease, executor_kind="legacy_api_v1")
    else:
        with psycopg.connect(environment["admin"]) as connection:
            if mutation == "expired":
                connection.execute(
                    "update platform_control.turn_attempts set lease_expires_at=clock_timestamp()-interval '1 second' "
                    "where attempt_id=%s",
                    (lease.attempt_id,),
                )
            elif mutation == "route_changed":
                connection.execute(
                    "update platform_control.conversations set route_epoch=8 where conversation_id=%s",
                    (worker_turn.conversation.conversation_id,),
                )
            else:
                connection.execute(
                    "update platform_control.conversations set execution_owner='legacy_api_v1' where conversation_id=%s",
                    (worker_turn.conversation.conversation_id,),
                )
    with pytest.raises(LeaseRejected):
        attempt_repository.renew(lease, 120)


def test_worker_routing_cannot_adopt_other_bot(direct_database, repository):
    environment, owner_id, _ = direct_database
    shell = repository.ensure_direct_conversation_shell(
        owner_id, uuid4(), direct_agent_id="fae-bot", title="Other Bot"
    )
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_control.conversations set execution_owner='worker_direct' "
            "where conversation_id=%s",
            (shell.conversation_id,),
        )
    with pytest.raises(ConversationRepositoryError):
        repository.append_turn(
            owner_id, shell.conversation_id, uuid4(), "Other Bot input"
        )


def test_standalone_brain_and_other_bot_keep_legacy_claims(direct_database, repository):
    _, owner_id, _ = direct_database
    standalone = repository._missions.create_mission(owner_id, uuid4(), "Standalone")
    brain = repository.start(owner_id, uuid4(), "Brain input")
    other = repository.start(
        owner_id,
        uuid4(),
        "Other Bot input",
        mode="direct_agent",
        direct_agent_id="fae-bot",
    )
    claims = {row.mission_id for row in repository._missions.claim_pending(50)}
    assert claims == {
        standalone.mission_id,
        brain.mission.mission_id,
        other.mission.mission_id,
    }


def test_concurrent_duplicate_intake_creates_one_attempt(
    worker_conversation, repository, attempt_repository
):
    request_id = uuid4()
    barrier = Barrier(2)

    def submit():
        barrier.wait(timeout=5)
        return repository.append_turn(
            worker_conversation.owner_internal_user_id,
            worker_conversation.conversation_id,
            request_id,
            "Concurrent input",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(submit) for _ in range(2)]
        submissions = [future.result(timeout=10) for future in futures]
    assert {item.turn.turn_id for item in submissions} == {submissions[0].turn.turn_id}
    assert sum(item.created for item in submissions) == 1
    with attempt_repository.transaction() as connection:
        assert (
            connection.execute(
                "select count(*) as count from platform_control.turn_attempts"
            ).fetchone()["count"]
            == 1
        )


def _wait_for_blocker(environment, waiter_pid, blocker_pid):
    deadline = monotonic() + 5
    with psycopg.connect(environment["admin"], autocommit=True) as connection:
        while monotonic() < deadline:
            if connection.execute(
                "select %s=any(pg_blocking_pids(%s))", (blocker_pid, waiter_pid)
            ).fetchone()[0]:
                return
            Event().wait(0.01)
    pytest.fail("expected database lock wait was not observed")


def test_duplicate_submit_and_renewal_use_compatible_lock_order(
    worker_turn, direct_database, repository, attempt_repository, monkeypatch
):
    environment, owner_id, _ = direct_database
    lease = attempt_repository.claim_due(uuid4(), 60)
    replay_locked, release_replay, renew_started = Event(), Event(), Event()
    pids = {}
    original_replay = repository._replay_locked

    def pause_replay(cursor, *args, **kwargs):
        # append_turn already owns its real Conversation row lock here.
        pids["replay"] = cursor.connection.info.backend_pid
        replay_locked.set()
        assert release_replay.wait(timeout=10)
        return original_replay(cursor, *args, **kwargs)

    monkeypatch.setattr(repository, "_replay_locked", pause_replay)

    def renew():
        with attempt_repository.transaction() as connection:
            pids["renew"] = connection.info.backend_pid
            renew_started.set()
            return attempt_repository.renew(lease, 120, connection=connection)

    with ThreadPoolExecutor(max_workers=2) as pool:
        replay = pool.submit(
            repository.append_turn,
            owner_id,
            worker_turn.conversation.conversation_id,
            worker_turn.turn.client_request_id,
            "P03 disposable input",
        )
        try:
            assert replay_locked.wait(timeout=5)
            renewal = pool.submit(renew)
            assert renew_started.wait(timeout=5)
            _wait_for_blocker(environment, pids["renew"], pids["replay"])
        finally:
            release_replay.set()
        assert replay.result(timeout=10).created is False
        assert renewal.result(timeout=10).expires_at > lease.expires_at


def test_renewal_rechecks_expiry_after_lock_wait(
    worker_turn, direct_database, attempt_repository
):
    environment, _, _ = direct_database
    lease = attempt_repository.claim_due(uuid4(), 60)
    started = Event()
    pids = {}

    def renew():
        with attempt_repository.transaction() as connection:
            pids["renew"] = connection.info.backend_pid
            started.set()
            return attempt_repository.renew(lease, 120, connection=connection)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with psycopg.connect(environment["admin"]) as blocker:
            blocker.execute(
                "select conversation_id from platform_control.conversations where conversation_id=%s for update",
                (worker_turn.conversation.conversation_id,),
            )
            future = pool.submit(renew)
            assert started.wait(timeout=5)
            _wait_for_blocker(environment, pids["renew"], blocker.info.backend_pid)
            blocker.execute(
                "update platform_control.turn_attempts set lease_expires_at=clock_timestamp()-interval '1 second' "
                "where attempt_id=%s",
                (lease.attempt_id,),
            )
        with pytest.raises(LeaseRejected):
            future.result(timeout=10)


def test_renewal_joins_caller_transaction_and_rolls_back(
    worker_turn, attempt_repository
):
    lease = attempt_repository.claim_due(uuid4(), 60)
    with (
        pytest.raises(RuntimeError, match="abort renewal"),
        attempt_repository.transaction() as connection,
    ):
        renewed = attempt_repository.renew(lease, 120, connection=connection)
        assert renewed.expires_at > lease.expires_at
        raise RuntimeError("abort renewal")
    row = attempt_repository.get_for_owner(
        worker_turn.conversation.owner_internal_user_id, lease.attempt_id
    )
    assert row.lease_expires_at == lease.expires_at


def test_provenance_migration_preserves_preexisting_legacy_turn(
    attempt_repository, conversation_database, repository
):
    environment, owner_id, _ = conversation_database
    legacy = repository.start(
        owner_id,
        uuid4(),
        "Preexisting legacy input",
        mode="direct_agent",
        direct_agent_id="hr-bot",
    )
    try:
        with psycopg.connect(environment["admin"]) as connection:
            connection.execute(
                "update platform_control.conversations set execution_owner='worker_direct',route_epoch=7 "
                "where conversation_id=%s",
                (legacy.conversation.conversation_id,),
            )
            connection.execute("set local role platform_control_owner")
            connection.execute(DRAFT.read_text())
            origin = connection.execute(
                "select execution_owner,origin_route_epoch from platform_control.conversation_turns "
                "where turn_id=%s",
                (legacy.turn.turn_id,),
            ).fetchone()
        assert origin == ("legacy_api_v1", 0)
        assert legacy.mission.mission_id in {
            row.mission_id for row in repository._missions.claim_pending(50)
        }
        with pytest.raises(AttemptNotFound):
            attempt_repository.create_queued(legacy.turn.turn_id, "worker_direct")
    finally:
        with psycopg.connect(environment["admin"]) as connection:
            _drop_direct_draft(connection)
