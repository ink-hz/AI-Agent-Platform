from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier, Event
from time import monotonic, sleep
from uuid import uuid4

import psycopg
import pytest
from test_agent_brain_conversation_repository import _codec
from test_execution_acceptance_v5 import response
from test_hr_direct_worker import (  # actual P03 fixture chain
    attempt_repository as attempt_repository,  # noqa: PLC0414
)
from test_hr_direct_worker import (
    control_database as control_database,  # noqa: PLC0414
)
from test_hr_direct_worker import (
    conversation_database as conversation_database,  # noqa: PLC0414
)
from test_hr_direct_worker import (
    direct_database as direct_database,  # noqa: PLC0414
)
from test_hr_direct_worker import (
    repository as repository,  # noqa: PLC0414
)
from test_hr_direct_worker import (
    worker_conversation as worker_conversation,  # noqa: PLC0414
)
from test_hr_direct_worker import (
    worker_turn as worker_turn,  # noqa: PLC0414
)

from app.agent_brain.direct_command_binding import (
    BindingRejected,
    DirectCommandBindingRepository,
    FrozenInput,
)
from app.agent_brain.turn_attempts import LeaseRejected, TerminalEvidence
from app.execution_relay.acceptance_v5 import parse_v5_acceptance
from app.execution_relay.frozen_command_v5 import hydrate_frozen_command
from app.execution_relay.repository import (
    ExecutionRelayConflict,
    ExecutionRelayNotFound,
    ExecutionRelayRepository,
)

pytestmark = pytest.mark.postgres


@pytest.fixture()
def bindings(direct_database):
    environment, _, _ = direct_database
    return DirectCommandBindingRepository(
        ExecutionRelayRepository(
            environment["urls"]["platform_control_app"],
            content_codec=_codec(),
        )
    )


def frozen_input(worker_turn):
    return FrozenInput(
        worker_turn.conversation.owner_internal_user_id,
        worker_turn.conversation.updated_at,
        worker_turn.turn.updated_at,
        worker_turn.conversation.summary_through_seq,
        worker_turn.message.message_id,
        worker_turn.message.seq,
        "P03 synthetic frozen input",
        "a" * 64,
    )


def test_atomic_encrypted_binding_reuses_actual_attempt_without_consuming_sequence(
    bindings,
    worker_turn,
    attempt_repository,
):
    lease = attempt_repository.claim_due(uuid4(), 60)
    assert lease is not None
    with attempt_repository.transaction() as connection:
        result = bindings.prepare(
            lease, frozen_input(worker_turn), connection=connection
        )
        assert result is not None
        assert result.command_seq == 1
        assert result.attempt_id == lease.attempt_id
    with attempt_repository.transaction() as connection:
        duplicate = bindings.prepare(
            lease, frozen_input(worker_turn), connection=connection
        )
        assert duplicate == result
        row = connection.execute(
            "select j.payload_ciphertext,a.transport_run_id,b.launch_lease_epoch "
            "from platform_control.direct_command_bindings b "
            "join platform_control.execution_jobs j using(job_id) "
            "join platform_control.turn_attempts a using(attempt_id) where b.attempt_id=%s",
            (lease.attempt_id,),
        ).fetchone()
        assert row["transport_run_id"] == result.run_id
        assert row["launch_lease_epoch"] is None
        assert b"P03 synthetic frozen input" not in bytes(row["payload_ciphertext"])


@pytest.fixture()
def prepared(bindings, worker_turn, attempt_repository):
    lease = attempt_repository.claim_due(uuid4(), 60)
    with attempt_repository.transaction() as connection:
        binding = bindings.prepare(
            lease, frozen_input(worker_turn), connection=connection
        )
    return lease, binding


def test_only_unoffered_job_can_retire_its_reservation(
    bindings, prepared, attempt_repository
):
    lease, _binding = prepared
    with attempt_repository.transaction() as connection:
        assert bindings.retire_unoffered(lease, connection=connection) is True
    with attempt_repository.transaction() as connection:
        row = connection.execute(
            "select j.status,b.retired_unsent_at from platform_control.direct_command_bindings b "
            "join platform_control.execution_jobs j using(job_id) where attempt_id=%s",
            (lease.attempt_id,),
        ).fetchone()
        assert row["status"] == "cancelled"
        assert row["retired_unsent_at"] is not None


