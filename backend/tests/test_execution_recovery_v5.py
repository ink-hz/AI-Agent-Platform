# ruff: noqa: PLC0414
import json
from uuid import uuid4

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
from test_turn_result_projection import projector as projector

from app.execution_relay.contracts_v5 import parse_v5_command

pytestmark = pytest.mark.postgres


@pytest.fixture()
def lost_ack(
    signed_api,
    prepared,
    bindings,
    attempt_repository,
    transport_worker,
    worker_turn,
    projector,
    direct_database,
):
    client, signer, _ = signed_api
    lease, binding = prepared
    with attempt_repository.transaction() as connection:
        bindings.authorize_transport(
            lease, transport_worker, "http://127.0.0.1:19191", connection=connection
        )
    handoff = post(client, signer, PREFIX + "/handoff", b"{}").json()
    # Actual dispatch was offered. No acceptance is inserted; report must recover it.
    attempt_repository.request_cancel(
        worker_turn.conversation.owner_internal_user_id, worker_turn.turn.turn_id
    )
    environment, _, _ = direct_database
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_control.turn_attempts set lease_expires_at=clock_timestamp()-interval '1 second' where attempt_id=%s",
            (lease.attempt_id,),
        )
    current = attempt_repository.claim_due(uuid4(), 60)
    assert (
        current.status == "reconciling" and current.lease_epoch == lease.lease_epoch + 1
    )
    return lease, current, binding, handoff


def observation(command, missing=False):
    return {
        "version": "hr_bound_recovery_v1",
        "commandId": command["commandId"],
        "runId": command["runId"],
        "commandHash": command["commandHash"],
        "missing": missing,
        "acceptance": None if missing else response(parse_v5_command(command)),
        "recovery": None
        if missing
        else {
            "evidenceComplete": False,
            "toolEffect": "unknown",
            "hasOutput": False,
            "executorStopped": False,
            "executorStopProofRef": None,
            "replayUsed": False,
        },
    }


def test_signed_recovery_uses_current_writer_but_original_transport_without_reoffering(
    signed_api, lost_ack, attempt_repository
):
    client, signer, _ = signed_api
    old, current, binding, original = lost_ack
    work = post(client, signer, PREFIX + "/recovery", b"{}")
    assert work.status_code == 200
    item = work.json()
    assert item["leaseEpoch"] == current.lease_epoch
    assert item["command"] == original["command"]
    assert item["stop"] is True
    path = f"{PREFIX}/runs/{binding.run_id}/recovery"
    body = {
        "executorId": str(current.executor_id),
        "leaseEpoch": current.lease_epoch,
        "observation": observation(item["command"]),
    }
    stale = {**body, "executorId": str(old.executor_id), "leaseEpoch": old.lease_epoch}
    assert post(client, signer, path, json.dumps(stale).encode()).status_code == 409
    assert post(client, signer, path, json.dumps(body).encode()).status_code == 200
    assert post(client, signer, PREFIX + "/handoff", b"{}").status_code == 204
    with attempt_repository.transaction() as connection:
        row = connection.execute(
            "select launch_lease_epoch,retired_unsent_at from platform_control.direct_command_bindings where attempt_id=%s",
            (old.attempt_id,),
        ).fetchone()
    assert row == {"launch_lease_epoch": old.lease_epoch, "retired_unsent_at": None}


def test_absence_keeps_unknown_dispatch_and_same_conversation_hold(
    signed_api, lost_ack, attempt_repository
):
    client, signer, _ = signed_api
    _, current, binding, original = lost_ack
    body = {
        "executorId": str(current.executor_id),
        "leaseEpoch": current.lease_epoch,
        "observation": observation(original["command"], missing=True),
    }
    assert (
        post(
            client,
            signer,
            f"{PREFIX}/runs/{binding.run_id}/recovery",
            json.dumps(body).encode(),
        ).status_code
        == 200
    )
    with attempt_repository.transaction() as connection:
        row = connection.execute(
            "select a.status,b.launch_lease_epoch,b.retired_unsent_at,b.executor_stop_proof_ref from platform_control.turn_attempts a join platform_control.direct_command_bindings b using(attempt_id) where attempt_id=%s",
            (current.attempt_id,),
        ).fetchone()
    assert row == {
        "status": "reconciling",
        "launch_lease_epoch": None,
        "retired_unsent_at": None,
        "executor_stop_proof_ref": None,
    }
