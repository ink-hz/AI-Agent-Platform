"""Real ledger regressions for scheduler liveness; no model/result stubs."""

# ruff: noqa: PLC0414
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Event
from time import monotonic
from uuid import uuid4

import psycopg
import pytest
from test_hr_direct_worker import (
    attempt_repository as attempt_repository,
)
from test_hr_direct_worker import (
    control_database as control_database,
)
from test_hr_direct_worker import (
    conversation_database as conversation_database,
)
from test_hr_direct_worker import (
    direct_database as direct_database,
)
from test_hr_direct_worker import (
    repository as repository,
)
from test_hr_direct_worker import (
    worker_conversation as worker_conversation,
)
from test_hr_direct_worker import (
    worker_turn as worker_turn,
)

from app.agent_brain.conversation_context import ConversationContextBuilder
from app.agent_brain.direct_command_binding import DirectCommandBindingRepository
from app.agent_brain.direct_mission_adapter import DirectMissionAdapter
from app.agent_brain.direct_worker import DirectWorker
from app.agent_brain.turn_result_projection import TurnResultProjector
from app.execution_relay.repository import ExecutionRelayRepository
from tests.helpers.hr_web_loop import WebLoop

pytestmark = pytest.mark.postgres


@pytest.fixture()
def scheduler(direct_database, repository, attempt_repository, worker_turn):
    environment, owner, _ = direct_database
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute("set local role platform_control_owner")
        connection.execute(
            (
                Path(__file__).parents[1]
                / "control_migrations/pending/hr_web_result_recovery.sql"
            ).read_text()
        )
    bindings = DirectCommandBindingRepository(
        ExecutionRelayRepository(
            environment["urls"]["platform_control_app"],
            content_codec=repository.content_codec,
        )
    )
    adapter = DirectMissionAdapter(
        attempt_repository,
        bindings,
        ConversationContextBuilder(repository),
        TurnResultProjector(attempt_repository, bindings),
    )
    identity = WebLoop(environment, owner, worker_turn.conversation.conversation_id)
    worker = DirectWorker(attempt_repository, adapter, lease_seconds=10, limit=2)
    stopping = Event()
    try:
        yield worker, adapter, stopping
    finally:
        stopping.set()
        identity.close()


def wait_until(predicate, timeout=4):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if predicate():
            return
        Event().wait(0.02)
    assert predicate(), "scheduler did not make bounded progress"


def state(environment, turn_id):
    with psycopg.connect(environment["admin"]) as connection:
        return connection.execute(
            "select a.status,a.transport_run_id,a.lease_expires_at>clock_timestamp() "
            "from platform_control.turn_attempts a where turn_id=%s",
            (turn_id,),
        ).fetchone()


def test_context_version_conflict_finishes_without_ever_dispatching(
    scheduler,
    direct_database,
    worker_turn,
):
    worker, adapter, stopping = scheduler
    environment, _, _ = direct_database
    original = adapter.context_builder.build_direct

    def changed_context(conversation_id, turn_id):
        context = original(conversation_id, turn_id)
        with psycopg.connect(environment["admin"]) as connection:
            connection.execute(
                "update platform_control.conversations set updated_at=clock_timestamp() where conversation_id=%s",
                (conversation_id,),
            )
        return context

    adapter.context_builder.build_direct = changed_context
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(worker.run, stopping)
        try:
            wait_until(
                lambda: state(environment, worker_turn.turn.turn_id)[0] == "failed"
            )
            assert state(environment, worker_turn.turn.turn_id)[1] is None
        finally:
            stopping.set()
            running.result(timeout=5)


