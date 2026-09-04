from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest
from app.agent_brain.conversation_repository import message_subject
from app.agent_brain.conversation_service import ConversationCommandService
from app.hr.context import HrPositionScope
from app.hr.models import CreateManualPosition
from app.hr.position_intelligence_repository import PositionIntelligenceRepository
from app.hr.position_intelligence_service import PositionIntelligenceService
from app.hr.repository import HrPositionRepository
from app.hr.task_repository import PostgresHrPositionTaskRepository
from app.hr.task_result_projection import (
    HrTaskResultProjectionRepository,
    HrTaskResultReconciler,
)
from app.hr.task_service import HrPositionTaskService
from test_agent_brain_conversation_repository import (
    _codec,
    conversation_database,  # noqa: F401
    repository,  # noqa: F401
)
from test_control_plane_migration import control_database  # noqa: F401


class _UnusedCandidates:
    def add_analysis(self, _command):
        raise AssertionError("candidate projection was not expected")


def _finish_task(
    environment,
    task,
    owner_id,
    position_id,
    task_kind,
    value,
    *,
    model_version="hr-runtime-before-upgrade",
) -> None:
    codec = _codec()
    message_id = uuid4()
    task_record_id = uuid4()
    mission_task_id = uuid4()
    run_id = uuid4()
    worker_id = f"projection-test-{str(task_record_id)[:8]}"
    sealed = codec.seal_json(message_subject(task.conversation_id, message_id), value)
    with psycopg.connect(environment["admin"]) as connection:
        mission_id = connection.execute(
            "select mission_id from platform_control.conversation_turns "
            "where conversation_id=%s and turn_id=%s",
            (task.conversation_id, task.turn_id),
        ).fetchone()[0]
        connection.execute(
            "insert into platform_hr.position_task_records("
            "task_record_id,owner_internal_user_id,position_id,client_request_id,"
            "task_kind,material_attachment_ids,document_attachment_ids,"
            "human_feedback_ids,conversation_id,turn_id,prompt_context,canonical_sha256,"
            "execution_model_version) "
            "select %s,%s,%s,client_request_id,%s,'{}','{}','{}',%s,%s,%s,%s,%s "
            "from platform_hr.position_task_requests where task_request_id=%s",
            (
                task_record_id,
                owner_id,
                position_id,
                task_kind,
                task.conversation_id,
                task.turn_id,
                "projection database test",
                "0" * 64,
                model_version,
                task.task_id,
            ),
        )
        connection.execute(
            "update platform_hr.position_task_requests set status='consumed' "
            "where task_request_id=%s",
            (task.task_id,),
        )
        connection.execute(
            "insert into platform_control.execution_workers("
            "worker_id,allowed_agent_ids,status) values (%s,array['hr-bot'],'active')",
            (worker_id,),
        )
        connection.execute(
            "insert into platform_control.mission_tasks("
            "task_id,mission_id,agent_id,objective_ciphertext,encryption_key_version,"
            "status,started_at,terminal_at) values (%s,%s,'hr-bot',%s,1,'completed',now(),now())",
            (mission_task_id, mission_id, b"o" * 29),
        )
        connection.execute(
            "insert into platform_control.mission_runs("
            "run_id,mission_id,task_id,phase,agent_id,status,input_ciphertext,"
            "encryption_key_version,started_at,terminal_at) "
            "values (%s,%s,%s,'direct','hr-bot','completed',%s,1,now(),now())",
            (run_id, mission_id, mission_task_id, b"i" * 29),
        )
        connection.execute(
            "insert into platform_control.execution_jobs("
            "job_id,run_id,agent_id,payload_ciphertext,encryption_key_version,status,"
            "lease_worker_id,terminal_at) values (%s,%s,'hr-bot',%s,1,'completed',%s,now())",
            (uuid4(), run_id, b"p" * 29, worker_id),
        )
        connection.execute(
            "insert into platform_control.conversation_messages("
            "message_id,conversation_id,seq,role,content_ciphertext,"
            "encryption_key_version,turn_id,mission_id,delivery_status,completed_at) "
            "select %s,%s,max(seq)+1,'assistant',%s,%s,%s,%s,'completed',now() "
            "from platform_control.conversation_messages where conversation_id=%s",
            (
                message_id,
                task.conversation_id,
                sealed.ciphertext,
                sealed.key_version,
                task.turn_id,
                mission_id,
                task.conversation_id,
            ),
        )
        connection.execute(
            "update platform_control.conversation_turns set "
            "assistant_message_id=%s,status='completed',updated_at=now() "
            "where turn_id=%s",
            (message_id, task.turn_id),
        )
        connection.execute(
            "update platform_control.missions set status='completed',"
            "terminal_at=now(),updated_at=now() where mission_id=%s",
            (mission_id,),
        )


