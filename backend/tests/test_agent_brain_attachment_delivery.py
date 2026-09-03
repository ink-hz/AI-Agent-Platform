from __future__ import annotations

import hashlib

# Imported fixture names intentionally become pytest fixtures in this module.
# ruff: noqa: F401,F811
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import psycopg
import pytest
from app.agent_brain.adapters.base import (
    AdapterCapabilities,
    AdapterDelivery,
    AdapterEvent,
    AdapterMessage,
    AdapterRegistry,
    AdapterTask,
    AgentAdapter,
    CancelReceipt,
    ChildSessionReceipt,
    DispatchReceipt,
    MessageDeliveryReceipt,
    StopDeliveryReceipt,
)
from app.agent_brain.loop_repository import AdapterSessionPoll, TaskDeliveryLease
from app.agent_brain.loop_runtime import BrainLoopRuntime
from app.attachments import grant_service as grant_service_module
from app.attachments.grant_service import (
    OutputWriteGrant,
    TaskAttachmentGrant,
    TaskGrantRepository,
)
from app.attachments.result_projection import ConversationResultProjection
from app.execution_relay.models import RequesterSubject
from psycopg.rows import dict_row
from test_agent_brain_loop_repository import (
    _codec,
    _commit,
    loop_database,
    loop_repository,
    seeded_loop,
)
from test_control_plane_migration import control_database
from test_conversation_attachment_migration import (
    _insert_artifact,
    _insert_attachment,
    _insert_task_output_binding,
    _seed_task,
)

TASK_ID = UUID("10000000-0000-4000-8000-000000000001")
LOOP_ID = UUID("10000000-0000-4000-8000-000000000002")
CONVERSATION_ID = UUID("10000000-0000-4000-8000-000000000003")
TURN_ID = UUID("10000000-0000-4000-8000-000000000004")
ATTACHMENT_ID = UUID("10000000-0000-4000-8000-000000000005")
NOW = datetime(2026, 9, 3, 10, 0, tzinfo=timezone.utc)


def test_task_grant_repository_accepts_brain_worker_dsn_purpose(monkeypatch) -> None:
    purposes = []
    monkeypatch.setattr(
        grant_service_module,
        "validate_control_dsn",
        lambda _url, *, purpose: purposes.append(purpose),
    )

    repository = TaskGrantRepository(
        "postgresql://brain-worker@control/agent_platform_control",
        content_codec=_codec(),
        dsn_purpose="brain",
    )

    assert "database_url=<redacted>" in repr(repository)
    assert purposes == ["brain"]


class _Collaboration:
    pass


class _Repository:
    def __init__(self, lease: TaskDeliveryLease) -> None:
        self.lease = lease
        self.bound = []
        self.dispatched = []

    def collaboration_repository(self):
        return _Collaboration()

    def lease_task_delivery(self, _worker_id, *, lease_seconds):
        assert lease_seconds == 45
        selected, self.lease = self.lease, None
        return selected

    def bind_adapter_session_ref(self, task_id, value):
        self.bound.append((task_id, value))

    def mark_delivery_dispatched(self, lease):
        self.dispatched.append(lease.delivery_id)


class _CapturingAdapter(AgentAdapter):
    supports_cancellation = True
    capabilities = AdapterCapabilities(
        supports_persistent_session=True,
        supports_followup_message=True,
        supports_progress_events=True,
        supports_thinking_summary=True,
        supports_cancel=True,
        supports_attachments=True,
        typical_latency_seconds=10,
    )

    def __init__(self) -> None:
        self.tasks = []

    def start_session(self, task: AdapterTask, delivery: AdapterDelivery):
        self.tasks.append(task)
        return ChildSessionReceipt(True, "child-session-0001", task.task_id)

    def send_message(
        self,
        child_session_id: str,
        message: AdapterMessage,
        delivery: AdapterDelivery,
        *,
        task: AdapterTask | None = None,
    ) -> MessageDeliveryReceipt:
        raise AssertionError("unexpected follow-up")

    def read_events(
        self,
        child_session_id: str,
        *,
        after: int,
        task: AdapterTask | None = None,
    ) -> tuple[AdapterEvent, ...]:
        return ()

    def request_stop(
        self,
        child_session_id: str,
        reason: str,
        delivery: AdapterDelivery,
        *,
        task: AdapterTask | None = None,
    ) -> StopDeliveryReceipt:
        raise AssertionError("unexpected stop")

    def dispatch(self, task: AdapterTask, delivery: AdapterDelivery) -> DispatchReceipt:
        raise AssertionError("unexpected legacy dispatch")

    def request_cancel(self, task: AdapterTask) -> CancelReceipt:
        return CancelReceipt(True)