def test_offered_unknown_job_never_releases_sequence(
    bindings, prepared, attempt_repository
):
    lease, _ = prepared
    with attempt_repository.transaction() as connection:
        assert bindings.mark_offered(lease, connection=connection) is True
    with attempt_repository.transaction() as connection:
        assert bindings.retire_unoffered(lease, connection=connection) is False
        assert (
            connection.execute(
                "select retired_unsent_at from platform_control.direct_command_bindings where attempt_id=%s",
                (lease.attempt_id,),
            ).fetchone()["retired_unsent_at"]
            is None
        )


def test_caller_rollback_leaves_no_job_binding_or_run(
    bindings, worker_turn, attempt_repository
):
    lease = attempt_repository.claim_due(uuid4(), 60)
    with (
        pytest.raises(RuntimeError, match="synthetic rollback"),
        attempt_repository.transaction() as connection,
    ):
        bindings.prepare(lease, frozen_input(worker_turn), connection=connection)
        raise RuntimeError("synthetic rollback")
    with attempt_repository.transaction() as connection:
        assert (
            connection.execute(
                "select count(*) as n from platform_control.direct_command_bindings"
            ).fetchone()["n"]
            == 0
        )
        assert (
            connection.execute(
                "select count(*) as n from platform_control.execution_jobs where job_kind='worker_direct_v5'"
            ).fetchone()["n"]
            == 0
        )
        assert (
            connection.execute(
                "select transport_run_id from platform_control.turn_attempts where attempt_id=%s",
                (lease.attempt_id,),
            ).fetchone()["transport_run_id"]
            is None
        )


def test_same_attempt_concurrent_prepare_commits_one_binding(
    bindings, worker_turn, attempt_repository
):
    lease = attempt_repository.claim_due(uuid4(), 60)
    gate = Barrier(2)

    def prepare(_):
        gate.wait(timeout=5)
        with attempt_repository.transaction() as connection:
            return bindings.prepare(
                lease, frozen_input(worker_turn), connection=connection
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(prepare, range(2)))
    assert first == second


@pytest.mark.parametrize(
    "change",
    [
        {"owner_id": uuid4()},
        {"user_message_id": uuid4()},
        {"user_message_seq": 999},
        {"summary_through_seq": 999},
    ],
)
def test_prepare_checks_actual_frozen_identity_and_version(
    bindings, worker_turn, attempt_repository, change
):
    lease = attempt_repository.claim_due(uuid4(), 60)
    with pytest.raises(BindingRejected), attempt_repository.transaction() as connection:
        bindings.prepare(
            lease, replace(frozen_input(worker_turn), **change), connection=connection
        )


def test_same_attempt_rejects_changed_frozen_business_input(
    bindings, prepared, worker_turn, attempt_repository
):
    lease, _ = prepared
    for change in (
        {"prompt": "changed"},
        {"context_hash": "b" * 64},
        {"tool_policy": "default"},
    ):
        with (
            pytest.raises(BindingRejected),
            attempt_repository.transaction() as connection,
        ):
            bindings.prepare(
                lease,
                replace(frozen_input(worker_turn), **change),
                connection=connection,
            )


def test_obsolete_executor_cannot_prepare(bindings, worker_turn, attempt_repository):
    lease = attempt_repository.claim_due(uuid4(), 60)
    with pytest.raises(LeaseRejected), attempt_repository.transaction() as connection:
        bindings.prepare(
            replace(lease, executor_id=uuid4()),
            frozen_input(worker_turn),
            connection=connection,
        )


