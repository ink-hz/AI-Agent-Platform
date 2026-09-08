"""Signed-source Result consumers; native/PDF bytes are covered by the HTTP loop."""

# ruff: noqa: PLC0414
import asyncio
import hashlib
import io
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from time import monotonic
from uuid import uuid4

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_conversation_attachment_api import Auth, authenticate
from test_execution_acceptance_v5 import response
from test_execution_transport_v5 import PREFIX, post
from test_execution_transport_v5 import signed_api as signed_api
from test_execution_worker_v5_receiver import event
from test_hr_direct_material_transport import (
    attempt_repository as attempt_repository,
)
from test_hr_direct_material_transport import (
    control_database as control_database,
)
from test_hr_direct_material_transport import (
    conversation_database as conversation_database,
)
from test_hr_direct_material_transport import (
    direct_database as direct_database,
)
from test_hr_direct_material_transport import (
    material_adapter as material_adapter,
)
from test_hr_direct_material_transport import (
    repository as repository,
)
from test_hr_direct_material_transport import (
    worker_conversation as worker_conversation,
)
from test_hr_direct_material_transport import (
    worker_turn as worker_turn,
)

from app.agent_brain.direct_worker import DirectWorker
from app.agent_brain.turn_snapshot import TurnSnapshotReader
from app.attachments.artifact_service import (
    ArtifactOutputService,
    ArtifactRepository,
    ArtifactUploadError,
)
from app.attachments.conversation_models import ObjectReceipt
from app.attachments.conversation_routes import build_conversation_attachment_router
from app.attachments.download_service import (
    ConversationAttachmentAccessRepository,
    ConversationAttachmentDownloadService,
    S3ImmutableAttachmentStore,
)
from app.attachments.result_artifact_recovery import ArtifactRecovery, artifact_key
from app.attachments.scanner import TrustedInternalScanner
from app.attachments.validation import AttachmentValidator, OpenedObject
from app.attachments.worker import AttachmentProcessor
from app.attachments.worker_runtime import AttachmentProcessingRepository
from app.control_plane.middleware import IdentitySecurityMiddleware
from app.control_plane.models import AuthContext, Role
from app.execution_relay.contracts_v5 import parse_v5_command, parse_v5_event

pytestmark = pytest.mark.postgres


@pytest.fixture()
def prepared(material_adapter, attempt_repository):
    lease = attempt_repository.claim_due(uuid4(), 60)
    return lease, material_adapter.prepare(lease)


@pytest.fixture()
def bindings(material_adapter):
    return material_adapter.bindings


@pytest.fixture()
def transport_worker(prepared, direct_database):
    worker_id = prepared[0].admission["workerId"]
    yield worker_id
    # The reused readiness fixture owns/deletes this Worker. Remove only the
    # signing key created by signed_api before that fixture's teardown.
    with psycopg.connect(direct_database[0]["admin"]) as connection:
        connection.execute(
            "delete from platform_control.execution_worker_nonces where worker_id=%s",
            (worker_id,),
        )
        connection.execute(
            "delete from platform_control.execution_worker_keys where worker_id=%s and key_id='worker-v1'",
            (worker_id,),
        )


@pytest.fixture()
def artifact_content():
    return (
        Path(__file__).parent / "fixtures/conversation_attachments/valid.pdf"
    ).read_bytes()


@pytest.fixture()
def intent_changes(request):
    return getattr(request, "param", {})


@pytest.fixture()
def file_warning(request):
    return getattr(request, "param", None)