def test_snapshot_version_tracks_claim_and_manifest_but_not_lease_renewal(
    scheduler,
    direct_database,
    repository,
    worker_turn,
    attempt_repository,
):
    from app.agent_brain.turn_snapshot import TurnSnapshotReader

    _, adapter, _ = scheduler
    environment, owner, _ = direct_database
    reader = TurnSnapshotReader(repository)
    before = reader.get(owner, worker_turn.conversation.conversation_id)
    lease = attempt_repository.claim_due(uuid4(), 60)
    claimed = reader.get(owner, worker_turn.conversation.conversation_id)
    assert claimed["read_version"] > before["read_version"]
    adapter.prepare(lease)
    prepared = reader.get(owner, worker_turn.conversation.conversation_id)
    assert prepared["context_manifest_ref"] != claimed["context_manifest_ref"]
    assert prepared["read_version"] > claimed["read_version"]
    attempt_repository.renew(lease, 60)
    assert reader.get(owner, worker_turn.conversation.conversation_id) == prepared
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_control.turn_attempts set lease_expires_at=clock_timestamp()-interval '1 second' where attempt_id=%s",
            (lease.attempt_id,),
        )
    current = attempt_repository.claim_due(uuid4(), 60)
    reclaimed = reader.get(owner, worker_turn.conversation.conversation_id)
    assert current.lease_epoch == lease.lease_epoch + 1
    assert reclaimed["attempt"]["status"] == "reconciling"
    assert reclaimed["read_version"] > prepared["read_version"]


def test_slow_context_does_not_block_other_cancel_and_lease_renewal(
    scheduler,
    direct_database,
    worker_turn,
    repository,
    attempt_repository,
):
    worker, adapter, stopping = scheduler
    environment, owner, _ = direct_database
    entered, release = Event(), Event()
    second = repository.ensure_direct_conversation_shell(
        owner, uuid4(), direct_agent_id="hr-bot", title="Owned slow context"
    )
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_control.conversations set execution_owner='worker_direct' where conversation_id=%s",
            (second.conversation_id,),
        )
    original = adapter.context_builder.build_direct

    def delayed_context(conversation_id, turn_id):
        if conversation_id == second.conversation_id:
            entered.set()
            assert release.wait(6), "owned blocking provider was not released"
        return original(conversation_id, turn_id)

    adapter.context_builder.build_direct = delayed_context
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(worker.run, stopping)
        try:
            wait_until(
                lambda: state(environment, worker_turn.turn.turn_id)[1] is not None
            )
            queued = repository.append_turn(
                owner, second.conversation_id, uuid4(), "Owned second question"
            )
            assert entered.wait(3)
            attempt_repository.request_cancel(owner, worker_turn.turn.turn_id)
            wait_until(
                lambda: state(environment, worker_turn.turn.turn_id)[0] == "cancelled",
                timeout=2,
            )
            assert state(environment, queued.turn.turn_id)[2] is True
        finally:
            release.set()
            stopping.set()
            running.result(timeout=5)


def test_corrupt_history_isolates_one_preparation_without_stopping_worker(
    scheduler, direct_database, worker_turn, repository, attempt_repository,
):
    worker, _, stopping = scheduler
    environment, owner, _ = direct_database
    # Real malformed ciphertext in the owned input, not a fake Result or fault
    # raised by an orchestration stub.
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute("update platform_control.conversation_messages set content_ciphertext=%s where message_id=%s",
            (b"invalid-owned-ciphertext" * 3, worker_turn.turn.user_message_id))
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(worker.run, stopping)
        try:
            wait_until(lambda: running.done() or state(environment, worker_turn.turn.turn_id)[0] == "failed")
            assert not running.done(), "one unreadable history stopped the whole Worker"
            assert state(environment, worker_turn.turn.turn_id)[1] is None
            second = repository.ensure_direct_conversation_shell(owner, uuid4(), direct_agent_id="hr-bot", title="Owned independent question")
            with psycopg.connect(environment["admin"]) as connection:
                connection.execute("update platform_control.conversations set execution_owner='worker_direct' where conversation_id=%s", (second.conversation_id,))
            queued = repository.append_turn(owner, second.conversation_id, uuid4(), "取消这项独立任务")
            attempt_repository.request_cancel(owner, queued.turn.turn_id)
            wait_until(lambda: state(environment, queued.turn.turn_id)[0] == "cancelled")
        finally:
            stopping.set()
            # Preserve the primary liveness assertion if the candidate died.
            if not running.done():
                running.result(timeout=5)


