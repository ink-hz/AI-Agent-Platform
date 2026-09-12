"""Signed recovery: explicit wrong-lane fault fixtures, never fake cutover success."""
import json
import hashlib
from dataclasses import replace
from uuid import uuid4

import pytest
from test_execution_recovery_v5 import observation
from test_execution_transport_v5 import PREFIX, post
from test_hr_cutover_dispatch_review import (
    attempt_repository, authorize, bindings, conversation_database, database,
    direct_database, repository, set_residual_phase, signed_api, signed_http_app,
    transport_worker, worker_conversation, worker_turn,
)

_FIXTURES = (attempt_repository, bindings, conversation_database, database,
             direct_database, repository, signed_api, signed_http_app,
             transport_worker, worker_conversation, worker_turn)
pytestmark = pytest.mark.postgres


@pytest.fixture
def prepared(bindings, worker_turn, attempt_repository):
    from app.hr.turn_scope import load_authorized_turn_scope
    from app.execution_relay.contracts_v5 import canonical_command_bytes
    from test_hr_direct_command_binding import frozen_input

    lease = attempt_repository.claim_due(uuid4(), 60)
    with attempt_repository.transaction() as connection:
        scope = load_authorized_turn_scope(
            worker_turn.conversation.owner_internal_user_id,
            worker_turn.conversation.conversation_id, worker_turn.turn.turn_id,
            connection=connection,
        )
        # Synthetic frozen intent, actual authorized persisted scope and v6 grant path.
        metadata = {
            "scope": scope.scope.model_dump(mode="json", by_alias=True),
            "rolePackage": lease.admission["service"]["rolePackage"],
            "methodSelection": None,
            "toolCapabilities": ["hr.read_context", "hr.submit_result", "hr.confirm_standard"],
        }
        base = frozen_input(worker_turn)
        context_hash = hashlib.sha256(canonical_command_bytes({
            "prompt": base.prompt,
            **{key: metadata[key] for key in ("scope", "rolePackage", "methodSelection")},
        })).hexdigest()
        frozen = replace(base, hr_v6=metadata, tool_policy="default", context_hash=context_hash)
        binding = bindings.prepare(lease, frozen, connection=connection)
    assert binding.frozen.document["contractVersion"] == "core_chat_collaboration_v6"
    return lease, binding


@pytest.fixture
def recovering(database, signed_api, bindings, prepared, attempt_repository, transport_worker):
    client, signer, _ = signed_api
    authorize(bindings, prepared, attempt_repository, transport_worker)
    assert post(client, signer, PREFIX + "/handoff", b"{}").status_code == 200
    with database.admin_connection() as connection:
        connection.execute(
            "update platform_control.turn_attempts set lease_expires_at=clock_timestamp()-interval '1 second' "
            "where attempt_id=%s", (prepared[0].attempt_id,),
        )
    lease = attempt_repository.claim_due(uuid4(), 60)
    assert lease.status == "reconciling"
    assert lease.lease_epoch == prepared[0].lease_epoch + 1
    return lease


def state(database, attempt_id):
    with database.admin_connection() as connection:
        return connection.execute(
            "select j.payload_ciphertext,j.encryption_key_version,b.recovery_poll_after,"
            "(select count(*) from platform_hr.tool_grants_v6 g where g.attempt_id=b.attempt_id) "
            "from platform_control.direct_command_bindings b join platform_control.execution_jobs j using(job_id) "
            "where b.attempt_id=%s", (attempt_id,),
        ).fetchone()


@pytest.mark.parametrize("phase", ["cloud", "draining_cloud"])
@pytest.mark.parametrize("already_refreshed", [False, True])
def test_wrong_lane_recovery_returns_no_authority_and_makes_no_mutation(
    database, signed_api, recovering, phase, already_refreshed,
):
    client, signer, _ = signed_api
    if already_refreshed:
        assert post(client, signer, PREFIX + "/recovery", b"{}").status_code == 200
        with database.admin_connection() as connection:
            connection.execute(
                "update platform_control.direct_command_bindings set recovery_poll_after=null where attempt_id=%s",
                (recovering.attempt_id,),
            )
    set_residual_phase(database, phase)
    before = state(database, recovering.attempt_id)
    result = post(client, signer, PREFIX + "/recovery", b"{}")
    assert result.status_code == 204
    assert state(database, recovering.attempt_id) == before


def test_draining_legacy_allows_owned_recovery_and_refreshes_current_grant(
    database, signed_api, recovering,
):
    client, signer, _ = signed_api
    with database.admin_connection() as connection:
        connection.execute(
            "select * from platform_control.transition_hr_execution_cutover_v102('draining_legacy',%s)",
            (uuid4(),),
        )
    before = state(database, recovering.attempt_id)
    work = post(client, signer, PREFIX + "/recovery", b"{}")
    assert work.status_code == 200
    assert work.json()["stop"] is False
    assert work.json()["leaseEpoch"] == recovering.lease_epoch
    assert state(database, recovering.attempt_id)[3] == before[3] + 1


@pytest.mark.parametrize("phase", ["cloud", "draining_cloud"])
def test_wrong_lane_stopping_and_observation_callbacks_keep_original_authority(
    database, signed_api, recovering, attempt_repository, worker_turn, prepared, phase,
):
    client, signer, _ = signed_api
    attempt_repository.request_cancel(
        worker_turn.conversation.owner_internal_user_id, worker_turn.turn.turn_id,
    )
    set_residual_phase(database, phase)
    before = state(database, recovering.attempt_id)
    work = post(client, signer, PREFIX + "/recovery", b"{}")
    assert work.status_code == 200
    assert work.json()["stop"] is True
    after = state(database, recovering.attempt_id)
    assert after[:2] == before[:2]
    assert after[3] == before[3]
    body = {"executorId": str(recovering.executor_id), "leaseEpoch": recovering.lease_epoch,
            "observation": observation(work.json()["command"], missing=True)}
    assert post(client, signer, f"{PREFIX}/runs/{prepared[1].run_id}/recovery", json.dumps(body).encode()).status_code == 200