@pytest.fixture()
def artifact_result(
    signed_api,
    prepared,
    material_adapter,
    artifact_content,
    intent_changes,
    file_warning,
):
    client, signer, _ = signed_api
    lease, binding = prepared
    command = post(client, signer, PREFIX + "/handoff", b"{}").json()["command"]
    accepted = post(
        client,
        signer,
        f"{PREFIX}/runs/{binding.run_id}/acceptance",
        json.dumps(response(parse_v5_command(command))).encode(),
    )
    assert accepted.status_code == 200
    raw = event(command, kind="result")
    content = artifact_content
    raw["payload"]["publicAnswerMarkdown"] = "面试方案已整理，文件单独准备。"
    raw["payload"]["executionRecovery"].update(
        executorStopped=False, executorStopProofRef=None
    )
    raw["payload"]["artifactIntents"] = [
        {
            "taskId": str(binding.run_id),
            "principalRef": command["principalRef"],
            "conversationId": command["conversationId"],
            "index": 0,
            "sha256": hashlib.sha256(content).hexdigest(),
            "mimeType": "application/pdf",
            "sizeBytes": len(content),
            "displayName": "interview.pdf",
            "opaqueSpoolRef": "spool:owned-artifact-0",
        }
    ]
    raw["payload"]["artifactIntents"][0].update(intent_changes)
    if file_warning is not None:
        warning = {
            **raw,
            "type": "raw_progress",
            "payload": {
                "source": "agent_runtime",
                "sourceRef": f"v5:{raw['commandId']}:output-files",
                "visibility": "private",
                "kind": "file",
                "text": "output_files_unavailable",
                **file_warning,
            },
        }
        assert (
            post(
                client,
                signer,
                f"{PREFIX}/runs/{binding.run_id}/events",
                json.dumps(warning).encode(),
            ).status_code
            == 200
        )
        raw["seq"] += 1
        raw["payload"]["artifactIntents"] = []
        raw["payload"]["publicAnswerMarkdown"] = "中" * 43690 + "ab"
    saved = post(
        client,
        signer,
        f"{PREFIX}/runs/{binding.run_id}/events",
        json.dumps(raw).encode(),
    )
    assert saved.status_code == 200
    message_id = material_adapter.projector.commit(lease, parse_v5_event(raw))
    return lease, binding, command, raw, message_id


@pytest.mark.parametrize(
    "file_warning",
    [
        {},
        {"source": "provider"},
        {"kind": "log"},
        {"sourceRef": f"v5:{uuid4()}:output-files"},
        {"text": "private exception detail"},
    ],
    indirect=True,
)
def test_fixed_file_warning_is_visible_once_without_truncating_answer(
    file_warning,
    artifact_result,
    repository,
    material_adapter,
    worker_turn,
):
    lease, _, _, raw, message_id = artifact_result
    owner = worker_turn.conversation.owner_internal_user_id
    conversation = worker_turn.conversation.conversation_id
    assert len(raw["payload"]["publicAnswerMarkdown"].encode()) == 131072
    assert material_adapter.projector.commit(lease, parse_v5_event(raw)) == message_id
    messages = repository.messages_after(
        owner, conversation, turn_id=worker_turn.turn.turn_id
    )
    assert [m.content for m in messages if m.role == "assistant"] == [
        raw["payload"]["publicAnswerMarkdown"]
    ]
    notices = [m.content for m in messages if m.role == "system"]
    assert notices == (
        ["文字回答已完成，但生成文件未能整理成功，当前没有可下载附件。"]
        if not file_warning
        else []
    )
    assert (
        TurnSnapshotReader(repository).get(owner, conversation)["answer"]["content"]
        == raw["payload"]["publicAnswerMarkdown"]
    )


def test_text_is_available_with_pending_artifact_not_empty_enrichment(
    artifact_result,
    repository,
    worker_turn,
):
    _, _, _, raw, message_id = artifact_result
    snapshot = TurnSnapshotReader(repository).get(
        worker_turn.conversation.owner_internal_user_id,
        worker_turn.conversation.conversation_id,
        worker_turn.turn.turn_id,
    )
    assert snapshot["answer"]["message_id"] == str(message_id)
    assert snapshot["answer"]["content"] == raw["payload"]["publicAnswerMarkdown"]
    assert snapshot["result_enrichment"] == {
        "status": "pending",
        "pending_count": 1,
        "failed_count": 0,
    }
    assert snapshot["deliveries"] == []
    message = repository.messages_after(
        worker_turn.conversation.owner_internal_user_id,
        worker_turn.conversation.conversation_id,
    )[-1]
    assert message.result_delivery_status == "pending"


def test_text_commit_preserves_original_unexpired_grant_for_its_fixed_intent(
    artifact_result,
    direct_database,
):
    _, binding, command, _, _ = artifact_result
    environment, _, _ = direct_database
    digest = hashlib.sha256(
        command["outputWriteGrant"]["bearerToken"].encode()
    ).digest()
    with psycopg.connect(environment["admin"]) as connection:
        row = connection.execute(
            "select revoked_at,expires_at>clock_timestamp(),file_count from platform_attachments.task_grants where task_id=%s and token_sha256=%s",
            (binding.run_id, digest),
        ).fetchone()
    assert row == (None, True, 0)