class _Grants:
    def __init__(self) -> None:
        self.reads = []
        self.outputs = []

    def issue_attachment(self, task_id, attachment_id, agent_id, *, expires_at=None):
        self.reads.append((task_id, attachment_id, agent_id, expires_at))
        return TaskAttachmentGrant(
            attachment_id=attachment_id,
            display_name="candidate.pdf",
            detected_mime="application/pdf",
            size_bytes=1024,
            sha256_hex="a" * 64,
            download_url=(
                f"/api/v1/execution-worker/attachments/{attachment_id}/content"
            ),
            bearer_token="A" * 43,
            expires_at=expires_at,
        )

    def issue_output(self, task_id, agent_id, *, expires_at=None):
        self.outputs.append((task_id, agent_id, expires_at))
        return OutputWriteGrant(
            task_id=task_id,
            agent_id=agent_id,
            upload_url=f"/api/v1/execution-worker/tasks/{task_id}/artifacts",
            bearer_token="B" * 43,
            max_files=8,
            max_total_bytes=50 * 1024 * 1024,
        )


def _lease() -> TaskDeliveryLease:
    return TaskDeliveryLease(
        task_id=TASK_ID,
        loop_id=LOOP_ID,
        conversation_id=CONVERSATION_ID,
        turn_id=TURN_ID,
        agent_id="hr-bot",
        adapter_kind="metabot_local",
        capability_version=2,
        context={
            "objective": "分析候选人材料",
            "attachment_refs": [str(ATTACHMENT_ID)],
        },
        effective_deadline_at=NOW + timedelta(minutes=5),
        delivery_id=UUID("10000000-0000-4000-8000-000000000006"),
        attempt=1,
        idempotency_key="brain:task:initial:1",
        delivery_kind="initial",
        source_message_seq=None,
        child_session_id="child-session-0001",
        requester_subject=RequesterSubject(
            internal_user_id=UUID("10000000-0000-4000-8000-000000000007"),
            display_name="苍渊",
        ),
    )


def test_dispatch_issues_only_declared_task_attachment_grants_and_output_grant() -> (
    None
):
    repository = _Repository(_lease())
    adapter = _CapturingAdapter()
    adapters = AdapterRegistry()
    adapters.register("metabot_local", adapter)
    grants = _Grants()
    runtime = BrainLoopRuntime(
        repository=repository,
        model=None,
        request_builder=object(),
        system_prompt=object(),
        runtime_registry=object(),
        adapters=adapters,
        worker_id="test-worker",
        lease_seconds=45,
        attachment_grants=grants,
    )

    assert runtime.dispatch_one() is True

    task = adapter.tasks[0]
    assert [item.attachment_id for item in task.input_attachment_grants] == [
        ATTACHMENT_ID
    ]
    assert task.output_write_grant is not None
    assert task.output_write_grant.task_id == TASK_ID
    assert grants.reads == [
        (TASK_ID, ATTACHMENT_ID, "hr-bot", NOW + timedelta(minutes=5))
    ]
    assert grants.outputs == [(TASK_ID, "hr-bot", NOW + timedelta(minutes=5))]


