"""Ledger/grant seams; byte transfer and provider input use the separate HTTP loop."""

# ruff: noqa: PLC0414
import hashlib
from pathlib import Path
from uuid import UUID, uuid4

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
from app.agent_brain.turn_result_projection import TurnResultProjector
from app.attachments.conversation_repository import (
    attachment_name_subject,
    attachment_object_subject,
)
from app.attachments.grant_service import (
    AttachmentGrantService,
    TaskGrantRepository,
    TaskGrantUnavailable,
)
from app.execution_relay.recovery_v5 import bound_command
from app.execution_relay.repository import ExecutionRelayRepository
from tests.helpers.hr_web_loop import WebLoop

pytestmark = pytest.mark.postgres


@pytest.fixture()
def material_adapter(direct_database, repository, attempt_repository, worker_turn):
    environment, owner, _ = direct_database
    database_url = environment["urls"]["platform_control_app"]
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute("set local role platform_control_owner")
        connection.execute(
            (
                Path(__file__).parents[1]
                / "control_migrations/pending/hr_web_result_recovery.sql"
            ).read_text()
        )
    bindings = DirectCommandBindingRepository(
        ExecutionRelayRepository(database_url, content_codec=repository.content_codec)
    )
    adapter = DirectMissionAdapter(
        attempt_repository,
        bindings,
        ConversationContextBuilder(repository),
        TurnResultProjector(attempt_repository, bindings),
    )
    # Configured existing transfer authority, not a fake grant issuer.
    adapter.attachment_grants = AttachmentGrantService(
        TaskGrantRepository(database_url, content_codec=repository.content_codec),
        None,
        grant_seconds=24 * 60 * 60,
    )
    identity = WebLoop(environment, owner, worker_turn.conversation.conversation_id)
    try:
        yield adapter
    finally:
        identity.close()


def _ready_input(environment, codec, worker_turn, attachment_id, name):
    """Synthetic existing archive ledger, not upload/readiness acceptance."""
    data = b"owned ledger fixture: " + name.encode()
    name_value = codec.seal_json(
        attachment_name_subject(attachment_id), {"original_name": name}
    )
    object_value = codec.seal_json(
        attachment_object_subject(attachment_id), {"object_ref": "owned-ledger-object"}
    )
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_attachments.attachments(attachment_id,owner_internal_user_id,conversation_id,source_kind,original_name_ciphertext,original_name_key_version,object_ref_ciphertext,object_ref_key_version,immutable_locator,declared_mime,detected_mime,size_bytes,sha256,state,ready_at) values(%s,%s,%s,'user_input',%s,%s,%s,%s,'etag:owned-ledger-fixture','text/markdown','text/markdown',%s,%s,'ready',clock_timestamp())",
            (
                attachment_id,
                worker_turn.conversation.owner_internal_user_id,
                worker_turn.conversation.conversation_id,
                name_value.ciphertext,
                name_value.key_version,
                object_value.ciphertext,
                object_value.key_version,
                len(data),
                hashlib.sha256(data).digest(),
            ),
        )
        connection.execute(
            "insert into platform_attachments.bindings(binding_id,attachment_id,owner_internal_user_id,kind,conversation_id,turn_id,agent_id) values(%s,%s,%s,'turn_input',%s,%s,'hr-bot')",
            (
                uuid4(),
                attachment_id,
                worker_turn.conversation.owner_internal_user_id,
                worker_turn.conversation.conversation_id,
                worker_turn.turn.turn_id,
            ),
        )
    return data


