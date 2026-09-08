# ruff: noqa: PLC0414
import json
from pathlib import Path
from uuid import uuid4

import pytest
from jsonschema import validate
from test_turn_result_projection import (
    attempt_repository as attempt_repository,
)
from test_turn_result_projection import (
    bindings as bindings,
)
from test_turn_result_projection import (
    control_database as control_database,
)
from test_turn_result_projection import (
    conversation_database as conversation_database,
)
from test_turn_result_projection import (
    direct_database as direct_database,
)
from test_turn_result_projection import (
    prepared as prepared,
)
from test_turn_result_projection import (
    projector as projector,
)
from test_turn_result_projection import (
    repository as repository,
)
from test_turn_result_projection import (
    result_source as result_source,
)
from test_turn_result_projection import (
    signed_api as signed_api,
)
from test_turn_result_projection import (
    transport_worker as transport_worker,
)
from test_turn_result_projection import (
    worker_conversation as worker_conversation,
)
from test_turn_result_projection import (
    worker_turn as worker_turn,
)

from app.agent_brain.conversation_repository import ConversationRepositoryNotFound
from app.execution_relay.contracts_v5 import parse_v5_event

pytestmark = pytest.mark.postgres
SCHEMA = json.loads(
    (
        Path(__file__).parents[2] / "contracts/hr-execution/v5/snapshot.schema.json"
    ).read_text()
)


def test_snapshot_recovers_persisted_answer_independently_of_history_page(
    result_source, projector, repository, worker_turn, attempt_repository
):
    from app.agent_brain.turn_snapshot import TurnSnapshotReader

    lease, _, raw = result_source
    projector.commit(lease, parse_v5_event(raw))
    reader = TurnSnapshotReader(repository)
    owner, conversation = (
        worker_turn.conversation.owner_internal_user_id,
        worker_turn.conversation.conversation_id,
    )
    before = reader.get(owner, conversation)
    validate(before, SCHEMA)
    assert before["answer"]["content"] == raw["payload"]["publicAnswerMarkdown"]
    assert before["attempt"]["status"] == "reconciling"
    assert before["turn"]["status"] == "completed"
    assert before["deliveries"] == []
    with attempt_repository.transaction() as connection:
        original = connection.execute(
            "select to_jsonb(c) from platform_control.conversations c where conversation_id=%s",
            (conversation,),
        ).fetchone()
    assert TurnSnapshotReader(repository).get(owner, conversation) == before
    with attempt_repository.transaction() as connection:
        assert (
            connection.execute(
                "select to_jsonb(c) from platform_control.conversations c where conversation_id=%s",
                (conversation,),
            ).fetchone()
            == original
        )
    with pytest.raises(ConversationRepositoryNotFound):
        reader.get(uuid4(), conversation)


def test_empty_freeform_has_no_fabricated_turn_or_manifest(
    worker_conversation, repository
):
    from app.agent_brain.turn_snapshot import TurnSnapshotReader

    value = TurnSnapshotReader(repository).get(
        worker_conversation.owner_internal_user_id, worker_conversation.conversation_id
    )
    validate(value, SCHEMA)
    assert value["turn"] is None and value["context_manifest_ref"] is None


def test_late_signed_stop_advances_snapshot_without_republishing_answer(
    result_source,
    projector,
    repository,
    worker_turn,
    attempt_repository,
    bindings,
    signed_api,
):
    from test_execution_recovery_v5 import observation
    from test_execution_transport_v5 import PREFIX, post

    from app.agent_brain.direct_mission_adapter import DirectMissionAdapter
    from app.agent_brain.turn_snapshot import TurnSnapshotReader

    lease, binding, raw = result_source
    projector.commit(lease, parse_v5_event(raw))
    reader = TurnSnapshotReader(repository)
    owner = worker_turn.conversation.owner_internal_user_id
    conversation = worker_turn.conversation.conversation_id
    before = reader.get(owner, conversation)
    client, signer, _ = signed_api
    work = post(client, signer, PREFIX + "/recovery", b"{}").json()
    value = observation(work["command"])
    value["recovery"].update(
        executorStopped=True, executorStopProofRef="owned-native-exit"
    )
    response = post(
        client,
        signer,
        f"{PREFIX}/runs/{binding.run_id}/recovery",
        json.dumps(
            {
                "executorId": str(lease.executor_id),
                "leaseEpoch": lease.lease_epoch,
                "observation": value,
            }
        ).encode(),
    )
    assert response.status_code == 200
    DirectMissionAdapter(attempt_repository, bindings, None, projector).reconcile(lease)
    after = reader.get(owner, conversation)
    assert after["attempt"]["status"] == "completed"
    assert after["answer"] == before["answer"]
    assert after["read_version"] > before["read_version"]
    assert after["event_cursor"] > before["event_cursor"]


def test_worker_projects_only_safe_signed_progress_and_does_not_repeat_it(
    signed_api,
    prepared,
    bindings,
    attempt_repository,
    transport_worker,
    worker_turn,
    repository,
    projector,
):
    from test_execution_acceptance_v5 import response
    from test_execution_transport_v5 import PREFIX, post
    from test_execution_worker_v5_receiver import event

    from app.agent_brain.direct_mission_adapter import DirectMissionAdapter
    from app.execution_relay.contracts_v5 import parse_v5_command

    lease, binding = prepared
    with attempt_repository.transaction() as connection:
        bindings.authorize_transport(
            lease, transport_worker, "http://127.0.0.1:19191", connection=connection
        )
    client, signer, _ = signed_api
    command = post(client, signer, PREFIX + "/handoff", b"{}").json()["command"]
    bindings.acknowledge_transport(
        transport_worker, binding.run_id, response(parse_v5_command(command))
    )
    for seq, (kind, source, text) in enumerate(
        [
            ("thinking_summary", "provider", "PRIVATE_THINKING"),
            ("agent_message", "provider", "正在提取岗位要求"),
            ("work_update", "agent_sdk", "正在核对技能要求"),
            ("log", "agent_runtime", "PRIVATE_LOG"),
        ],
        1,
    ):
        raw = event(command, seq)
        raw["payload"].update(kind=kind, source=source, text=text)
        assert (
            post(
                client,
                signer,
                f"{PREFIX}/runs/{binding.run_id}/events",
                json.dumps(raw).encode(),
            ).status_code
            == 200
        )
    adapter = DirectMissionAdapter(attempt_repository, bindings, None, projector)
    adapter.reconcile(lease)
    owner, conversation = (
        worker_turn.conversation.owner_internal_user_id,
        worker_turn.conversation.conversation_id,
    )
    visible = repository.events_after(owner, conversation)
    summaries = [value.payload.get("summary") for value in visible]
    assert "正在提取岗位要求" in summaries and "正在核对技能要求" in summaries
    assert "PRIVATE" not in str(summaries)
    adapter.reconcile(lease)
    assert repository.events_after(owner, conversation) == visible