def register_intent(direct_database, material_adapter, artifact_result, **changes):
    _, binding, command, raw, _ = artifact_result
    intent = raw["payload"]["artifactIntents"][0]
    values = {
        "token_sha256": hashlib.sha256(
            command["outputWriteGrant"]["bearerToken"].encode()
        ).digest(),
        "task_id": binding.run_id,
        "agent_id": "hr-bot",
        "artifact_key": artifact_key(intent["displayName"]),
        "producer_version_id": intent["sha256"],
        "display_name": intent["displayName"],
        "declared_mime": intent["mimeType"],
        "declared_size": intent["sizeBytes"],
        "expected_sha256": bytes.fromhex(intent["sha256"]),
    }
    values.update(changes)
    return ArtifactRepository(
        direct_database[0]["urls"]["platform_control_app"],
        content_codec=material_adapter.bindings.relay.content_codec,
    ).register(**values)


def test_fixed_file_can_start_after_text_and_lost_begin_response_replays_same_upload(
    direct_database,
    material_adapter,
    artifact_result,
):
    first = register_intent(direct_database, material_adapter, artifact_result)
    second = register_intent(direct_database, material_adapter, artifact_result)
    assert first.state == "uploading"
    assert second.replayed
    assert first.upload_id == second.upload_id
    assert first.attachment_id == second.attachment_id
    with psycopg.connect(direct_database[0]["admin"]) as connection:
        assert (
            connection.execute(
                "select status from platform_control.mission_tasks where task_id=%s",
                (artifact_result[1].run_id,),
            ).fetchone()[0]
            == "completed"
        )
        assert (
            connection.execute(
                "select file_count from platform_attachments.task_grants where task_id=%s and scope='write_output'",
                (artifact_result[1].run_id,),
            ).fetchone()[0]
            == 1
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"artifact_key": "artifact-" + "a" * 24},
        {"producer_version_id": "b" * 64},
        {"declared_mime": "text/plain"},
        {"declared_size": 1},
        {"expected_sha256": b"x" * 32},
        {"agent_id": "admin-bot"},
        {"display_name": "changed.pdf"},
    ],
)
def test_text_completion_does_not_authorize_an_unlisted_or_changed_file(
    direct_database,
    material_adapter,
    artifact_result,
    changes,
):
    with pytest.raises(ArtifactUploadError):
        register_intent(direct_database, material_adapter, artifact_result, **changes)


def test_completed_text_never_renews_an_expired_output_grant(
    direct_database,
    material_adapter,
    artifact_result,
    repository,
    worker_turn,
):
    with psycopg.connect(direct_database[0]["admin"]) as connection:
        connection.execute(
            "update platform_attachments.task_grants set expires_at=clock_timestamp()-interval '1 second' where task_id=%s",
            (artifact_result[1].run_id,),
        )
    with pytest.raises(ArtifactUploadError):
        register_intent(direct_database, material_adapter, artifact_result)
    assert ArtifactRecovery(repository).retry_due() == 1
    snapshot = TurnSnapshotReader(repository).get(
        worker_turn.conversation.owner_internal_user_id,
        worker_turn.conversation.conversation_id,
    )
    assert snapshot["result_enrichment"] == {
        "status": "failed",
        "pending_count": 0,
        "failed_count": 1,
    }
    assert snapshot["answer"]["message_id"] == str(artifact_result[4])


class OwnedObjectStore:
    """Byte storage substitute only; upload, digest, validator and PG transitions are real."""

    def __init__(self):
        self.objects = {}

    def put_stream(self, object_ref, body, expected_size):
        data = body.read()
        assert len(data) == expected_size
        self.objects[object_ref] = data
        return ObjectReceipt(len(data), hashlib.sha256(data).digest())

    def open(self, object_ref, immutable_locator=None):
        data = self.objects[object_ref]
        locator = "etag:" + hashlib.sha256(data).hexdigest()
        assert immutable_locator in (None, locator)
        return OpenedObject(io.BytesIO(data), len(data), locator)

    def delete(self, object_ref):
        self.objects.pop(object_ref, None)

    def get_object(self, *, Bucket, Key, IfMatch):
        assert Bucket == "owned-result-files"
        data = self.objects[Key]
        assert IfMatch == hashlib.sha256(data).hexdigest()
        return {"Body": io.BytesIO(data)}


