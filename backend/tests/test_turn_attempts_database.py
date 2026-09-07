from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier, Event
from uuid import uuid4

import psycopg
import pytest
from test_agent_brain_conversation_repository import (
    _codec,
)
from test_agent_brain_conversation_repository import (
    conversation_database as conversation_database,  # noqa: PLC0414 - pytest fixture export
)
from test_agent_brain_conversation_repository import (
    repository as repository,  # noqa: PLC0414 - pytest fixture export
)
from test_control_plane_migration import control_database  # noqa: F401

from app.agent_brain.conversation_repository import event_subject, message_subject
from app.agent_brain.conversation_service import ConversationCommandService
from app.agent_brain.turn_attempts import (
    ActiveAttemptConflict,
    AttemptNotFound,
    LeaseRejected,
    TerminalEvidence,
    TurnAttemptRepository,
)
from app.control_plane.migrate import load_numbered_migrations

DRAFT = Path(__file__).parents[1] / "control_migrations/pending/hr_turn_attempts.sql"
pytestmark = pytest.mark.postgres


@pytest.fixture()
def attempt_repository(conversation_database):
    environment, owner_id, _ = conversation_database
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute("set local role platform_control_owner")
        connection.execute(DRAFT.read_text())
    yield TurnAttemptRepository(environment["urls"]["platform_control_app"], _codec())
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "delete from platform_control.turn_attempts a using "
            "platform_control.conversation_turns t, platform_control.conversations c "
            "where a.turn_id=t.turn_id and t.conversation_id=c.conversation_id "
            "and c.owner_internal_user_id=%s",
            (owner_id,),
        )
        connection.execute("drop table platform_control.turn_attempts")
        connection.execute(
            "alter table platform_control.conversations drop column execution_owner, "
            "drop column route_epoch, drop column snapshot_version"
        )


@pytest.fixture()
def queued_attempt(attempt_repository, repository, conversation_database):
    environment, owner_id, _ = conversation_database
    submission = ConversationCommandService(repository, v2_enabled=False).start(
        owner_id,
        uuid4(),
        "P02 disposable turn",
        mode="direct_agent",
        direct_agent_id="hr-bot",
    )
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_control.conversations set execution_owner='worker_direct' "
            "where conversation_id=%s",
            (submission.conversation.conversation_id,),
        )
    return attempt_repository.create_queued(submission.turn.turn_id, "worker_direct")


def test_cancel_request_does_not_release_live_attempt(
    attempt_repository, queued_attempt
):
    repo, attempt = attempt_repository, queued_attempt
    repo.request_cancel(attempt.owner_id, attempt.turn_id)
    with pytest.raises(ActiveAttemptConflict):
        repo.create_queued(attempt.turn_id, "worker_direct")
    row = repo.get_for_owner(attempt.owner_id, attempt.attempt_id)
    assert row.cancel_requested_at is not None
    assert row.status == "queued"


def test_concurrent_claim_has_one_holder(attempt_repository, queued_attempt):
    gate = Barrier(2)

    def claim(_):
        gate.wait(timeout=5)
        return attempt_repository.claim_due(uuid4(), 60)

    with ThreadPoolExecutor(max_workers=2) as pool:
        leases = [lease for lease in pool.map(claim, range(2)) if lease is not None]
    assert len(leases) == 1
    assert leases[0].attempt_id == queued_attempt.attempt_id
    assert leases[0].lease_epoch == 1
    assert leases[0].status == "running"
    row = attempt_repository.get_for_owner(
        queued_attempt.owner_id, queued_attempt.attempt_id
    )
    assert row.executor_id == str(leases[0].executor_id)
    assert row.attempt_no == 1


def test_expired_lease_is_reconciled_without_new_attempt(
    attempt_repository,
    queued_attempt,
    conversation_database,
):
    first = attempt_repository.claim_due(uuid4(), 60)
    environment, _, _ = conversation_database
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_control.turn_attempts set lease_expires_at=clock_timestamp()-interval '1 second' "
            "where attempt_id=%s",
            (first.attempt_id,),
        )
    recovered = attempt_repository.claim_due(uuid4(), 60)
    assert recovered is not None
    assert recovered.status == "reconciling"
    assert recovered.attempt_id == first.attempt_id
    assert recovered.lease_epoch == 2
    assert recovered.executor_id != first.executor_id
    with pytest.raises(ActiveAttemptConflict):
        attempt_repository.create_queued(queued_attempt.turn_id, "worker_direct")
    assert (
        attempt_repository.get_for_owner(
            queued_attempt.owner_id, first.attempt_id
        ).attempt_no
        == 1
    )