def test_selected_archive_ids_and_sha_survive_frozen_handoff_and_recovery(
    material_adapter,
    direct_database,
    repository,
    worker_turn,
    attempt_repository,
):
    environment, _, _ = direct_database
    # Historical SQL order is UUID order, NOT a claim of original upload order.
    ids = (
        UUID("bbbbbbbb-1111-4111-8111-111111111111"),
        UUID("aaaaaaaa-1111-4111-8111-111111111111"),
    )
    contents = {
        value: _ready_input(
            environment,
            repository.content_codec,
            worker_turn,
            value,
            f"resume-{index}.md",
        )
        for index, value in enumerate(ids)
    }
    lease = attempt_repository.claim_due(uuid4(), 60)
    assert lease is not None
    binding = material_adapter.prepare(lease)
    assert binding is not None
    frozen = binding.frozen.document
    expected = sorted(ids)
    assert [value["attachmentId"] for value in frozen["inputAttachments"]] == [
        str(value) for value in expected
    ]
    assert [value["sha256"] for value in frozen["inputAttachments"]] == [
        hashlib.sha256(contents[value]).hexdigest() for value in expected
    ]
    assert frozen["outputScope"]["taskId"] == str(binding.run_id)
    handoff = material_adapter.bindings.handoff(lease.admission["workerId"])
    assert handoff is not None
    command = handoff["command"]
    assert [value["attachmentId"] for value in command["inputAttachmentGrants"]] == [
        str(value) for value in expected
    ]
    assert command["outputWriteGrant"]["taskId"] == str(binding.run_id)
    assert material_adapter.prepare(lease) == binding
    repeated = material_adapter.bindings.handoff(lease.admission["workerId"])
    assert repeated["command"] == command
    with attempt_repository.transaction() as connection:
        row = material_adapter.bindings._transport_row(lease, connection)
        recovered, _ = bound_command(material_adapter.bindings, row)
        assert recovered.model_dump(mode="json", by_alias=True) == command
        assert all(
            value.encode() not in bytes(row["payload_ciphertext"])
            for value in (
                *(grant["bearerToken"] for grant in command["inputAttachmentGrants"]),
                command["outputWriteGrant"]["bearerToken"],
            )
        )
    with psycopg.connect(environment["admin"]) as connection:
        rows = connection.execute(
            "select scope,attachment_id,token_sha256,revoked_at from platform_attachments.task_grants where task_id=%s order by scope,attachment_id",
            (binding.run_id,),
        ).fetchall()
    assert len(rows) == 3
    assert all(row[3] is None for row in rows)
    tokens = [value["bearerToken"] for value in command["inputAttachmentGrants"]] + [
        command["outputWriteGrant"]["bearerToken"]
    ]
    assert {bytes(row[2]) for row in rows} == {
        hashlib.sha256(value.encode()).digest() for value in tokens
    }


def test_grant_failure_rolls_back_command_compatibility_and_all_issued_grants(
    material_adapter,
    direct_database,
    repository,
    worker_turn,
    attempt_repository,
):
    from app.agent_brain.conversation_context import ConversationContextError

    environment, _, _ = direct_database
    attachment_id = uuid4()
    _ready_input(
        environment, repository.content_codec, worker_turn, attachment_id, "resume.md"
    )
    original = material_adapter.attachment_grants.issue_output

    def unavailable_after_write(*args, **kwargs):
        original(*args, **kwargs)
        raise TaskGrantUnavailable()

    material_adapter.attachment_grants.issue_output = unavailable_after_write
    lease = attempt_repository.claim_due(uuid4(), 60)
    with pytest.raises(
        ConversationContextError, match="material_execution_unavailable"
    ):
        material_adapter.prepare(lease)
    with psycopg.connect(environment["admin"]) as connection:
        assert connection.execute(
            "select transport_run_id from platform_control.turn_attempts where attempt_id=%s",
            (lease.attempt_id,),
        ).fetchone() == (None,)
        assert connection.execute(
            "select count(*) from platform_control.mission_tasks where mission_id=%s",
            (worker_turn.turn.mission_id,),
        ).fetchone() == (0,)
        assert connection.execute(
            "select count(*) from platform_attachments.task_grants where attachment_id=%s",
            (attachment_id,),
        ).fetchone() == (0,)
        assert connection.execute(
            "select count(*) from platform_control.direct_command_bindings where attempt_id=%s",
            (lease.attempt_id,),
        ).fetchone() == (0,)