def test_transient_context_read_retries_before_one_preparation(
    scheduler, direct_database, worker_turn,
):
    worker, adapter, stopping = scheduler
    environment, _, _ = direct_database
    original = adapter.context_builder._load
    reads = 0

    def flaky_load(*args, **kwargs):
        nonlocal reads
        reads += 1
        if reads < 3:
            raise psycopg.OperationalError("owned context read unavailable")
        return original(*args, **kwargs)

    adapter.context_builder._load = flaky_load
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(worker.run, stopping)
        try:
            wait_until(lambda: running.done() or state(environment, worker_turn.turn.turn_id)[1] is not None)
            assert not running.done(), "transient context read killed the Worker"
            assert reads == 3
            with psycopg.connect(environment["admin"]) as connection:
                assert connection.execute(
                    "select count(*) from platform_control.direct_command_bindings binding "
                    "join platform_control.turn_attempts attempt using(attempt_id) where attempt.turn_id=%s",
                    (worker_turn.turn.turn_id,),
                ).fetchone()[0] == 1
        finally:
            stopping.set()
            if not running.done():
                running.result(timeout=5)


def test_context_read_retry_exhaustion_does_not_block_other_cancel(
    scheduler, direct_database, worker_turn, repository, attempt_repository,
):
    worker, adapter, stopping = scheduler
    environment, owner, _ = direct_database
    original = adapter.context_builder._load
    second = repository.ensure_direct_conversation_shell(
        owner, uuid4(), direct_agent_id="hr-bot", title="Owned unavailable context"
    )
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_control.conversations set execution_owner='worker_direct' where conversation_id=%s",
            (second.conversation_id,),
        )
    reads = 0
    entered = Event()

    def unavailable_load(conversation_id, *args, **kwargs):
        nonlocal reads
        if conversation_id == second.conversation_id:
            reads += 1
            entered.set()
            raise psycopg.OperationalError("owned context read unavailable")
        return original(conversation_id, *args, **kwargs)

    adapter.context_builder._load = unavailable_load
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(worker.run, stopping)
        try:
            wait_until(lambda: state(environment, worker_turn.turn.turn_id)[1] is not None)
            queued = repository.append_turn(owner, second.conversation_id, uuid4(), "Owned context retry")
            assert entered.wait(3)
            attempt_repository.request_cancel(owner, worker_turn.turn.turn_id)
            wait_until(lambda: running.done() or state(environment, worker_turn.turn.turn_id)[0] == "cancelled")
            assert not running.done(), "context read fault blocked unrelated cancellation"
            wait_until(lambda: state(environment, queued.turn.turn_id)[0] == "failed")
            assert reads == 3
            assert state(environment, queued.turn.turn_id)[1] is None
            assert len(worker._preparations) <= worker.limit
        finally:
            stopping.set()
            if not running.done():
                running.result(timeout=5)


def test_failed_preparation_retries_only_terminal_write_after_storage_fault(
    scheduler, direct_database, worker_turn,
):
    worker, adapter, stopping = scheduler
    environment, _, _ = direct_database
    reads = writes = 0
    original = adapter.failed_prepare

    def invalid_load(*_args, **_kwargs):
        nonlocal reads
        reads += 1
        raise ValueError("owned invalid context")

    def unavailable_terminal(*args):
        nonlocal writes
        writes += 1
        if writes < 3:
            raise psycopg.OperationalError("owned terminal storage unavailable")
        return original(*args)

    adapter.context_builder._load = invalid_load
    adapter.failed_prepare = unavailable_terminal
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(worker.run, stopping)
        try:
            wait_until(lambda: running.done() or state(environment, worker_turn.turn.turn_id)[0] == "failed")
            assert not running.done(), "failed terminal write poisoned the Worker"
            assert reads == 1 and writes == 3
            assert state(environment, worker_turn.turn.turn_id)[1] is None
        finally:
            stopping.set()
            if not running.done():
                running.result(timeout=5)