def test_current_lease_records_terminal(attempt_repository, queued_attempt):
    lease = attempt_repository.claim_due(uuid4(), 60)
    with attempt_repository.transaction() as connection:
        outcome = attempt_repository.record_terminal(
            lease,
            TerminalEvidence("failed", reason_code="executor_stopped"),
            connection=connection,
        )
        assert outcome.applied is True
    assert (
        attempt_repository.get_for_owner(
            queued_attempt.owner_id, lease.attempt_id
        ).status
        == "failed"
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "old_epoch",
        "future_epoch",
        "old_process",
        "wrong_kind",
        "expired",
        "wrong_owner",
    ],
)
def test_terminal_rejects_invalid_fence(
    attempt_repository, queued_attempt, conversation_database, mutation
):
    lease = attempt_repository.claim_due(uuid4(), 60)
    if mutation == "old_epoch":
        lease = replace(lease, lease_epoch=lease.lease_epoch - 1)
    elif mutation == "future_epoch":
        lease = replace(lease, lease_epoch=lease.lease_epoch + 1)
    elif mutation == "old_process":
        lease = replace(lease, executor_id=uuid4())
    elif mutation == "wrong_kind":
        lease = replace(lease, executor_kind="legacy_api_v1")
    else:
        environment, _, _ = conversation_database
        with psycopg.connect(environment["admin"]) as connection:
            if mutation == "expired":
                connection.execute(
                    "update platform_control.turn_attempts set lease_expires_at=clock_timestamp()-interval '1 second' "
                    "where attempt_id=%s",
                    (lease.attempt_id,),
                )
            else:
                connection.execute(
                    "update platform_control.conversations set execution_owner='legacy_api_v1'"
                )
    with pytest.raises(LeaseRejected), attempt_repository.transaction() as connection:
        attempt_repository.record_terminal(
            lease, TerminalEvidence("cancelled"), connection=connection
        )
    assert (
        attempt_repository.get_for_owner(
            queued_attempt.owner_id, lease.attempt_id
        ).status
        == "running"
    )


def test_identical_terminal_replay_is_read_only(attempt_repository, queued_attempt):
    lease = attempt_repository.claim_due(uuid4(), 60)
    evidence = TerminalEvidence("cancelled", reason_code="executor_stopped")
    with attempt_repository.transaction() as connection:
        first = attempt_repository.record_terminal(
            lease, evidence, connection=connection
        )
    before = attempt_repository.get_for_owner(queued_attempt.owner_id, lease.attempt_id)
    with attempt_repository.transaction() as connection:
        replay = attempt_repository.record_terminal(
            lease, evidence, connection=connection
        )
    assert first.applied is True
    assert replay.applied is False
    assert replay.status == "cancelled"
    assert (
        attempt_repository.get_for_owner(queued_attempt.owner_id, lease.attempt_id)
        == before
    )
    assert (
        attempt_repository.request_cancel(
            queued_attempt.owner_id, queued_attempt.turn_id
        )
        == "too_late"
    )
    with pytest.raises(LeaseRejected), attempt_repository.transaction() as connection:
        attempt_repository.record_terminal(
            lease, TerminalEvidence("failed"), connection=connection
        )


def test_terminal_requires_non_autocommit_transaction(
    attempt_repository, queued_attempt, conversation_database
):
    lease = attempt_repository.claim_due(uuid4(), 60)
    environment, _, _ = conversation_database
    with (
        psycopg.connect(
            environment["urls"]["platform_control_app"], autocommit=True
        ) as connection,
        pytest.raises(ValueError, match="transaction"),
    ):
        attempt_repository.record_terminal(
            lease, TerminalEvidence("failed"), connection=connection
        )
    assert (
        attempt_repository.get_for_owner(
            queued_attempt.owner_id, lease.attempt_id
        ).status
        == "running"
    )