def trusted_acceptance(binding, lease, *, launch=None):
    """Storage-boundary fixture; actual HTTP acceptance is proved in MetaBot tests."""
    command = hydrate_frozen_command(
        binding.frozen,
        lease_epoch=lease.lease_epoch,
        event_callback_url=f"http://127.0.0.1:9120/callbacks/{binding.run_id}/{'A' * 43}",
        input_attachment_grants=[],
        output_write_grant=None,
    )
    return parse_v5_acceptance(
        response(command, launch=launch or lease.lease_epoch), command
    )


def test_acceptance_records_origin_once_without_adjudicating_attempt(
    bindings, prepared, attempt_repository
):
    lease, binding = prepared
    with attempt_repository.transaction() as connection:
        bindings.mark_offered(lease, connection=connection)
        ack = trusted_acceptance(binding, lease)
        assert bindings.record_acceptance(lease, ack, connection=connection) is True
    with attempt_repository.transaction() as connection:
        assert bindings.record_acceptance(lease, ack, connection=connection) is False
        row = connection.execute(
            "select b.launch_lease_epoch,b.accepted_at,a.status from platform_control.direct_command_bindings b "
            "join platform_control.turn_attempts a using(attempt_id) where attempt_id=%s",
            (lease.attempt_id,),
        ).fetchone()
        assert row["launch_lease_epoch"] == lease.lease_epoch
        assert row["accepted_at"] is not None
        assert row["status"] == "running"


def test_binding_identity_cannot_be_rewritten_even_by_internal_app_role(
    prepared, attempt_repository
):
    lease, _ = prepared
    with (
        pytest.raises(psycopg.errors.CheckViolation),
        attempt_repository.transaction() as connection,
    ):
        connection.execute(
            "update platform_control.direct_command_bindings set command_hash=%s where attempt_id=%s",
            ("b" * 64, lease.attempt_id),
        )


@pytest.mark.parametrize("operation", ["request_cancel", "interrupt", "job_state"])
def test_legacy_cancel_and_timeout_boundaries_cannot_mutate_v5_job(
    bindings,
    prepared,
    attempt_repository,
    direct_database,
    operation,
):
    _, binding = prepared
    environment, _, _ = direct_database
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_control.execution_jobs set created_at=now()-interval '1 hour' where job_id=%s",
            (binding.job_id,),
        )
    if operation == "request_cancel":
        assert bindings.relay.request_cancel(binding.run_id) is False
    else:
        with pytest.raises(ExecutionRelayNotFound):
            getattr(bindings.relay, operation)(binding.run_id)
    with attempt_repository.transaction() as connection:
        row = connection.execute(
            "select status,cancel_requested,terminal_at from platform_control.execution_jobs where job_id=%s",
            (binding.job_id,),
        ).fetchone()
        assert row == {
            "status": "queued",
            "cancel_requested": False,
            "terminal_at": None,
        }


def test_legacy_leasing_heartbeat_and_owned_updates_exclude_v5_before_decryption(
    bindings,
    prepared,
    attempt_repository,
    direct_database,
):
    _, binding = prepared
    environment, _, _ = direct_database
    worker = "relay-acceptance-" + uuid4().hex[:16]
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.execution_workers(worker_id,allowed_agent_ids,status) values(%s,array['hr-bot'],'active')",
            (worker,),
        )
    assert bindings.relay.lease(worker, ("hr-bot",), 60) is None
    with pytest.raises(ExecutionRelayConflict):
        bindings.relay.lease_acceptance(worker, ("hr-bot",), 60, binding.run_id)


