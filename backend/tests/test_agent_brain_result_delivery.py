from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import hashlib
from uuid import uuid4

import psycopg
import pytest

from app.agent_brain.conversation_projection import ConversationProjection
from app.attachments.result_projection import ConversationResultProjection
from app.attachments.conversation_repository import attachment_name_subject
from app.attachments.artifact_service import ArtifactRepository, ArtifactUploadConflict
from test_agent_brain_conversation_context import _complete_mission
from test_agent_brain_conversation_repository import conversation_database, repository
from test_control_plane_migration import control_database
from test_conversation_attachment_migration import (
    _insert_artifact, _insert_attachment, _insert_task_output_binding,
)


def _complete_collaboration(repository, owner, text="文字回答不应丢失", artifact_factory=None,
                            status="completed", agent_id="hr-bot"):
    started = repository.start(owner, uuid4(), "分析岗位", mode="direct_agent", direct_agent_id=agent_id)
    run = repository._missions.create_run(
        owner, started.mission.mission_id, phase="direct", agent_id=agent_id,
        input_payload={"prompt": "分析岗位"}, objective="分析岗位",
        event_type="task.dispatched", event_payload={"agent_id": agent_id},
    )
    artifacts = artifact_factory(started, run) if artifact_factory is not None else []
    payload = {"text": text}
    if status == "completed":
        payload["collaboration"] = {
            "contract_version": "core_chat_collaboration_v4",
            "citations": [{"citationKey": "official", "title": "官网岗位",
                           "url": "https://example.com/jobs/1", "site": "example.com",
                           "retrievedAt": datetime.now(timezone.utc).isoformat(),
                           "supports": ["岗位职责"]}],
            "artifacts": artifacts, "completion": "completed", "recovery": None,
        }
    repository._missions.complete_run(
        owner, started.mission.mission_id, run.run_id, status=status,
        output_payload=payload, event_type=f"mission.{status}", event_payload={"text": text},
        mission_status=status,
    )
    return started


def _delivery(environment, mission_id):
    with psycopg.connect(environment["admin"]) as connection:
        return connection.execute(
            "select status,attempts,last_error_code from platform_control.conversation_result_deliveries where mission_id=%s",
            (mission_id,),
        ).fetchone()


def _due_now(environment):
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute("update platform_control.conversation_result_deliveries set next_attempt_at=now() where status='pending'")


@pytest.mark.postgres
def test_answer_survives_result_projection_failure_and_restart(conversation_database, repository):
    environment, owner, _ = conversation_database
    started = _complete_collaboration(repository, owner)

    class FailAfterInsert(ConversationResultProjection):
        def project_locked(self, cursor, **values):
            super().project_locked(cursor, **values)
            raise RuntimeError("private fault detail must not be stored")

    projector = ConversationProjection(repository, result_projection=FailAfterInsert(content_codec=repository.content_codec))
    assert projector.project_terminal(started.mission.mission_id) is True
    messages = repository.messages_after(owner, started.conversation.conversation_id)
    assert [(message.role, message.content) for message in messages] == [("user", "分析岗位"), ("assistant", "文字回答不应丢失")]
    assert messages[-1].result_delivery_status == "pending"
    assert _delivery(environment, started.mission.mission_id) == ("pending", 0, None)

    projector.project_pending()
    assert _delivery(environment, started.mission.mission_id) == ("pending", 1, "result_projection_unavailable")
    with psycopg.connect(environment["admin"]) as connection:
        assert connection.execute("select count(*) from platform_attachments.message_citations where message_id=%s", (messages[-1].message_id,)).fetchone()[0] == 0
    # A reconstructed service resumes from committed DB state, not memory.
    _due_now(environment)
    resumed = ConversationProjection(repository, result_projection=ConversationResultProjection(content_codec=repository.content_codec))
    assert resumed.project_pending() == 1
    assert resumed.project_pending() == 0
    assert resumed.project_terminal(started.mission.mission_id) is False
    assert _delivery(environment, started.mission.mission_id) == ("completed", 2, None)
    messages = repository.messages_after(owner, started.conversation.conversation_id)
    assert len(messages) == 2
    assert len(messages[-1].citations) == 1
    assert messages[-1].result_delivery_status == "completed"