def _publish_result(connection, attempt, message_id):
    """P04 composition stand-in, persisting actual encrypted Result/Turn/event rows."""
    with connection.cursor(row_factory=psycopg.rows.dict_row) as cursor:
        turn = cursor.execute(
            "select * from platform_control.conversation_turns where turn_id=%s for update",
            (attempt.turn_id,),
        ).fetchone()
        conversation_id = turn["conversation_id"]
        sealed = _codec().seal_json(
            message_subject(conversation_id, message_id), {"text": "P02 result"}
        )
        cursor.execute(
            "insert into platform_control.conversation_messages "
            "(message_id,conversation_id,seq,role,content_ciphertext,encryption_key_version,turn_id,delivery_status,completed_at) "
            "select %s,%s,coalesce(max(seq),0)+1,'assistant',%s,%s,%s,'completed',now() "
            "from platform_control.conversation_messages where conversation_id=%s",
            (
                message_id,
                conversation_id,
                sealed.ciphertext,
                sealed.key_version,
                attempt.turn_id,
                conversation_id,
            ),
        )
        cursor.execute(
            "update platform_control.conversation_turns set status='completed',assistant_message_id=%s where turn_id=%s",
            (message_id, attempt.turn_id),
        )
        event_id = uuid4()
        payload = _codec().seal_json(
            event_subject(conversation_id, event_id), {"status": "completed"}
        )
        cursor.execute(
            "insert into platform_control.conversation_events "
            "(event_id,conversation_id,turn_id,seq,event_type,payload_ciphertext,encryption_key_version) "
            "select %s,%s,%s,coalesce(max(seq),0)+1,'turn.completed',%s,%s "
            "from platform_control.conversation_events where conversation_id=%s",
            (
                event_id,
                conversation_id,
                attempt.turn_id,
                payload.ciphertext,
                payload.key_version,
                conversation_id,
            ),
        )
        return event_id


def test_completed_requires_matching_published_result(
    attempt_repository, queued_attempt, repository, conversation_database
):
    _, owner_id, _ = conversation_database
    other = repository.start(owner_id, uuid4(), "another conversation")
    lease = attempt_repository.claim_due(uuid4(), 60)
    # An existing user message from a different conversation is never result evidence.
    with pytest.raises(LeaseRejected), attempt_repository.transaction() as connection:
        attempt_repository.record_terminal(
            lease,
            TerminalEvidence("completed", other.message.message_id),
            connection=connection,
        )
    assert (
        attempt_repository.get_for_owner(owner_id, queued_attempt.attempt_id).status
        == "running"
    )


def test_completed_result_turn_event_and_attempt_roll_back_together(
    attempt_repository, queued_attempt, conversation_database
):
    lease = attempt_repository.claim_due(uuid4(), 60)
    message_id = uuid4()
    with (
        pytest.raises(RuntimeError, match="abort publication"),
        attempt_repository.transaction() as connection,
    ):
        event_id = _publish_result(connection, queued_attempt, message_id)
        outcome = attempt_repository.record_terminal(
            lease,
            TerminalEvidence("completed", message_id),
            connection=connection,
        )
        assert outcome.applied is True
        raise RuntimeError("abort publication")
    assert (
        attempt_repository.get_for_owner(
            queued_attempt.owner_id, lease.attempt_id
        ).status
        == "running"
    )
    environment, _, _ = conversation_database
    with psycopg.connect(environment["admin"]) as connection:
        assert (
            connection.execute(
                "select 1 from platform_control.conversation_messages where message_id=%s",
                (message_id,),
            ).fetchone()
            is None
        )
        assert (
            connection.execute(
                "select 1 from platform_control.conversation_events where event_id=%s",
                (event_id,),
            ).fetchone()
            is None
        )
        assert connection.execute(
            "select status,assistant_message_id from platform_control.conversation_turns where turn_id=%s",
            (queued_attempt.turn_id,),
        ).fetchone() == ("accepted", None)