@pytest.mark.postgres
def test_projection_is_durable_idempotent_and_bad_result_does_not_block(
    conversation_database,  # noqa: F811
    repository,  # noqa: F811
    request,
) -> None:
    environment, owner_id, _ = conversation_database
    positions = HrPositionRepository(environment["urls"]["platform_control_app"])
    position = positions.create_manual(
        CreateManualPosition(owner_id, uuid4(), uuid4(), "投影测试岗位")
    )

    def cleanup() -> None:
        with psycopg.connect(environment["admin"]) as connection:
            connection.execute(
                "delete from platform_hr.hr_task_result_projections "
                "where owner_internal_user_id=%s and position_id=%s",
                (owner_id, position.position_id),
            )
            connection.execute(
                "delete from platform_hr.position_task_records "
                "where owner_internal_user_id=%s and position_id=%s",
                (owner_id, position.position_id),
            )
            connection.execute(
                "delete from platform_hr.position_task_requests "
                "where owner_internal_user_id=%s and position_id=%s",
                (owner_id, position.position_id),
            )
            connection.execute(
                "delete from platform_hr.position_context_versions "
                "where owner_internal_user_id=%s and position_id=%s",
                (owner_id, position.position_id),
            )
            connection.execute(
                "delete from platform_hr.position_binding_events "
                "where owner_internal_user_id=%s",
                (owner_id,),
            )
            connection.execute(
                "delete from platform_hr.position_conversations "
                "where owner_internal_user_id=%s and position_id=%s",
                (owner_id, position.position_id),
            )
            connection.execute(
                "delete from platform_hr.positions "
                "where owner_internal_user_id=%s and position_id=%s",
                (owner_id, position.position_id),
            )

    request.addfinalizer(cleanup)
    intelligence = PositionIntelligenceService(
        PositionIntelligenceRepository(environment["urls"]["platform_control_app"])
    )
    tasks = HrPositionTaskService(
        intelligence,
        ConversationCommandService(repository, v2_enabled=True),
        HrPositionScope(positions),
        PostgresHrPositionTaskRepository(environment["urls"]["platform_control_app"]),
    )
    contended = tasks.start(
        owner_id=owner_id,
        position_id=position.position_id,
        request_id=uuid4(),
        task_kind="talent_profile",
        context_version_id=None,
        material_ids=(),
        conversation_id=None,
        candidate_id=None,
        position_candidate_id=None,
    )
    _finish_task(
        environment,
        contended,
        owner_id,
        position.position_id,
        "talent_profile",
        {"text": "并发结果"},
    )
    gate = Barrier(2)

    def claim(worker_id):
        gate.wait()
        return HrTaskResultProjectionRepository(
            environment["urls"]["platform_control_app"]
        ).claim(worker_id, 300)

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = tuple(pool.map(claim, ("projection-a", "projection-b")))
    assert sum(item is not None for item in claims) == 1

    bad = tasks.start(
        owner_id=owner_id,
        position_id=position.position_id,
        request_id=uuid4(),
        task_kind="jr",
        context_version_id=None,
        material_ids=(),
        conversation_id=None,
        candidate_id=None,
        position_candidate_id=None,
    )
    good = tasks.start(
        owner_id=owner_id,
        position_id=position.position_id,
        request_id=uuid4(),
        task_kind="jd",
        context_version_id=None,
        material_ids=(),
        conversation_id=None,
        candidate_id=None,
        position_candidate_id=None,
    )
    _finish_task(environment, bad, owner_id, position.position_id, "jr", {"text": " "})
    _finish_task(
        environment, good, owner_id, position.position_id, "jd", {"text": "真实 JD"}
    )
    reconciler = HrTaskResultReconciler(
        HrTaskResultProjectionRepository(environment["urls"]["platform_control_app"]),
        intelligence,
        _UnusedCandidates(),
        _codec(),
        worker_id="projection-test",
    )
    assert reconciler.reconcile_one() is True
    assert reconciler.reconcile_one() is True
    assert reconciler.reconcile_one() is False
    assert tasks.get(owner_id, position.position_id, bad.task_id).error == (
        "result_projection_failed"
    )
    assert tasks.get(owner_id, position.position_id, good.task_id).status == "completed"
    drafts = intelligence.drafts(owner_id, position.position_id)
    assert len(drafts) == 1
    assert drafts[0].modules == {"jd": {"text": "真实 JD"}}
    assert drafts[0].model_version == "hr-runtime-before-upgrade"
