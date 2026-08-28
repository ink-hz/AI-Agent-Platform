from __future__ import annotations

# Pytest fixtures are imported into this module's namespace for discovery.
# ruff: noqa: F401,F811
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4, uuid5

import psycopg
import pytest
from app.agent_brain.action_service import ActionCommandConflict, ActionCommandService
from app.agent_brain.adapters.base import AdapterDelivery, AdapterRegistry, AdapterTask
from app.agent_brain.adapters.voc import VocBrainAdapter
from app.agent_brain.worker_runtime import register_voc_action_adapter
from app.execution_relay.models import RequesterSubject
from test_agent_brain_live_repository import live_database, seeded_live_task
from test_control_plane_migration import control_database


class DurableFakeVoc:
    def __init__(self) -> None:
        self._drafts: dict[UUID, dict[str, object]] = {}
        self._submissions: dict[UUID, dict[str, object]] = {}
        self.create_effects = 0
        self.submit_effects = 0

    def create_draft(
        self, *, actor_id: UUID, request_id: UUID, source_text: str
    ) -> dict[str, object]:
        if request_id not in self._drafts:
            self.create_effects += 1
            self._drafts[request_id] = {
                "draft_id": uuid5(request_id, "voc-draft"),
                "version": 1,
                "feedback": source_text,
                "actor_id": actor_id,
            }
        return dict(self._drafts[request_id])

    def submit_draft(
        self,
        *,
        actor_id: UUID,
        draft_id: UUID,
        request_id: UUID,
        expected_version: int,
    ) -> dict[str, object]:
        if request_id not in self._submissions:
            self.submit_effects += 1
            self._submissions[request_id] = {
                "voc_no": "VOC-20260828-001",
                "revision": 1,
                "already_submitted": False,
                "draft_id": str(draft_id),
                "expected_version": expected_version,
                "actor_id": str(actor_id),
            }
        return dict(self._submissions[request_id])


def _task(task_id, loop_id, owner_id) -> AdapterTask:
    return AdapterTask(
        task_id=task_id,
        loop_id=loop_id,
        agent_id="voc",
        context={
            "objective": "客户反馈设备连续运行后发热，请整理为 VOC 草稿",
            "context_excerpt": ["客户现场连续运行三小时"],
        },
        effective_deadline_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        requester_subject=RequesterSubject(
            internal_user_id=owner_id,
            display_name="苍渊",
        ),
    )


def _delivery(task_id) -> AdapterDelivery:
    return AdapterDelivery(
        delivery_id=uuid5(task_id, "voc-delivery"),
        attempt=1,
        idempotency_key=f"voc-task:{task_id}",
    )


def test_worker_registers_voc_only_with_complete_private_configuration(
    tmp_path,
) -> None:
    signing_key = tmp_path / "voc-signing-key"
    signing_key.write_bytes(b"v" * 32)
    signing_key.chmod(0o600)
    actions = object.__new__(ActionCommandService)
    adapters = AdapterRegistry()

    client = register_voc_action_adapter(
        adapters,
        actions,
        environ={
            "PLATFORM_VOC_EXTENSION_BASE_URL": "http://172.29.0.3:18130",
            "PLATFORM_VOC_EXTENSION_SIGNING_KEY_FILE": str(signing_key),
            "PLATFORM_VOC_EXTENSION_TIMEOUT_SECONDS": "10",
        },
    )

    assert client is not None
    assert adapters.registered_kinds == ("voc_action",)
    assert isinstance(adapters.require("voc_action"), VocBrainAdapter)
    client.close()