@pytest.mark.postgres
def test_one_broken_result_does_not_block_other_deliveries(conversation_database, repository):
    environment, owner, _ = conversation_database
    broken = _complete_collaboration(repository, owner, "first")
    healthy = _complete_collaboration(repository, owner, "second")

    class FailOne(ConversationResultProjection):
        def project_locked(self, cursor, **values):
            if values["conversation_id"] == broken.conversation.conversation_id:
                raise RuntimeError("fault")
            return super().project_locked(cursor, **values)

    projector = ConversationProjection(repository, result_projection=FailOne(content_codec=repository.content_codec))
    projector.project_terminal(broken.mission.mission_id)
    projector.project_terminal(healthy.mission.mission_id)
    assert projector.project_pending() == 1
    assert _delivery(environment, broken.mission.mission_id)[:2] == ("pending", 1)
    assert _delivery(environment, healthy.mission.mission_id)[:2] == ("completed", 1)


@pytest.mark.postgres
def test_result_retry_is_bounded_and_does_not_change_completed_turn(conversation_database, repository):
    environment, owner, _ = conversation_database
    started = _complete_collaboration(repository, owner)

    class Broken(ConversationResultProjection):
        def project_locked(self, cursor, **values):
            raise RuntimeError("fault")

    projector = ConversationProjection(repository, result_projection=Broken(content_codec=repository.content_codec))
    projector.project_terminal(started.mission.mission_id)
    for attempt in range(1, 9):
        _due_now(environment)
        assert projector.project_pending() == 0
        assert _delivery(environment, started.mission.mission_id)[1] == attempt
        # Not due yet, or exhausted. No tight retry loop.
        assert projector.project_pending() == 0
        assert _delivery(environment, started.mission.mission_id)[1] == attempt
    assert _delivery(environment, started.mission.mission_id)[0] == "failed"
    assert repository.messages_after(owner, started.conversation.conversation_id)[-1].delivery_status == "completed"
    assert repository.messages_after(owner, started.conversation.conversation_id)[-1].result_delivery_status == "failed"


@pytest.mark.postgres
def test_corrupt_terminal_projection_does_not_block_healthy_conversation(conversation_database, repository):
    environment, owner, _ = conversation_database
    broken = repository.start(owner, uuid4(), "broken")
    healthy = repository.start(owner, uuid4(), "healthy")
    for started in (broken, healthy):
        _complete_mission(environment, repository, started.mission.mission_id, "answer")
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute("update platform_control.mission_events set payload_ciphertext=%s where mission_id=%s and event_type='mission.completed'", (b"x" * 40, broken.mission.mission_id))
    projector = ConversationProjection(repository)
    assert projector.project_pending() == 1
    assert repository.messages_after(owner, healthy.conversation.conversation_id)[-1].content == "answer"


