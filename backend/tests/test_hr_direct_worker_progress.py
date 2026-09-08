"""Real ledger regressions for scheduler liveness; no model/result stubs."""

# ruff: noqa: PLC0414
from concurrent.futures import ThreadPoolExecutor
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