def upload_and_process(
    direct_database, material_adapter, artifact_result, artifact_content
):
    upload = register_intent(direct_database, material_adapter, artifact_result)
    environment = direct_database[0]
    codec = material_adapter.bindings.relay.content_codec
    objects = OwnedObjectStore()
    service = ArtifactOutputService(
        ArtifactRepository(
            environment["urls"]["platform_control_app"], content_codec=codec
        ),
        objects,
    )
    token = artifact_result[2]["outputWriteGrant"]["bearerToken"]
    written = service.write(
        token, upload.upload_id, io.BytesIO(artifact_content), len(artifact_content)
    )
    assert written.state == "validating"
    processor = AttachmentProcessor(
        repository=AttachmentProcessingRepository(
            environment["urls"]["platform_brain_worker"], content_codec=codec
        ),
        object_store=objects,
        validator=AttachmentValidator(),
        scanner=TrustedInternalScanner(),
        derivatives=None,
        worker_id="owned-result-artifact-fixture",
    )
    assert asyncio.run(processor.process_next())
    assert asyncio.run(processor.process_next())
    return upload, objects


def test_late_ready_pdf_rebinds_once_after_consumer_restart_without_new_attempt(
    direct_database,
    material_adapter,
    artifact_result,
    artifact_content,
    repository,
    worker_turn,
):
    upload, _ = upload_and_process(
        direct_database, material_adapter, artifact_result, artifact_content
    )
    # Real validator/processing SQL, not a seeded ready flag or fabricated receipt.
    with psycopg.connect(direct_database[0]["admin"]) as connection:
        assert connection.execute(
            "select state,detected_mime from platform_attachments.attachments where attachment_id=%s",
            (upload.attachment_id,),
        ).fetchone() == ("ready", "application/pdf")
    assert ArtifactRecovery(repository).retry_due() == 1
    assert ArtifactRecovery(repository).on_ready(upload.attachment_id) == 0
    assert ArtifactRecovery(repository).retry_due() == 0
    snapshot = TurnSnapshotReader(repository).get(
        worker_turn.conversation.owner_internal_user_id,
        worker_turn.conversation.conversation_id,
        worker_turn.turn.turn_id,
    )
    assert snapshot["result_enrichment"] == {
        "status": "ready",
        "pending_count": 0,
        "failed_count": 0,
    }
    messages = repository.messages_after(
        worker_turn.conversation.owner_internal_user_id,
        worker_turn.conversation.conversation_id,
    )
    assert messages[-1].message_id == artifact_result[4]
    assert [item.attachment_id for item in messages[-1].output_attachments] == [
        upload.attachment_id
    ]
    assert messages[-1].result_delivery_status == "completed"
    with psycopg.connect(direct_database[0]["admin"]) as connection:
        assert (
            connection.execute(
                "select count(*) from platform_control.turn_attempts where turn_id=%s",
                (worker_turn.turn.turn_id,),
            ).fetchone()[0]
            == 1
        )


def test_two_file_consumers_do_not_duplicate_the_message_binding(
    direct_database,
    material_adapter,
    artifact_result,
    artifact_content,
    repository,
):
    upload, _ = upload_and_process(
        direct_database, material_adapter, artifact_result, artifact_content
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            pool.submit(ArtifactRecovery(repository).on_ready, upload.attachment_id)
            for _ in range(2)
        ]
        assert sum(result.result(timeout=10) for result in results) == 1
    assert ArtifactRecovery(repository).on_ready(uuid4()) == 0


def test_result_file_identity_cannot_be_rewritten_by_the_consumer(
    direct_database,
    artifact_result,
):
    with (
        psycopg.connect(
            direct_database[0]["urls"]["platform_control_app"]
        ) as connection,
        pytest.raises(psycopg.errors.CheckViolation, match="immutable"),
    ):
        connection.execute(
            "update platform_control.result_artifact_intents set artifact_key=%s where run_id=%s",
            ("artifact-" + "f" * 24, artifact_result[1].run_id),
        )