@pytest.mark.postgres
def test_registered_artifact_becomes_ready_after_text_and_binds_once(conversation_database, repository):
    environment, owner, _ = conversation_database
    attachment_ids = []

    def pending_artifact(started, run):
        context = {"owner_id": owner, "conversation_id": started.conversation.conversation_id,
                   "task_id": run.task_id, "agent_id": "hr-bot"}
        with psycopg.connect(environment["admin"]) as connection:
            artifact_id = _insert_artifact(connection, context, "interview")
            attachment_id = _insert_attachment(connection, context, state="validating", source_kind="agent_output")
            _insert_task_output_binding(connection, context, attachment_id)
            name = repository.content_codec.seal_json(attachment_name_subject(attachment_id), {"original_name": "面试题.pdf"})
            connection.execute("update platform_attachments.attachments set original_name_ciphertext=%s,original_name_key_version=%s where attachment_id=%s", (name.ciphertext, name.key_version, attachment_id))
            connection.execute(
                "insert into platform_attachments.artifact_versions(artifact_version_id,artifact_id,attachment_id,version_no,producer_version_id,original_name_ciphertext,original_name_key_version,object_ref_ciphertext,object_ref_key_version) "
                "values (%s,%s,%s,1,'v1',%s,%s,%s,1)",
                (uuid4(), artifact_id, attachment_id, name.ciphertext, name.key_version, b"r" * 29),
            )
        attachment_ids.append(attachment_id)
        return [{"attachmentId": str(attachment_id), "artifactKey": "interview",
                 "producerVersionId": "v1", "displayName": "面试题.pdf", "status": "ready"}]

    started = _complete_collaboration(repository, owner, artifact_factory=pending_artifact)
    projector = ConversationProjection(repository)
    projector.project_terminal(started.mission.mission_id)
    assert projector.project_pending() == 0
    messages = repository.messages_after(owner, started.conversation.conversation_id)
    assert messages[-1].content == "文字回答不应丢失"
    assert messages[-1].output_attachments == ()
    with psycopg.connect(environment["admin"]) as connection:
        # Emulate the asynchronous file processor completing its own DB state.
        connection.execute("update platform_attachments.attachments set state='ready',ready_at=now(),immutable_locator='version:interview-v1' where attachment_id=%s", (attachment_ids[0],))
        connection.execute("update platform_attachments.artifact_versions set state='ready',result_status='succeeded' where attachment_id=%s", (attachment_ids[0],))
    _due_now(environment)
    assert ConversationProjection(repository).project_pending() == 1
    assert ConversationProjection(repository).project_pending() == 0
    messages = repository.messages_after(owner, started.conversation.conversation_id)
    assert len(messages) == 2
    assert len(messages[-1].output_attachments) == 1
    assert len(messages[-1].citations) == 1

    # A different task cannot borrow this ready output merely by naming its ID.
    foreign = _complete_collaboration(repository, owner, artifact_factory=lambda *_: [
        {"attachmentId": str(attachment_ids[0]), "artifactKey": "interview",
         "producerVersionId": "v1", "displayName": "面试题.pdf", "status": "ready"},
    ])
    projector.project_terminal(foreign.mission.mission_id)
    assert projector.project_pending() == 0
    foreign_messages = repository.messages_after(owner, foreign.conversation.conversation_id)
    assert foreign_messages[-1].content == "文字回答不应丢失"
    assert foreign_messages[-1].output_attachments == ()


@pytest.mark.postgres
def test_delivery_migration_scopes_grants_to_matching_environment(control_database):
    for name, environment in control_database["environments"].items():
        selected = "platform_control_app" + ("_preview" if name == "preview" else "")
        opposite = "platform_control_app" if name == "preview" else "platform_control_app_preview"
        with psycopg.connect(environment["admin"]) as connection:
            assert connection.execute(
                "select has_table_privilege(%s,'platform_control.conversation_result_deliveries','select'),"
                "has_table_privilege(%s,'platform_control.conversation_result_deliveries','insert'),"
                "has_table_privilege(%s,'platform_control.conversation_result_deliveries','update'),"
                "has_table_privilege(%s,'platform_control.conversation_result_deliveries','select'),"
                "has_table_privilege('public','platform_control.conversation_result_deliveries','select')",
                (selected, selected, selected, opposite),
            ).fetchone() == (True, True, True, False, False)


@pytest.mark.postgres
def test_concurrent_projectors_commit_one_enrichment(conversation_database, repository):
    environment, owner, _ = conversation_database
    started = _complete_collaboration(repository, owner)
    ConversationProjection(repository).project_terminal(started.mission.mission_id)
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(ConversationProjection(repository).project_pending) for _ in range(2)]
        assert sum(future.result(timeout=10) for future in futures) == 1
    assert _delivery(environment, started.mission.mission_id) == ("completed", 1, None)
    messages = repository.messages_after(owner, started.conversation.conversation_id)
    assert len(messages) == 2
    assert len(messages[-1].citations) == 1