@pytest.mark.postgres
def test_voc_action_survives_restarts_and_submits_exactly_once(
    live_database,
    seeded_live_task,
) -> None:
    environment, codec, owner_id, _conversation_id, _turn_id = live_database
    _collaboration, _loops, loop_id, task_id, _ = seeded_live_task
    worker_actions = ActionCommandService(
        environment["urls"]["platform_brain_worker"],
        content_codec=codec,
        dsn_purpose="brain",
    )
    app_actions = ActionCommandService(
        environment["urls"]["platform_control_app"],
        content_codec=codec,
        dsn_purpose="app",
    )
    voc = DurableFakeVoc()
    task = _task(task_id, loop_id, owner_id)
    delivery = _delivery(task_id)

    first_process = VocBrainAdapter(voc, worker_actions)
    first_receipt = first_process.start_session(task, delivery)
    second_process = VocBrainAdapter(voc, worker_actions)
    replayed_receipt = second_process.start_session(task, delivery)

    assert replayed_receipt == first_receipt
    assert voc.create_effects == 1
    pending_events = second_process.read_events(
        replayed_receipt.child_session_id, after=0
    )
    assert [event.kind for event in pending_events] == [
        "work_update",
        "action_required",
    ]
    action = worker_actions.for_task(task_id)
    assert action is not None and action.projection.status == "pending"

    with pytest.raises(ActionCommandConflict):
        app_actions.confirm(
            owner_id,
            action.projection.action_id,
            "0" * 64,
        )
    first = app_actions.confirm(
        owner_id, action.projection.action_id, action.projection.action_digest
    )
    second = app_actions.confirm(
        owner_id, action.projection.action_id, action.projection.action_digest
    )
    assert first == second

    after_confirm_restart = VocBrainAdapter(voc, worker_actions)
    terminal_events = after_confirm_restart.read_events(
        replayed_receipt.child_session_id, after=2
    )
    after_submit_restart = VocBrainAdapter(voc, worker_actions)
    replayed_terminal = after_submit_restart.read_events(
        replayed_receipt.child_session_id, after=2
    )

    assert [event.kind for event in terminal_events] == ["result"]
    assert terminal_events == replayed_terminal
    assert terminal_events[0].payload["status"] == "completed"
    assert terminal_events[0].payload["deliverables"] == ["VOC-20260828-001"]
    assert voc.submit_effects == 1
    completed = worker_actions.for_task(task_id)
    assert completed is not None
    assert completed.projection.execution_status == "completed"


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("decision", "expected_kind", "expected_status"),
    (
        ("reject", "cancelled", "cancelled"),
        ("expire", "timeout", "timed_out"),
        ("supersede", "cancelled", "cancelled"),
    ),
)
def test_voc_action_non_confirmation_terminalizes_without_submit(
    live_database,
    seeded_live_task,
    decision,
    expected_kind,
    expected_status,
) -> None:
    environment, codec, owner_id, _conversation_id, _turn_id = live_database
    _collaboration, _loops, loop_id, task_id, _ = seeded_live_task
    worker_actions = ActionCommandService(
        environment["urls"]["platform_brain_worker"],
        content_codec=codec,
        dsn_purpose="brain",
    )
    app_actions = ActionCommandService(
        environment["urls"]["platform_control_app"],
        content_codec=codec,
        dsn_purpose="app",
    )
    voc = DurableFakeVoc()
    adapter = VocBrainAdapter(voc, worker_actions)
    receipt = adapter.start_session(
        _task(task_id, loop_id, owner_id), _delivery(task_id)
    )
    action = worker_actions.for_task(task_id)
    assert action is not None

    if decision == "reject":
        app_actions.reject(owner_id, action.projection.action_id)
    elif decision == "expire":
        with psycopg.connect(environment["admin"]) as connection:
            connection.execute(
                "update platform_brain.agent_task_actions set "
                "created_at=clock_timestamp()-interval '2 minutes',"
                "expires_at=clock_timestamp()-interval '1 minute' "
                "where action_id=%s",
                (action.projection.action_id,),
            )
        assert worker_actions.expire(limit=10) == 1
    else:
        worker_actions.supersede(action.projection.action_id)
    events = adapter.read_events(receipt.child_session_id, after=2)

    assert [event.kind for event in events] == [expected_kind]
    assert events[0].payload["status"] == expected_status
    assert voc.submit_effects == 0