@pytest.mark.parametrize(("state", "event_count"), (("pending", 0), ("ready", 1)))
def test_v2_reconcile_waits_for_registered_artifacts_before_terminal_event(
    state: str, event_count: int
) -> None:
    artifact_id = uuid4()

    class Collaboration:
        def __init__(self) -> None:
            self.events = []

        def append_task_event_and_wake(self, event):
            self.events.append(event)
            return type("Outcome", (), {"replayed": False})()

    class Repository:
        def __init__(self) -> None:
            self.collaboration = Collaboration()
            self.completed = []
            self.touched = []
            self.failed = []

        def collaboration_repository(self):
            return self.collaboration

        def next_adapter_session_poll(self):
            return AdapterSessionPoll(
                task_id=TASK_ID,
                loop_id=LOOP_ID,
                agent_id="hr-bot",
                adapter_kind="metabot_local",
                child_session_id="child-session-0001",
                after_event_seq=0,
                conversation_id=CONVERSATION_ID,
                turn_id=TURN_ID,
                capability_version=2,
                effective_deadline_at=NOW + timedelta(minutes=5),
            )

        def complete_initial_delivery_after_events(self, task_id):
            self.completed.append(task_id)

        def touch_adapter_session(self, task_id):
            self.touched.append(task_id)

        def fail_agent_task_protocol(self, task_id):
            self.failed.append(task_id)

    class Adapter(_CapturingAdapter):
        def read_events(self, child_session_id, *, after, task=None):
            assert child_session_id == "child-session-0001"
            assert after == 0
            return (
                AdapterEvent(
                    seq=1,
                    kind="result",
                    source="agent",
                    source_ref="run-1",
                    created_at=NOW,
                    payload={
                        "result": {
                            "contractVersion": "core_chat_collaboration_v4",
                            "publicAnswerMarkdown": "报告已生成。",
                            "citations": [],
                            "artifacts": [
                                {
                                    "attachmentId": str(artifact_id),
                                    "artifactKey": "candidate-report",
                                    "producerVersionId": "report-v1",
                                    "displayName": "candidate-report.pdf",
                                    "status": "ready",
                                }
                            ],
                            "completion": "completed",
                            "recovery": None,
                        }
                    },
                ),
            )

    class Grants:
        def classify_result_artifacts(self, task_id, agent_id, artifacts):
            assert (task_id, agent_id) == (TASK_ID, "hr-bot")
            assert artifacts[0]["attachmentId"] == str(artifact_id)
            return state

    repository = Repository()
    adapters = AdapterRegistry()
    adapters.register("metabot_local", Adapter())
    runtime = BrainLoopRuntime(
        repository=repository,
        model=None,
        request_builder=object(),
        system_prompt=object(),
        runtime_registry=object(),
        adapters=adapters,
        worker_id="test-worker",
        lease_seconds=45,
        attachment_grants=Grants(),
    )

    assert runtime.reconcile_one() is (state == "ready")
    assert len(repository.collaboration.events) == event_count
    assert repository.completed == ([TASK_ID] if state == "ready" else [])