@pytest.mark.postgres
@pytest.mark.parametrize("claim_before_result", [True, False])
@pytest.mark.parametrize("outcome", ["completed", "failed", "cancelled", "other_bot", "revoked"])
def test_inflight_registered_upload_can_finish_after_hr_text_completes(
    conversation_database, repository, claim_before_result, outcome,
):
    environment, owner, _ = conversation_database
    app_url = environment["urls"]["platform_control_app"]
    artifacts = ArtifactRepository(app_url, content_codec=repository.content_codec)
    token_hash = hashlib.sha256(b"local-regression-output-token").digest()
    digest = hashlib.sha256(b"local-regression-pdf").digest()
    registered = {}
    agent_id = "marketing-bot" if outcome == "other_bot" else "hr-bot"

    def uploading(started, run):
        grant_id = uuid4()
        with psycopg.connect(app_url) as connection:
            connection.execute(
                "select platform_attachments.issue_task_grant_v64(%s,%s,%s,null,%s,'write_output',now()+interval '30 minutes',0,4096,2,2048)",
                (grant_id, token_hash, run.task_id, agent_id),
            )
        values = dict(token_sha256=token_hash, task_id=run.task_id, agent_id=agent_id,
                      artifact_key="interview", producer_version_id="v1", display_name="面试题.pdf",
                      declared_mime="application/pdf", declared_size=128, expected_sha256=digest)
        upload = artifacts.register(**values)
        registered.update(upload=upload, values=values)
        if claim_before_result:
            registered["attempt"] = artifacts.claim_write(token_sha256=token_hash, upload_id=upload.upload_id)
        if outcome == "revoked":
            with psycopg.connect(app_url) as connection:
                connection.execute("select platform_attachments.revoke_task_grant_v64(%s)", (grant_id,))
        return [{"attachmentId": str(upload.attachment_id), "artifactKey": "interview",
                 "producerVersionId": "v1", "displayName": "面试题.pdf", "status": "ready"}]

    started = _complete_collaboration(repository, owner, artifact_factory=uploading,
                                      status=outcome if outcome in {"failed", "cancelled"} else "completed",
                                      agent_id=agent_id)
    projector = ConversationProjection(repository)
    projector.project_terminal(started.mission.mission_id)
    upload = registered["upload"]
    if outcome != "completed":
        with psycopg.connect(environment["admin"]) as connection:
            assert connection.execute("select revoked_at is not null from platform_attachments.task_grants where token_sha256=%s", (token_hash,)).fetchone() == (True,)
        with pytest.raises(ArtifactUploadConflict):
            if claim_before_result:
                artifacts.finalize(token_sha256=token_hash, upload_id=upload.upload_id,
                                   attempt_id=registered["attempt"].attempt_id, declared_mime="application/pdf",
                                   size_bytes=128, sha256=digest)
            else:
                artifacts.claim_write(token_sha256=token_hash, upload_id=upload.upload_id)
        return
    assert repository.messages_after(owner, started.conversation.conversation_id)[-1].delivery_status == "completed"
    attempt = registered.get("attempt") or artifacts.claim_write(token_sha256=token_hash, upload_id=upload.upload_id)
    finalized = artifacts.finalize(token_sha256=token_hash, upload_id=upload.upload_id,
                                   attempt_id=attempt.attempt_id, declared_mime="application/pdf",
                                   size_bytes=128, sha256=digest)
    assert finalized.state == "validating"
    with psycopg.connect(environment["admin"]) as connection:
        assert connection.execute("select state from platform_attachments.processing_jobs where attachment_id=%s", (upload.attachment_id,)).fetchone() == ("queued",)
        assert connection.execute("select expires_at<=now()+interval '15 minutes' from platform_attachments.task_grants where token_sha256=%s", (token_hash,)).fetchone() == (True,)
    # Successful execution never reopens registration or unrelated uploads.
    with pytest.raises(ArtifactUploadConflict):
        artifacts.register(**{**registered["values"], "producer_version_id": "v2"})
    with pytest.raises(ArtifactUploadConflict):
        artifacts.claim_write(token_sha256=token_hash, upload_id=uuid4())
    with psycopg.connect(app_url) as connection, pytest.raises(psycopg.errors.CheckViolation, match="active task"):
        connection.execute(
            "select platform_attachments.issue_task_grant_v64(%s,%s,%s,null,'hr-bot','write_output',now()+interval '15 minutes',0,4096,2,2048)",
            (uuid4(), hashlib.sha256(b"new-grant-after-terminal").digest(), upload.task_id),
        )
