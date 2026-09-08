# ruff: noqa: PLC0414
import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from threading import Event
from time import monotonic

import psycopg
import pytest
from test_execution_acceptance_v5 import response
from test_execution_transport_v5 import (
    PREFIX,
    post,
)
from test_execution_transport_v5 import (
    attempt_repository as attempt_repository,
)
from test_execution_transport_v5 import (
    bindings as bindings,
)
from test_execution_transport_v5 import (
    control_database as control_database,
)
from test_execution_transport_v5 import (
    conversation_database as conversation_database,
)
from test_execution_transport_v5 import (
    direct_database as direct_database,
)
from test_execution_transport_v5 import (
    prepared as prepared,
)
from test_execution_transport_v5 import (
    repository as repository,
)
from test_execution_transport_v5 import (
    signed_api as signed_api,
)
from test_execution_transport_v5 import (
    transport_worker as transport_worker,
)
from test_execution_transport_v5 import (
    worker_conversation as worker_conversation,
)
from test_execution_transport_v5 import (
    worker_turn as worker_turn,
)
from test_execution_worker_v5_receiver import event

from app.agent_brain.turn_attempts import LeaseRejected
from app.execution_relay.contracts_v5 import parse_v5_command, parse_v5_event

pytestmark = pytest.mark.postgres


@pytest.fixture()
def result_source(signed_api, prepared, bindings, attempt_repository, transport_worker):
    client, signer, _ = signed_api
    lease, binding = prepared
    with attempt_repository.transaction() as connection:
        bindings.authorize_transport(
            lease, transport_worker, "http://127.0.0.1:19191", connection=connection
        )
    command = post(client, signer, PREFIX + "/handoff", b"{}").json()["command"]
    assert (
        post(
            client,
            signer,
            f"{PREFIX}/runs/{binding.run_id}/acceptance",
            json.dumps(response(parse_v5_command(command))).encode(),
        ).status_code
        == 200
    )
    raw = event(command, kind="result")
    raw["payload"]["publicAnswerMarkdown"] = "你好。" * 10923
    raw["payload"]["executionRecovery"].update(
        executorStopped=False, executorStopProofRef=None
    )
    assert (
        post(
            client,
            signer,
            f"{PREFIX}/runs/{binding.run_id}/events",
            json.dumps(raw).encode(),
        ).status_code
        == 200
    )
    return lease, binding, raw


@pytest.fixture()
def projector(bindings, attempt_repository, direct_database):
    from app.agent_brain.turn_result_projection import TurnResultProjector

    environment, _, _ = direct_database
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute("set local role platform_control_owner")
        connection.execute(
            (
                Path(__file__).parents[1]
                / "control_migrations/pending/hr_web_result_recovery.sql"
            ).read_text()
        )
    return TurnResultProjector(attempt_repository, bindings)


def test_actual_signed_source_publishes_long_answer_but_holds_unstopped_attempt(
    result_source,
    projector,
    repository,
    worker_turn,
    attempt_repository,
):
    lease, _, raw = result_source
    parsed = parse_v5_event(raw)
    with attempt_repository.transaction() as connection:
        initial_version = connection.execute(
            "select snapshot_version from platform_control.conversations where conversation_id=%s",
            (worker_turn.conversation.conversation_id,),
        ).fetchone()["snapshot_version"]
    message_id = projector.commit(lease, parsed)
    messages = repository.messages_after(
        worker_turn.conversation.owner_internal_user_id,
        worker_turn.conversation.conversation_id,
    )
    answers = [m for m in messages if m.role == "assistant"]
    assert len(answers) == 1
    assert answers[0].message_id == message_id
    assert answers[0].content == raw["payload"]["publicAnswerMarkdown"]
    assert projector.commit(lease, parsed) == message_id
    attempt = attempt_repository.get_for_owner(
        worker_turn.conversation.owner_internal_user_id, lease.attempt_id
    )
    assert attempt.status == "reconciling" and attempt.result_message_id is None
    with attempt_repository.transaction() as connection:
        row = connection.execute(
            "select t.status,c.snapshot_version,b.terminal_source_seq,b.published_message_id from platform_control.conversation_turns t join platform_control.conversations c using(conversation_id) join platform_control.direct_command_bindings b using(conversation_id) where t.turn_id=%s",
            (worker_turn.turn.turn_id,),
        ).fetchone()
    assert row == {
        "status": "completed",
        "snapshot_version": initial_version + 1,
        "terminal_source_seq": 1,
        "published_message_id": message_id,
    }


def test_public_assertion_cannot_replace_persisted_source(result_source, projector):
    lease, _, raw = result_source
    changed = deepcopy(raw)
    changed["payload"]["publicAnswerMarkdown"] = "Not the authenticated source"
    with pytest.raises(ValueError, match="source"):
        projector.commit(lease, parse_v5_event(changed))


def test_old_writer_cannot_publish_source(result_source, projector):
    lease, _, raw = result_source
    with pytest.raises(LeaseRejected):
        projector.commit(
            replace(lease, lease_epoch=lease.lease_epoch + 1), parse_v5_event(raw)
        )


def test_caller_transaction_rollback_includes_text_turn_version_and_event(
    result_source, projector, attempt_repository, repository, worker_turn
):
    lease, _, raw = result_source
    with pytest.raises(RuntimeError, match="abort"), attempt_repository.transaction() as connection:
        renewed = attempt_repository.renew(lease, 60, connection=connection)
        projector.commit_locked(connection, renewed, parse_v5_event(raw))
        raise RuntimeError("abort")
    assert not [
        m
        for m in repository.messages_after(
            worker_turn.conversation.owner_internal_user_id,
            worker_turn.conversation.conversation_id,
        )
        if m.role == "assistant"
    ]


def test_unknown_stop_cannot_publish_after_compatibility_lock_outlives_lease(
    result_source, projector, direct_database, worker_turn
):
    environment, _, _ = direct_database
    lease, _, raw = result_source
    with psycopg.connect(environment["admin"]) as setup:
        setup.execute(
            "update platform_control.turn_attempts set lease_expires_at=clock_timestamp()+interval '2 seconds' where attempt_id=%s",
            (lease.attempt_id,),
        )
    with (
        psycopg.connect(environment["admin"]) as blocker,
        psycopg.connect(environment["admin"], autocommit=True) as observer,
    ):
        blocker.execute(
            "select 1 from platform_control.missions where mission_id=%s for update",
            (worker_turn.turn.mission_id,),
        )
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(projector.commit, lease, parse_v5_event(raw))
            try:
                deadline = monotonic() + 5
                blocked = False
                while monotonic() < deadline and not pending.done():
                    blocked = (
                        observer.execute(
                            "select 1 from pg_stat_activity where datname=current_database() and %s=any(pg_blocking_pids(pid))",
                            (blocker.info.backend_pid,),
                        ).fetchone()
                        is not None
                    )
                    if blocked:
                        break
                    Event().wait(0.02)
                assert blocked
                expired = False
                while monotonic() < deadline:
                    expired = observer.execute(
                        "select lease_expires_at<=clock_timestamp() from platform_control.turn_attempts where attempt_id=%s",
                        (lease.attempt_id,),
                    ).fetchone()[0]
                    if expired:
                        break
                    Event().wait(0.02)
                assert expired
            finally:
                blocker.rollback()
            with pytest.raises(LeaseRejected):
                pending.result(timeout=5)
        assert (
            observer.execute(
                "select count(*) from platform_control.conversation_messages where turn_id=%s and role='assistant'",
                (worker_turn.turn.turn_id,),
            ).fetchone()[0]
            == 0
        )