def test_legacy_heartbeat_and_owned_updates_cannot_mutate_v5_transport(
    bindings, prepared, attempt_repository, direct_database
):
    _, binding = prepared
    environment, _, _ = direct_database
    worker = "worker-p03b-" + uuid4().hex[:16]
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.execution_workers(worker_id,allowed_agent_ids,status) values(%s,array['hr-bot'],'active')",
            (worker,),
        )
        # Explicit transport-owned fixture, not a claim that the v5 sender is implemented.
        connection.execute(
            "update platform_control.execution_jobs set status='leased',lease_worker_id=%s,lease_expires_at=now()-interval '1 second',cancel_requested=true,stop_requested_status='cancelled' where job_id=%s",
            (worker, binding.job_id),
        )
        before = connection.execute(
            "select lease_expires_at from platform_control.execution_jobs where job_id=%s",
            (binding.job_id,),
        ).fetchone()[0]
    assert bindings.relay.heartbeat(worker) == ()
    with pytest.raises(ExecutionRelayNotFound):
        bindings.relay.mark_dispatched(worker, binding.run_id)
    with attempt_repository.transaction() as connection:
        row = connection.execute(
            "select status,lease_expires_at from platform_control.execution_jobs where job_id=%s",
            (binding.job_id,),
        ).fetchone()
        assert row == {"status": "leased", "lease_expires_at": before}


def next_storage_turn(worker_turn, lease, attempt_repository, repository):
    # No executor is launched in these repository tests. This adjudication only
    # advances the actual Turn fixture to exercise command-number bookkeeping.
    with attempt_repository.transaction() as connection:
        connection.execute(
            "update platform_control.conversation_turns set status='failed' where turn_id=%s",
            (worker_turn.turn.turn_id,),
        )
        attempt_repository.record_terminal(
            lease,
            TerminalEvidence("failed", reason_code="storage_fixture_no_executor"),
            connection=connection,
        )
    return repository.append_turn(
        worker_turn.conversation.owner_internal_user_id,
        worker_turn.conversation.conversation_id,
        uuid4(),
        "Next storage fixture Turn",
    )


@pytest.mark.parametrize("prior", ["retired_unoffered", "accepted", "offered_unknown"])
def test_next_sequence_uses_actual_accepted_reservations_only(
    bindings,
    prepared,
    worker_turn,
    attempt_repository,
    repository,
    prior,
):
    lease, binding = prepared
    with attempt_repository.transaction() as connection:
        if prior == "retired_unoffered":
            assert bindings.retire_unoffered(lease, connection=connection)
        else:
            bindings.mark_offered(lease, connection=connection)
            if prior == "accepted":
                bindings.record_acceptance(
                    lease, trusted_acceptance(binding, lease), connection=connection
                )
    next_turn = next_storage_turn(worker_turn, lease, attempt_repository, repository)
    next_lease = attempt_repository.claim_due(uuid4(), 60)
    if prior == "offered_unknown":
        with (
            pytest.raises(BindingRejected),
            attempt_repository.transaction() as connection,
        ):
            bindings.prepare(next_lease, frozen_input(next_turn), connection=connection)
    else:
        with attempt_repository.transaction() as connection:
            result = bindings.prepare(
                next_lease, frozen_input(next_turn), connection=connection
            )
            assert result.command_seq == (2 if prior == "accepted" else 1)
            assert result.command_id != binding.command_id
            assert result.frozen.document["turnSeq"] == 2


@pytest.mark.parametrize(
    "mutation", ["route", "expiry", "conversation_version", "turn_version", "cancel"]
)
def test_preparation_fences_current_route_lease_and_frozen_versions(
    bindings,
    worker_turn,
    attempt_repository,
    direct_database,
    mutation,
):
    lease = attempt_repository.claim_due(uuid4(), 60)
    environment, _, _ = direct_database
    with psycopg.connect(environment["admin"]) as connection:
        if mutation == "route":
            connection.execute(
                "update platform_control.conversations set route_epoch=route_epoch+1 where conversation_id=%s",
                (worker_turn.conversation.conversation_id,),
            )
        elif mutation == "expiry":
            connection.execute(
                "update platform_control.turn_attempts set lease_expires_at=now()-interval '1 second' where attempt_id=%s",
                (lease.attempt_id,),
            )
        elif mutation == "conversation_version":
            connection.execute(
                "update platform_control.conversations set updated_at=clock_timestamp() where conversation_id=%s",
                (worker_turn.conversation.conversation_id,),
            )
        elif mutation == "turn_version":
            connection.execute(
                "update platform_control.conversation_turns set updated_at=clock_timestamp() where turn_id=%s",
                (worker_turn.turn.turn_id,),
            )
        else:
            connection.execute(
                "update platform_control.turn_attempts set cancel_requested_at=now() where attempt_id=%s",
                (lease.attempt_id,),
            )
    with (
        pytest.raises((LeaseRejected, BindingRejected)),
        attempt_repository.transaction() as connection,
    ):
        bindings.prepare(lease, frozen_input(worker_turn), connection=connection)