@pytest.mark.postgres
def test_v2_task_can_atomically_bind_active_turn_attachment_and_issue_grant(
    loop_database, loop_repository, seeded_loop
) -> None:
    environment, _codec, owner_id, conversation_id, turn_id = loop_database
    loop_id, snapshot_id = seeded_loop
    lease = loop_repository.lease_step("brain-worker-a", lease_seconds=45)
    assert lease is not None
    task_id = loop_repository.commit_model_step(
        loop_id,
        lease.step_seq,
        "brain-worker-a",
        _commit(snapshot_id),
    ).task_ids[0]
    attachment_id = uuid4()
    token_digest = hashlib.sha256(b"v2-task-grant").digest()
    grant_id = uuid4()
    try:
        with psycopg.connect(environment["admin"]) as connection:
            connection.execute(
                "insert into platform_attachments.attachments ("
                "attachment_id,owner_internal_user_id,conversation_id,source_kind,"
                "original_name_ciphertext,original_name_key_version,"
                "object_ref_ciphertext,object_ref_key_version,detected_mime,size_bytes,"
                "sha256,state,ready_at) values (%s,%s,%s,'user_input',%s,1,%s,1,"
                "'application/pdf',128,%s,'ready',now())",
                (
                    attachment_id,
                    owner_id,
                    conversation_id,
                    b"n" * 29,
                    b"r" * 29,
                    b"h" * 32,
                ),
            )
            connection.execute(
                "insert into platform_attachments.bindings ("
                "binding_id,attachment_id,owner_internal_user_id,kind,"
                "conversation_id,turn_id) values (%s,%s,%s,'turn_input',%s,%s)",
                (uuid4(), attachment_id, owner_id, conversation_id, turn_id),
            )

        with psycopg.connect(environment["admin"]) as connection:
            assert connection.execute(
                "select task_status,owner_internal_user_id,conversation_id "
                "from platform_attachments.task_context_v64(%s,'hr-bot')",
                (task_id,),
            ).fetchone() == ("queued", owner_id, conversation_id)

        with psycopg.connect(
            environment["urls"]["platform_brain_worker"]
        ) as connection:
            issued = connection.execute(
                "select platform_attachments.issue_task_grant_v64("
                "%s,%s,%s,%s,'hr-bot','read',"
                "clock_timestamp()+interval '5 minutes',1,128,0,0)",
                (grant_id, token_digest, task_id, attachment_id),
            ).fetchone()
        assert issued == (grant_id,)

        with psycopg.connect(environment["admin"]) as connection:
            binding = connection.execute(
                "select task_id,agent_id from platform_attachments.bindings "
                "where attachment_id=%s and kind='task_input'",
                (attachment_id,),
            ).fetchone()
        assert binding == (task_id, "hr-bot")

        with psycopg.connect(environment["admin"]) as connection:
            connection.execute(
                "update platform_brain.agent_tasks set status='completed',"
                "terminal_at=clock_timestamp() where task_id=%s",
                (task_id,),
            )
            assert connection.execute(
                "select revoked_at is not null from platform_attachments.task_grants "
                "where grant_id=%s",
                (grant_id,),
            ).fetchone() == (True,)
    finally:
        with psycopg.connect(environment["admin"]) as connection:
            connection.execute(
                "delete from platform_attachments.access_events where attachment_id=%s",
                (attachment_id,),
            )
            connection.execute(
                "delete from platform_attachments.task_grants where attachment_id=%s",
                (attachment_id,),
            )
            connection.execute(
                "delete from platform_attachments.bindings where attachment_id=%s",
                (attachment_id,),
            )
            connection.execute(
                "delete from platform_attachments.attachments where attachment_id=%s",
                (attachment_id,),
            )


@pytest.mark.postgres
def test_v4_result_projection_atomically_records_citations_and_message_outputs(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    message_id = uuid4()
    with psycopg.connect(environment["admin"]) as admin:
        context = _seed_task(admin, agent_id="hr-bot")
        artifact_id = _insert_artifact(admin, context, "candidate-report")
        attachment_id = _insert_attachment(admin, context, source_kind="agent_output")
        _insert_task_output_binding(admin, context, attachment_id)
        admin.execute(
            "update platform_attachments.attachments set "
            "immutable_locator='version:artifact-v1' where attachment_id=%s",
            (attachment_id,),
        )
        admin.execute(
            "insert into platform_attachments.artifact_versions("
            "artifact_version_id,artifact_id,attachment_id,version_no,"
            "producer_version_id,original_name_ciphertext,original_name_key_version,"
            "object_ref_ciphertext,object_ref_key_version,size_bytes,state,result_status) "
            "values (%s,%s,%s,1,'report-v1',%s,1,%s,1,128,'ready','succeeded')",
            (uuid4(), artifact_id, attachment_id, b"n" * 29, b"r" * 29),
        )
        admin.execute("set constraints all deferred")
        admin.execute(
            "insert into platform_control.conversation_messages("
            "message_id,conversation_id,seq,role,content_ciphertext,"
            "encryption_key_version,turn_id,mission_id,delivery_status,completed_at) "
            "values (%s,%s,2,'assistant',%s,1,%s,%s,'completed',now())",
            (
                message_id,
                context["conversation_id"],
                b"m" * 29,
                context["turn_id"],
                context["mission_id"],
            ),
        )
        admin.commit()

    projector = ConversationResultProjection(content_codec=_codec())
    with (
        psycopg.connect(
            environment["urls"]["platform_control_app"], row_factory=dict_row
        ) as connection,
        connection.transaction(),
    ):
        projector.project_locked(
            connection,
            owner_id=context["owner_id"],
            conversation_id=context["conversation_id"],
            message_id=message_id,
            task_id=context["task_id"],
            agent_id="hr-bot",
            collaboration={
                "contract_version": "core_chat_collaboration_v4",
                "citations": [
                    {
                        "citationKey": "candidate-profile",
                        "title": "候选人公开项目",
                        "url": "https://example.com/profile",
                        "site": "example.com",
                        "retrievedAt": NOW.isoformat(),
                        "supports": ["视觉算法经验"],
                    }
                ],
                "artifacts": [
                    {
                        "attachmentId": str(attachment_id),
                        "artifactKey": "candidate-report",
                        "producerVersionId": "report-v1",
                        "displayName": "candidate-report.pdf",
                        "status": "ready",
                    }
                ],
                "completion": "completed",
                "recovery": None,
            },
        )

    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select citation_key from platform_attachments.message_citations "
            "where message_id=%s",
            (message_id,),
        ).fetchone() == ("candidate-profile",)
        assert admin.execute(
            "select attachment_id,task_id,agent_id from "
            "platform_attachments.bindings where message_id=%s "
            "and kind='message_output'",
            (message_id,),
        ).fetchone() == (attachment_id, None, "hr-bot")