def test_result_and_cancelled_race_commits_only_one_terminal(
    attempt_repository, queued_attempt, conversation_database
):
    lease = attempt_repository.claim_due(uuid4(), 60)
    message_id = uuid4()
    gate = Barrier(2)

    def finish(status):
        try:
            with attempt_repository.transaction() as connection:
                if status == "completed":
                    _publish_result(connection, queued_attempt, message_id)
                gate.wait(timeout=5)
                return attempt_repository.record_terminal(
                    lease,
                    TerminalEvidence(
                        status, message_id if status == "completed" else None
                    ),
                    connection=connection,
                )
        except LeaseRejected:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = [
            outcome
            for outcome in pool.map(finish, ["completed", "cancelled"])
            if outcome
        ]
    assert len(outcomes) == 1
    assert outcomes[0].applied is True
    row = attempt_repository.get_for_owner(queued_attempt.owner_id, lease.attempt_id)
    assert row.status == outcomes[0].status
    with psycopg.connect(conversation_database[0]["admin"]) as connection:
        exists = connection.execute(
            "select 1 from platform_control.conversation_messages where message_id=%s",
            (message_id,),
        ).fetchone()
        assert (exists is not None) == (row.status == "completed")
    assert (
        attempt_repository.request_cancel(
            queued_attempt.owner_id, queued_attempt.turn_id
        )
        == "too_late"
    )


def test_cancelled_queue_is_claimed_for_reconciliation_not_dispatch(
    attempt_repository, queued_attempt
):
    attempt_repository.request_cancel(queued_attempt.owner_id, queued_attempt.turn_id)
    lease = attempt_repository.claim_due(uuid4(), 60)
    assert lease.status == "reconciling"
    with attempt_repository.transaction() as connection:
        assert attempt_repository.record_terminal(
            lease, TerminalEvidence("cancelled"), connection=connection
        ).applied


@pytest.mark.parametrize("seconds", [0, -1, True, 0.5])
def test_claim_requires_positive_integer_lease(
    attempt_repository, queued_attempt, seconds
):
    with pytest.raises(ValueError, match="lease"):
        attempt_repository.claim_due(uuid4(), seconds)


def test_create_rejects_autocommit_connection(
    attempt_repository, repository, conversation_database
):
    environment, owner_id, _ = conversation_database
    turn = repository.start(owner_id, uuid4(), "autocommit attempt").turn
    with (
        psycopg.connect(
            environment["urls"]["platform_control_app"], autocommit=True
        ) as connection,
        pytest.raises(ValueError, match="transaction"),
    ):
        attempt_repository.create_queued(
            turn.turn_id, "legacy_api_v1", connection=connection
        )


def test_owner_scoped_reads_cancellation_and_submission(
    attempt_repository, queued_attempt, repository, conversation_database
):
    _, _, other_owner_id = conversation_database
    with pytest.raises(AttemptNotFound):
        attempt_repository.get_for_owner(other_owner_id, queued_attempt.attempt_id)
    with pytest.raises(AttemptNotFound):
        attempt_repository.request_cancel(other_owner_id, queued_attempt.turn_id)
    from app.agent_brain.conversation_repository import ConversationRepositoryNotFound

    with attempt_repository.transaction() as connection:
        conversation_id = connection.execute(
            "select conversation_id from platform_control.conversation_turns where turn_id=%s",
            (queued_attempt.turn_id,),
        ).fetchone()["conversation_id"]
    with pytest.raises(ConversationRepositoryNotFound):
        ConversationCommandService(repository, v2_enabled=False).append_turn(
            other_owner_id,
            conversation_id,
            uuid4(),
            "unauthorized turn",
        )
    assert (
        attempt_repository.get_for_owner(
            queued_attempt.owner_id, queued_attempt.attempt_id
        )
        == queued_attempt
    )


def test_internal_create_cannot_override_execution_owner(
    attempt_repository, repository, conversation_database
):
    _, owner_id, _ = conversation_database
    turn = repository.start(owner_id, uuid4(), "legacy turn").turn
    with pytest.raises(AttemptNotFound):
        attempt_repository.create_queued(turn.turn_id, "worker_direct")
    legacy = attempt_repository.create_queued(turn.turn_id, "legacy_api_v1")
    assert legacy.executor_id is None
    assert legacy.lease_epoch == 0
    assert legacy.lease_expires_at is None
    assert attempt_repository.claim_due(uuid4(), 60) is None