@pytest.mark.parametrize(
    "intent_changes",
    [
        {"principalRef": "principal:another-owner"},
        {"conversationId": str(uuid4())},
        {"taskId": str(uuid4())},
        {"sizeBytes": 51 * 1024 * 1024},
    ],
    indirect=True,
)
def test_bad_file_intent_never_withdraws_text_or_authorizes_upload(
    artifact_result,
    repository,
    worker_turn,
    direct_database,
    material_adapter,
):
    snapshot = TurnSnapshotReader(repository).get(
        worker_turn.conversation.owner_internal_user_id,
        worker_turn.conversation.conversation_id,
    )
    assert snapshot["answer"]["message_id"] == str(artifact_result[4])
    assert snapshot["result_enrichment"] == {
        "status": "failed",
        "pending_count": 0,
        "failed_count": 1,
    }
    with pytest.raises(ArtifactUploadError):
        register_intent(direct_database, material_adapter, artifact_result)


def test_independent_worker_advances_ready_files_without_reopening_completed_text(
    direct_database,
    material_adapter,
    artifact_result,
    artifact_content,
    repository,
    attempt_repository,
    worker_turn,
):
    upload, _ = upload_and_process(
        direct_database, material_adapter, artifact_result, artifact_content
    )
    worker = DirectWorker(
        attempt_repository,
        material_adapter,
        executor_id=artifact_result[0].executor_id,
        artifact_recovery=ArtifactRecovery(repository),
    )
    try:
        deadline = monotonic() + 6
        while monotonic() < deadline:
            worker.tick()
            current = repository.messages_after(
                worker_turn.conversation.owner_internal_user_id,
                worker_turn.conversation.conversation_id,
            )[-1]
            if current.output_attachments:
                break
            Event().wait(0.02)
    finally:
        worker.close()
    if worker._artifact_pending is not None:
        worker._artifact_pending.result()
    message = repository.messages_after(
        worker_turn.conversation.owner_internal_user_id,
        worker_turn.conversation.conversation_id,
    )[-1]
    with repository._connection() as connection:
        diagnostic = connection.execute(
            "select status,last_error_code from platform_control.result_artifact_intents where run_id=%s",
            (artifact_result[1].run_id,),
        ).fetchone()
    assert [item.attachment_id for item in message.output_attachments] == [
        upload.attachment_id
    ], diagnostic


def test_real_ready_pdf_download_api_returns_original_bytes_and_checks_owner(
    direct_database,
    material_adapter,
    artifact_result,
    artifact_content,
    repository,
    worker_turn,
    tmp_path,
):
    upload, objects = upload_and_process(
        direct_database, material_adapter, artifact_result, artifact_content
    )
    assert ArtifactRecovery(repository).on_ready(upload.attachment_id) == 1
    download = ConversationAttachmentDownloadService(
        ConversationAttachmentAccessRepository(
            direct_database[0]["urls"]["platform_control_app"],
            content_codec=repository.content_codec,
        ),
        S3ImmutableAttachmentStore(objects, "owned-result-files"),
        ticket_secret=b"t" * 32,
    )
    download._temporary_root = tmp_path

    class FixtureIdentity(Auth):
        # Only IdP/session resolution is substituted here; Task1 owns the real
        # persisted web-auth loop. Download authorization and CSRF are real.
        def authenticate(self, token):
            if token not in {"owner", "other"}:
                return None
            owner = (
                worker_turn.conversation.owner_internal_user_id
                if token == "owner"
                else uuid4()
            )
            return AuthContext(owner, Role.MEMBER, uuid4(), False), b"csrf"

    app = FastAPI()
    app.state.conversation_attachment_download_service = download
    app.include_router(build_conversation_attachment_router())
    app.add_middleware(
        IdentitySecurityMiddleware,
        auth=FixtureIdentity(),
        public_assets=frozenset(),
        routes=tuple(app.router.routes),
    )
    with TestClient(app) as client:
        path = f"/api/v1/attachments/{upload.attachment_id}/ticket"
        assert client.post(path, json={"purpose": "download"}).status_code == 401
        headers = authenticate(client)
        assert (
            client.post(
                path,
                json={"purpose": "download"},
                headers={**headers, "X-CSRF-Token": "wrong"},
            ).status_code
            == 403
        )
        ticket = client.post(path, json={"purpose": "download"}, headers=headers)
        assert ticket.status_code == 200
        response = client.get(ticket.json()["content_path"])
        assert response.status_code == 200
        assert response.content == artifact_content
        assert response.headers["content-type"] == "application/pdf"
        assert "attachment;" in response.headers["content-disposition"]
        assert list(tmp_path.iterdir()) == []
        assert (
            client.post(
                path,
                json={"purpose": "download"},
                headers=authenticate(client, owner=False),
            ).status_code
            == 404
        )