@pytest.mark.postgres
def test_brain_answer_can_reference_only_ready_artifacts_from_its_own_loop(
    loop_database, loop_repository, seeded_loop
) -> None:
    environment, _codec_value, owner_id, conversation_id, turn_id = loop_database
    loop_id, snapshot_id = seeded_loop
    lease = loop_repository.lease_step("brain-worker-a", lease_seconds=45)
    assert lease is not None
    task_id = loop_repository.commit_model_step(
        loop_id,
        lease.step_seq,
        "brain-worker-a",
        _commit(snapshot_id),
    ).task_ids[0]
    context = {
        "owner_id": owner_id,
        "conversation_id": conversation_id,
        "turn_id": turn_id,
        "task_id": task_id,
        "agent_id": "hr-bot",
    }
    message_id = uuid4()
    with psycopg.connect(environment["admin"]) as admin:
        artifact_id = _insert_artifact(admin, context, "candidate-report")
        attachment_id = _insert_attachment(admin, context, source_kind="agent_output")
        _insert_task_output_binding(admin, context, attachment_id)
        admin.execute(
            "update platform_attachments.attachments set "
            "immutable_locator='version:artifact-v1' where attachment_id=%s",
            (attachment_id,),
        )
        admin.execute(
            "insert into platform_attachments.artifact_versions("
            "artifact_version_id,artifact_id,attachment_id,version_no,"
            "producer_version_id,original_name_ciphertext,original_name_key_version,"
            "object_ref_ciphertext,object_ref_key_version,size_bytes,state,result_status) "
            "values (%s,%s,%s,1,'report-v1',%s,1,%s,1,128,'ready','succeeded')",
            (uuid4(), artifact_id, attachment_id, b"n" * 29, b"r" * 29),
        )
        admin.execute("set constraints all deferred")
        admin.execute(
            "insert into platform_control.conversation_messages("
            "message_id,conversation_id,seq,role,content_ciphertext,"
            "encryption_key_version,turn_id,delivery_status,completed_at) "
            "values (%s,%s,2,'assistant',%s,1,%s,'completed',now())",
            (message_id, conversation_id, b"m" * 29, turn_id),
        )
        admin.commit()

    assert loop_repository.ready_artifact_ids_for_loop(loop_id) == (attachment_id,)
    with psycopg.connect(environment["urls"]["platform_brain_worker"]) as brain:
        assert brain.execute(
            "select platform_attachments.bind_brain_answer_artifacts_v64(%s,%s,%s)",
            (loop_id, message_id, [attachment_id]),
        ).fetchone() == (1,)
        brain.commit()
    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select attachment_id,agent_id from platform_attachments.bindings "
            "where message_id=%s and kind='message_output'",
            (message_id,),
        ).fetchone() == (attachment_id, "hr-bot")