def test_create_joins_callers_transaction_and_attempt_numbers_are_monotonic(
    attempt_repository, repository, conversation_database
):
    _, owner_id, _ = conversation_database
    turn = repository.start(owner_id, uuid4(), "attempt numbering").turn
    with (
        pytest.raises(RuntimeError, match="abort creation"),
        attempt_repository.transaction() as connection,
    ):
        staged = attempt_repository.create_queued(
            turn.turn_id, "legacy_api_v1", connection=connection
        )
        raise RuntimeError("abort creation")
    with pytest.raises(AttemptNotFound):
        attempt_repository.get_for_owner(owner_id, staged.attempt_id)
    first = attempt_repository.create_queued(turn.turn_id, "legacy_api_v1")
    assert first.attempt_no == 1
    lease = attempt_repository.claim_due(uuid4(), 60, executor_kind="legacy_api_v1")
    with attempt_repository.transaction() as connection:
        attempt_repository.record_terminal(
            lease, TerminalEvidence("failed"), connection=connection
        )
    second = attempt_repository.create_queued(turn.turn_id, "legacy_api_v1")
    assert second.attempt_no == 2


def test_draft_is_not_a_numbered_migration():
    migrations = list(load_numbered_migrations(DRAFT.parent.parent))
    assert migrations
    assert all(
        "create table platform_control.turn_attempts" not in migration.sql
        for migration in migrations
    )
    assert DRAFT.read_text() not in [migration.sql for migration in migrations]


def test_additive_defaults_and_database_constraints(
    attempt_repository, repository, conversation_database
):
    environment, owner_id, _ = conversation_database
    created = repository.start(owner_id, uuid4(), "legacy compatibility")
    with psycopg.connect(environment["admin"]) as connection:
        assert connection.execute(
            "select execution_owner,route_epoch,snapshot_version from platform_control.conversations where conversation_id=%s",
            (created.conversation.conversation_id,),
        ).fetchone() == ("legacy_api_v1", 0, 0)
        assert connection.execute(
            "select count(*) from platform_control.schema_migrations"
        ).fetchone()[0] == len(list(load_numbered_migrations(DRAFT.parent.parent)))
    attempt = attempt_repository.create_queued(created.turn.turn_id, "legacy_api_v1")
    for change in [
        "status='unknown'",
        "attempt_no=0",
        "lease_epoch=-1",
        "status='running'",
        "status='completed'",
    ]:
        with (
            pytest.raises(psycopg.errors.CheckViolation),
            psycopg.connect(environment["admin"]) as connection,
        ):
            connection.execute(
                f"update platform_control.turn_attempts set {change} where attempt_id=%s",
                (attempt.attempt_id,),
            )
    with (
        pytest.raises(psycopg.errors.UniqueViolation),
        psycopg.connect(environment["admin"]) as connection,
    ):
        connection.execute(
            "insert into platform_control.turn_attempts (attempt_id,turn_id,attempt_no,executor_kind,status) "
            "values (%s,%s,2,'legacy_api_v1','queued')",
            (uuid4(), created.turn.turn_id),
        )
    with psycopg.connect(environment["admin"]) as connection:
        assert connection.execute(
            "select has_table_privilege('platform_control_app','platform_control.turn_attempts','SELECT,INSERT,UPDATE')"
        ).fetchone() == (True,)
        assert connection.execute(
            "select has_table_privilege('platform_control_app','platform_control.turn_attempts','DELETE')"
        ).fetchone() == (False,)
        assert connection.execute(
            "select has_table_privilege('platform_control_maintenance','platform_control.turn_attempts','SELECT')"
        ).fetchone() == (True,)
        assert connection.execute(
            "select has_table_privilege('platform_directory_worker','platform_control.turn_attempts','SELECT')"
        ).fetchone() == (False,)


@pytest.mark.parametrize(
    "status,message_id",
    [("running", None), ("completed", None), ("cancelled", uuid4())],
)
def test_terminal_evidence_rejects_nonterminal_or_inconsistent_result(
    status, message_id
):
    with pytest.raises(ValueError, match="terminal"):
        TerminalEvidence(status, message_id)


def test_claim_requires_process_uuid(attempt_repository, queued_attempt):
    with pytest.raises(ValueError, match="UUID"):
        attempt_repository.claim_due("process-name", 60)


def test_cannot_create_an_attempt_for_completed_turn(
    attempt_repository, repository, conversation_database
):
    environment, owner_id, _ = conversation_database
    turn = repository.start(owner_id, uuid4(), "already finished").turn
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_control.conversation_turns set status='completed' where turn_id=%s",
            (turn.turn_id,),
        )
    with pytest.raises(AttemptNotFound):
        attempt_repository.create_queued(turn.turn_id, "legacy_api_v1")