def test_recovered_holder_keeps_immutable_origin_and_rejects_wrong_or_stale_ack(
    bindings,
    prepared,
    attempt_repository,
    direct_database,
):
    lease, binding = prepared
    ack = trusted_acceptance(binding, lease)
    with attempt_repository.transaction() as connection:
        bindings.mark_offered(lease, connection=connection)
        bindings.record_acceptance(lease, ack, connection=connection)
    environment, _, _ = direct_database
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_control.turn_attempts set lease_expires_at=now()-interval '1 second' where attempt_id=%s",
            (lease.attempt_id,),
        )
    current = attempt_repository.claim_due(uuid4(), 60)
    assert current.status == "reconciling"
    for invalid in (
        ack,
        replace(
            ack,
            transport_lease_epoch=current.lease_epoch,
            launch_lease_epoch=current.lease_epoch,
        ),
        replace(
            ack, transport_lease_epoch=current.lease_epoch, command_id=str(uuid4())
        ),
    ):
        with (
            pytest.raises(BindingRejected),
            attempt_repository.transaction() as connection,
        ):
            bindings.record_acceptance(current, invalid, connection=connection)
    with attempt_repository.transaction() as connection:
        assert (
            bindings.record_acceptance(
                current,
                replace(ack, transport_lease_epoch=current.lease_epoch),
                connection=connection,
            )
            is False
        )
    with pytest.raises(LeaseRejected), attempt_repository.transaction() as connection:
        bindings.retire_unoffered(lease, connection=connection)


def test_existing_binding_is_read_before_rebuilding_any_external_context(
    bindings, prepared, attempt_repository
):
    lease, binding = prepared
    with attempt_repository.transaction() as connection:
        assert bindings.get_prepared(lease, connection=connection) == binding


def test_failed_business_turn_cannot_prepare_an_initial_command(
    bindings, worker_turn, attempt_repository, direct_database
):
    lease = attempt_repository.claim_due(uuid4(), 60)
    environment, _, _ = direct_database
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_control.conversation_turns set status='failed' where turn_id=%s",
            (worker_turn.turn.turn_id,),
        )
    with pytest.raises(BindingRejected), attempt_repository.transaction() as connection:
        bindings.prepare(lease, frozen_input(worker_turn), connection=connection)


def test_lease_is_rechecked_after_waiting_for_the_final_job_lock(
    bindings,
    prepared,
    attempt_repository,
    direct_database,
):
    lease, binding = prepared
    environment, _, _ = direct_database
    ready = Event()
    backend_pid = []

    def offer():
        with attempt_repository.transaction() as connection:
            backend_pid.append(connection.info.backend_pid)
            ready.set()
            return bindings.mark_offered(lease, connection=connection)

    with psycopg.connect(environment["admin"]) as blocker:
        blocker.execute(
            "update platform_control.turn_attempts set lease_expires_at=clock_timestamp()+interval '1 second' where attempt_id=%s",
            (lease.attempt_id,),
        )
        blocker.commit()
        blocker.execute(
            "select job_id from platform_control.execution_jobs where job_id=%s for update",
            (binding.job_id,),
        )
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(offer)
            assert ready.wait(timeout=3)
            try:
                deadline = monotonic() + 3
                while monotonic() < deadline:
                    blocked = blocker.execute(
                        "select cardinality(pg_blocking_pids(%s))>0", (backend_pid[0],)
                    ).fetchone()[0]
                    if blocked:
                        break
                    sleep(0.01)
                assert blocked
                blocker.execute("select pg_sleep(1.1)")
            finally:
                blocker.commit()
            with pytest.raises(LeaseRejected):
                future.result(timeout=3)