def test_scheduler_survives_storage_outage_without_fabricating_a_terminal(
    scheduler, direct_database, worker_turn, attempt_repository,
):
    worker, _, stopping = scheduler
    environment, _, _ = direct_database
    original = attempt_repository.transaction
    calls = 0

    @contextmanager
    def unavailable_transaction():
        nonlocal calls
        calls += 1
        if calls <= 3:
            assert state(environment, worker_turn.turn.turn_id)[:2] == ("queued", None)
            raise psycopg.OperationalError("owned scheduler storage unavailable")
        with original() as connection:
            yield connection

    attempt_repository.transaction = unavailable_transaction
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(worker.run, stopping)
        try:
            wait_until(lambda: running.done() or state(environment, worker_turn.turn.turn_id)[1] is not None)
            assert not running.done(), "storage outage stopped the coordinator"
            assert calls > 3
        finally:
            stopping.set()
            if not running.done():
                running.result(timeout=5)


def test_uncertain_preparation_write_preserves_offered_run_and_cancel_hold(
    scheduler, direct_database, worker_turn, attempt_repository,
):
    worker, adapter, stopping = scheduler
    environment, owner, _ = direct_database
    original = adapter.prepare
    offered = Event()
    calls = 0

    def uncertain_prepare(lease):
        nonlocal calls
        calls += 1
        original(lease)
        assert adapter.bindings.handoff(lease.admission["workerId"]) is not None
        offered.set()
        raise psycopg.OperationalError("owned acknowledgement unavailable")

    adapter.prepare = uncertain_prepare
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(worker.run, stopping)
        try:
            assert offered.wait(3)
            run_id = state(environment, worker_turn.turn.turn_id)[1]
            attempt_repository.request_cancel(owner, worker_turn.turn.turn_id)
            wait_until(lambda: running.done() or state(environment, worker_turn.turn.turn_id)[0] == "reconciling")
            assert not running.done()
            assert calls == 1
            assert state(environment, worker_turn.turn.turn_id) == ("reconciling", run_id, True)
        finally:
            stopping.set()
            if not running.done():
                running.result(timeout=5)


def test_lost_claim_response_does_not_renew_an_unprepared_turn_forever(
    scheduler, direct_database, worker_turn, attempt_repository,
):
    worker, _, _ = scheduler
    environment, _, _ = direct_database
    original = attempt_repository.claim_due
    lost = False

    def lost_response(*args):
        nonlocal lost
        lease = original(*args)
        if lease is not None and not lost:
            lost = True
            raise psycopg.OperationalError("owned claim response unavailable")
        return lease

    attempt_repository.claim_due = lost_response
    with pytest.raises(psycopg.OperationalError):
        worker.tick()
    with psycopg.connect(environment["admin"]) as connection:
        expiry = connection.execute(
            "select lease_expires_at from platform_control.turn_attempts where turn_id=%s",
            (worker_turn.turn.turn_id,),
        ).fetchone()[0]
    worker.tick()
    with psycopg.connect(environment["admin"]) as connection:
        assert connection.execute(
            "select lease_expires_at from platform_control.turn_attempts where turn_id=%s",
            (worker_turn.turn.turn_id,),
        ).fetchone()[0] == expiry
        connection.execute(
            "update platform_control.turn_attempts set lease_expires_at=clock_timestamp()-interval '1 second' where turn_id=%s",
            (worker_turn.turn.turn_id,),
        )
    worker.tick()
    assert lost and state(environment, worker_turn.turn.turn_id)[:2] == ("interrupted", None)


def test_expired_owned_lease_does_not_block_its_existing_recovery_at_capacity(
    scheduler, direct_database, worker_turn, attempt_repository,
):
    worker, _, _ = scheduler
    environment, _, _ = direct_database
    worker.limit = 1
    lease = attempt_repository.claim_due(worker.executor_id, 10)
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_control.turn_attempts set lease_expires_at=clock_timestamp()-interval '1 second' where attempt_id=%s",
            (lease.attempt_id,),
        )
    worker.tick()
    assert state(environment, worker_turn.turn.turn_id)[:2] == ("interrupted", None)