def test_lease_expiring_while_waiting_for_row_lock_cannot_finalize(
    attempt_repository, queued_attempt, conversation_database
):
    lease = attempt_repository.claim_due(uuid4(), 60)
    environment, _, _ = conversation_database
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_control.turn_attempts set lease_expires_at=clock_timestamp()+interval '0.4 second' where attempt_id=%s",
            (lease.attempt_id,),
        )
    started = Event()

    def finalize():
        with attempt_repository.transaction() as connection:
            started.set()
            try:
                attempt_repository.record_terminal(
                    lease, TerminalEvidence("failed"), connection=connection
                )
                return "accepted"
            except LeaseRejected:
                return "rejected"

    with ThreadPoolExecutor(max_workers=1) as pool:
        with psycopg.connect(environment["admin"]) as blocker:
            blocker.execute(
                "select 1 from platform_control.turn_attempts where attempt_id=%s for update",
                (lease.attempt_id,),
            )
            future = pool.submit(finalize)
            assert started.wait(timeout=5)
            blocker.execute("select pg_sleep(0.6)")
        assert future.result(timeout=5) == "rejected"
    assert (
        attempt_repository.get_for_owner(
            queued_attempt.owner_id, lease.attempt_id
        ).status
        == "running"
    )


@pytest.mark.parametrize("winner", ["completed", "cancelled"])
def test_first_persisted_terminal_wins_in_either_order(
    attempt_repository, queued_attempt, winner
):
    lease = attempt_repository.claim_due(uuid4(), 60)
    message_id = uuid4()
    with attempt_repository.transaction() as connection:
        if winner == "completed":
            _publish_result(connection, queued_attempt, message_id)
        attempt_repository.record_terminal(
            lease,
            TerminalEvidence(winner, message_id if winner == "completed" else None),
            connection=connection,
        )
    with pytest.raises(LeaseRejected), attempt_repository.transaction() as connection:
        loser = "cancelled" if winner == "completed" else "completed"
        if loser == "completed":
            _publish_result(connection, queued_attempt, message_id)
        attempt_repository.record_terminal(
            lease,
            TerminalEvidence(loser, message_id if loser == "completed" else None),
            connection=connection,
        )
    assert (
        attempt_repository.get_for_owner(
            queued_attempt.owner_id, lease.attempt_id
        ).status
        == winner
    )
    with attempt_repository.transaction() as connection:
        assert (
            attempt_repository.record_terminal(
                lease,
                TerminalEvidence(winner, message_id if winner == "completed" else None),
                connection=connection,
            ).applied
            is False
        )


def test_concurrent_create_keeps_one_live_slot(
    attempt_repository, repository, conversation_database
):
    _, owner_id, _ = conversation_database
    turn = repository.start(owner_id, uuid4(), "concurrent attempt creation").turn
    gate = Barrier(2)

    def create(_):
        gate.wait(timeout=5)
        try:
            return attempt_repository.create_queued(turn.turn_id, "legacy_api_v1")
        except ActiveAttemptConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        attempts = [attempt for attempt in pool.map(create, range(2)) if attempt]
    assert len(attempts) == 1
    assert attempts[0].attempt_no == 1


def test_database_rejects_orphan_attempt_and_duplicate_transport(
    attempt_repository, repository, conversation_database
):
    environment, owner_id, _ = conversation_database
    with (
        pytest.raises(psycopg.errors.ForeignKeyViolation),
        psycopg.connect(environment["admin"]) as connection,
    ):
        connection.execute(
            "insert into platform_control.turn_attempts(attempt_id,turn_id,attempt_no,executor_kind,status) "
            "values (%s,%s,1,'legacy_api_v1','queued')",
            (uuid4(), uuid4()),
        )
    turns = [
        repository.start(owner_id, uuid4(), text).turn
        for text in ("first transport", "second transport")
    ]
    attempts = [
        attempt_repository.create_queued(turn.turn_id, "legacy_api_v1")
        for turn in turns
    ]
    run_id = uuid4()
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_control.turn_attempts set transport_run_id=%s where attempt_id=%s",
            (run_id, attempts[0].attempt_id),
        )
    with (
        pytest.raises(psycopg.errors.UniqueViolation),
        psycopg.connect(environment["admin"]) as connection,
    ):
        connection.execute(
            "update platform_control.turn_attempts set transport_run_id=%s where attempt_id=%s",
            (run_id, attempts[1].attempt_id),
        )
