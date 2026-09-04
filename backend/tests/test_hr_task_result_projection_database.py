from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest
from test_agent_brain_conversation_repository import (
    _codec,
    conversation_database,  # noqa: F401
    repository,  # noqa: F401
)
from test_control_plane_migration import control_database  # noqa: F401

from app.agent_brain.conversation_repository import message_subject
from app.agent_brain.conversation_service import ConversationCommandService
from app.hr.candidate_repository import CandidateRepository
from app.hr.candidate_service import CandidateService
from app.hr.context import HrPositionScope
from app.hr.models import CreateManualPosition
from app.hr.position_intelligence_repository import PositionIntelligenceRepository
from app.hr.position_intelligence_service import PositionIntelligenceService
from app.hr.repository import HrPositionRepository
from app.hr.structured_output import encode_hr_envelope
from app.hr.task_repository import PostgresHrPositionTaskRepository
from app.hr.task_result_projection import (
    HrTaskResultProjectionRepository,
    HrTaskResultReconciler,
)
from app.hr.task_service import HrPositionTaskService


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
    candidate_scope=None,
    artifact_specs=(),
    grant_agent_id="hr-bot",
):
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
            "task_kind,context_version_id,candidate_id,position_candidate_id,"
            "material_attachment_ids,document_attachment_ids,"
            "human_feedback_ids,conversation_id,turn_id,prompt_context,canonical_sha256,"
            "execution_model_version) "
            "select %s,%s,%s,client_request_id,%s,%s,%s,%s,'{}',%s,'{}',%s,%s,%s,%s,%s "
            "from platform_hr.position_task_requests where task_request_id=%s",
            (
                task_record_id,
                owner_id,
                position_id,
                task_kind,
                candidate_scope["context"] if candidate_scope else None,
                candidate_scope["candidate"] if candidate_scope else None,
                candidate_scope["relation"] if candidate_scope else None,
                [candidate_scope["attachment"]] if candidate_scope else [],
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
            "status,started_at) values (%s,%s,'hr-bot',%s,1,'running',now())",
            (mission_task_id, mission_id, b"o" * 29),
        )
        connection.execute(
            "insert into platform_control.mission_runs("
            "run_id,mission_id,task_id,phase,agent_id,status,input_ciphertext,"
            "encryption_key_version,started_at) "
            "values (%s,%s,%s,'direct','hr-bot','running',%s,1,now())",
            (run_id, mission_id, mission_task_id, b"i" * 29),
        )
        connection.execute(
            "insert into platform_control.execution_jobs("
            "job_id,run_id,agent_id,payload_ciphertext,encryption_key_version,status,"
            "lease_worker_id) values (%s,%s,'hr-bot',%s,1,'running',%s)",
            (uuid4(), run_id, b"p" * 29, worker_id),
        )
        if grant_agent_id is not None:
            connection.execute(
                "insert into platform_attachments.task_grants("
                "grant_id,token_sha256,task_id,agent_id,scope,expires_at,max_reads,"
                "max_bytes,max_files,max_file_bytes) values ("
                "%s,%s,%s,%s,'write_output',now()+interval '1 hour',0,"
                "1048576,5,1048576)",
                (
                    uuid4(), uuid4().bytes + uuid4().bytes, mission_task_id,
                    grant_agent_id,
                ),
            )
        artifact_versions = []
        for index, spec in enumerate(artifact_specs, start=1):
            artifact_id, attachment_id, artifact_version_id = uuid4(), uuid4(), uuid4()
            artifact_owner = spec.get("artifact_owner_id", owner_id)
            attachment_owner = spec.get("attachment_owner_id", owner_id)
            artifact_conversation = spec.get("conversation_id", task.conversation_id)
            artifact_task = spec.get("task_id", mission_task_id)
            attachment_state = spec.get("state", "ready")
            mime = spec.get("mime", "application/pdf")
            connection.execute(
                "insert into platform_attachments.attachments("
                "attachment_id,owner_internal_user_id,conversation_id,source_kind,"
                "original_name_ciphertext,original_name_key_version,object_ref_ciphertext,"
                "object_ref_key_version,declared_mime,detected_mime,immutable_locator,"
                "size_bytes,sha256,state,ready_at) values ("
                "%s,%s,%s,'agent_output',%s,1,%s,1,%s,%s,%s,128,%s,%s,"
                "case when %s='ready' then now() end)",
                (
                    attachment_id, attachment_owner, artifact_conversation,
                    b"n" * 29, b"o" * 29, mime, mime,
                    f"version:interview-{artifact_version_id}", b"a" * 32,
                    attachment_state, attachment_state,
                ),
            )
            if spec.get("bypass_artifact_integrity"):
                connection.execute(
                    "alter table platform_attachments.artifacts disable trigger all"
                )
            elif spec.get("bypass_context"):
                connection.execute(
                    "alter table platform_attachments.artifacts disable trigger "
                    "enforce_artifact_task_context_v64"
                )
            connection.execute(
                "insert into platform_attachments.artifacts("
                "artifact_id,artifact_key,owner_internal_user_id,conversation_id,"
                "task_id,agent_id) values (%s,%s,%s,%s,%s,'hr-bot')",
                (
                    artifact_id, f"interview-{index}", artifact_owner,
                    artifact_conversation, artifact_task,
                ),
            )
            if spec.get("bypass_artifact_integrity"):
                connection.execute(
                    "alter table platform_attachments.artifacts enable trigger all"
                )
            elif spec.get("bypass_context"):
                connection.execute(
                    "alter table platform_attachments.artifacts enable trigger "
                    "enforce_artifact_task_context_v64"
                )
            for prior_version_no, prior_state in enumerate(
                spec.get("prior_states", ()), start=1
            ):
                prior_attachment_id = uuid4()
                prior_artifact_version_id = uuid4()
                prior_result_status = (
                    "succeeded" if prior_state == "ready" else "pending"
                )
                connection.execute(
                    "insert into platform_attachments.attachments("
                    "attachment_id,owner_internal_user_id,conversation_id,source_kind,"
                    "original_name_ciphertext,original_name_key_version,"
                    "object_ref_ciphertext,object_ref_key_version,declared_mime,"
                    "detected_mime,immutable_locator,size_bytes,sha256,state,ready_at) "
                    "values (%s,%s,%s,'agent_output',%s,1,%s,1,%s,%s,%s,128,%s,%s,"
                    "case when %s='ready' then now() end)",
                    (
                        prior_attachment_id, attachment_owner, artifact_conversation,
                        b"n" * 29, b"o" * 29, mime, mime,
                        f"version:prior-{prior_artifact_version_id}", b"p" * 32,
                        prior_state, prior_state,
                    ),
                )
                connection.execute(
                    "insert into platform_attachments.bindings("
                    "binding_id,attachment_id,owner_internal_user_id,kind,"
                    "conversation_id,task_id,agent_id) values ("
                    "%s,%s,%s,'task_output',%s,%s,'hr-bot')",
                    (
                        uuid4(), prior_attachment_id, attachment_owner,
                        artifact_conversation, artifact_task,
                    ),
                )
                connection.execute(
                    "insert into platform_attachments.artifact_versions("
                    "artifact_version_id,artifact_id,attachment_id,version_no,"
                    "producer_version_id,original_name_ciphertext,"
                    "original_name_key_version,object_ref_ciphertext,"
                    "object_ref_key_version,detected_mime,immutable_locator,size_bytes,"
                    "sha256,state,result_status) values ("
                    "%s,%s,%s,%s,%s,%s,1,%s,1,%s,%s,128,%s,%s,%s)",
                    (
                        prior_artifact_version_id, artifact_id, prior_attachment_id,
                        prior_version_no, f"interview-prior-v{prior_version_no}",
                        b"n" * 29, b"o" * 29, mime,
                        f"version:prior-{prior_artifact_version_id}", b"p" * 32,
                        prior_state, prior_result_status,
                    ),
                )
            if spec.get("bypass_context"):
                connection.execute(
                    "alter table platform_attachments.bindings disable trigger "
                    "enforce_binding_task_context_v64"
                )
            connection.execute(
                "insert into platform_attachments.bindings("
                "binding_id,attachment_id,owner_internal_user_id,kind,conversation_id,"
                "task_id,agent_id) values (%s,%s,%s,'task_output',%s,%s,'hr-bot')",
                (
                    uuid4(), attachment_id, attachment_owner,
                    artifact_conversation, artifact_task,
                ),
            )
            if spec.get("bypass_context"):
                connection.execute(
                    "alter table platform_attachments.bindings enable trigger "
                    "enforce_binding_task_context_v64"
                )
            version_state = "ready" if attachment_state == "ready" else "scanning"
            result_status = "succeeded" if attachment_state == "ready" else "pending"
            connection.execute(
                "insert into platform_attachments.artifact_versions("
                "artifact_version_id,artifact_id,attachment_id,version_no,"
                "producer_version_id,original_name_ciphertext,original_name_key_version,"
                "object_ref_ciphertext,object_ref_key_version,detected_mime,"
                "immutable_locator,size_bytes,sha256,state,result_status) values ("
                "%s,%s,%s,%s,%s,%s,1,%s,1,%s,%s,128,%s,%s,%s)",
                (
                    artifact_version_id, artifact_id, attachment_id,
                    len(spec.get("prior_states", ())) + 1,
                    f"interview-v{index}", b"n" * 29, b"o" * 29, mime,
                    f"version:interview-{artifact_version_id}", b"a" * 32,
                    version_state, result_status,
                ),
            )
            if spec.get("expired"):
                connection.execute(
                    "update platform_attachments.attachments "
                    "set retained_until=now()-interval '1 second' "
                    "where attachment_id=%s",
                    (attachment_id,),
                )
                connection.execute(
                    "update platform_attachments.artifact_versions "
                    "set retained_until=now()-interval '1 second' "
                    "where artifact_version_id=%s",
                    (artifact_version_id,),
                )
            if spec.get("erasure"):
                connection.execute(
                    "insert into platform_attachments.erasure_jobs("
                    "erasure_job_id,attachment_id,requested_by_internal_user_id,"
                    "reason_ciphertext,reason_key_version,reason_sha256) values ("
                    "%s,%s,%s,%s,1,%s)",
                    (
                        uuid4(), attachment_id, attachment_owner,
                        b"r" * 29, b"e" * 32,
                    ),
                )
            artifact_versions.append(artifact_version_id)
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
            "update platform_control.execution_jobs set status='completed',terminal_at=now() "
            "where run_id=%s", (run_id,),
        )
        connection.execute(
            "update platform_control.mission_runs set status='completed',terminal_at=now() "
            "where run_id=%s", (run_id,),
        )
        connection.execute(
            "update platform_control.mission_tasks set status='completed',terminal_at=now() "
            "where task_id=%s", (mission_task_id,),
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
    return mission_task_id, tuple(artifact_versions)


def _seed_candidate_scope(environment, owner_id, position_id):
    ids = {name: uuid4() for name in (
        "context", "attachment", "batch", "draft", "candidate", "document",
        "relation",
    )}
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_hr.position_context_versions("
            "context_version_id,owner_internal_user_id,position_id,client_request_id,"
            "version_number,state,modules,summary,created_by,confirmed_by,confirmed_at,"
            "confirmed_module_names) values (%s,%s,%s,%s,1,'confirmed',"
            "'{\"jd\":{}}','Confirmed',%s,%s,now(),array['jd'])",
            (ids["context"], owner_id, position_id, uuid4(), owner_id, owner_id),
        )
        connection.execute(
            "update platform_hr.positions set current_context_version_id=%s "
            "where position_id=%s", (ids["context"], position_id),
        )
        connection.execute(
            "insert into platform_attachments.attachments("
            "attachment_id,owner_internal_user_id,source_kind,"
            "original_name_ciphertext,original_name_key_version,object_ref_ciphertext,"
            "object_ref_key_version,immutable_locator,sha256,state,ready_at) values ("
            "%s,%s,'user_input',%s,1,%s,1,%s,%s,'ready',now())",
            (ids["attachment"], owner_id, b"n" * 29, b"o" * 29,
             f"etag:resume-{ids['attachment']}", b"r" * 32),
        )
        connection.execute(
            "insert into platform_hr.candidate_draft_batches("
            "batch_request_id,owner_internal_user_id,position_id,attachment_ids) "
            "values (%s,%s,%s,array[%s]::uuid[])",
            (ids["batch"], owner_id, position_id, ids["attachment"]),
        )
        connection.execute(
            "insert into platform_hr.candidate_drafts("
            "draft_id,owner_internal_user_id,position_id,attachment_id,batch_request_id,"
            "client_request_id,state,extracted_facts,row_version) values ("
            "%s,%s,%s,%s,%s,%s,'confirmed','{}',2)",
            (ids["draft"], owner_id, position_id, ids["attachment"],
             ids["batch"], uuid4()),
        )
        connection.execute(
            "insert into platform_hr.candidates(candidate_id,owner_internal_user_id,"
            "confirmation_request_id,stable_name,facts) values (%s,%s,%s,'候选人','{}')",
            (ids["candidate"], owner_id, uuid4()),
        )
        connection.execute(
            "insert into platform_hr.candidate_documents(document_id,"
            "owner_internal_user_id,candidate_id,attachment_id,source_draft_id,"
            "document_kind,version_number,content_sha256) values ("
            "%s,%s,%s,%s,%s,'resume',1,%s)",
            (ids["document"], owner_id, ids["candidate"], ids["attachment"],
             ids["draft"], "a" * 64),
        )
        connection.execute(
            "insert into platform_hr.position_candidates(position_candidate_id,"
            "owner_internal_user_id,position_id,candidate_id,context_version_id,"
            "source_draft_id,client_request_id) values (%s,%s,%s,%s,%s,%s,%s)",
            (ids["relation"], owner_id, position_id, ids["candidate"],
             ids["context"], ids["draft"], uuid4()),
        )
    return ids


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


@pytest.mark.postgres
def test_candidate_projection_persists_envelopes_and_isolates_invalid_interview_artifacts(
    conversation_database,  # noqa: F811
    repository,  # noqa: F811
) -> None:
    environment, owner_id, other_owner_id = conversation_database
    positions = HrPositionRepository(environment["urls"]["platform_control_app"])
    position = positions.create_manual(
        CreateManualPosition(owner_id, uuid4(), uuid4(), "结构工程师")
    )
    scope = _seed_candidate_scope(
        environment, owner_id, position.position_id
    )
    intelligence = PositionIntelligenceService(
        PositionIntelligenceRepository(environment["urls"]["platform_control_app"])
    )
    tasks = HrPositionTaskService(
        intelligence,
        ConversationCommandService(repository, v2_enabled=True),
        HrPositionScope(positions),
        PostgresHrPositionTaskRepository(environment["urls"]["platform_control_app"]),
    )
    candidates = CandidateService(
        CandidateRepository(environment["urls"]["platform_control_app"])
    )
    match_payload = {
        "summary": "总体匹配",
        "dimensions": {"engineering": "strong"},
        "evidence": [{"resume_fact": "负责挤出系统"}],
        "gaps": ["海外经验未说明"],
        "risks": ["团队规模未知"],
        "unknowns": ["量产良率经验待验证"],
        "verification_questions": ["请说明量产良率。"],
    }
    interview_payload = {
        "title": "结构工程师-候选人-面试题",
        "questions": [{
            "verification_goal": "验证量产经验",
            "candidate_reason": "简历提及挤出系统",
            "question": "请说明量产挑战。",
            "follow_ups": ["良率如何？"],
            "strong_evidence": ["量化良率变化"],
            "risk_signals": ["无法区分本人贡献"],
        }],
    }
    started = []
    try:
        with (
            psycopg.connect(
                environment["urls"]["platform_control_app"], autocommit=True
            ) as connection,
            pytest.raises(
                psycopg.errors.NoDataFound,
                match="candidate interview artifact required",
            ),
        ):
            connection.execute(
                "select platform_hr.create_candidate_analysis_v77("
                "%s,%s,%s,%s,%s,'candidate_interview_plan',array[%s]::uuid[],"
                "array[]::uuid[],%s::jsonb,'[]'::jsonb,'[]'::jsonb,'[]'::jsonb,"
                "%s::jsonb,'hr-bot','hr-runtime-before-upgrade',null)",
                (
                    uuid4(), owner_id, scope["relation"], scope["context"],
                    uuid4(), scope["document"],
                    json.dumps(interview_payload, ensure_ascii=False),
                    json.dumps(["请说明量产挑战。"], ensure_ascii=False),
                ),
            )
        match = tasks.start(
            owner_id=owner_id, position_id=position.position_id,
            request_id=uuid4(), task_kind="candidate_match",
            context_version_id=scope["context"], material_ids=(),
            conversation_id=None, candidate_id=scope["candidate"],
            position_candidate_id=scope["relation"],
        )
        _finish_task(
            environment, match, owner_id, position.position_id,
            "candidate_match",
            {"text": "# 匹配\n\n" + encode_hr_envelope(
                "candidate_match", match_payload
            )},
            candidate_scope=scope,
        )
        started.append(match)

        valid = tasks.start(
            owner_id=owner_id, position_id=position.position_id,
            request_id=uuid4(), task_kind="candidate_interview_plan",
            context_version_id=scope["context"], material_ids=(),
            conversation_id=None, candidate_id=scope["candidate"],
            position_candidate_id=scope["relation"],
        )
        _, valid_versions = _finish_task(
            environment, valid, owner_id, position.position_id,
            "candidate_interview_plan",
            {"text": "# 面试题\n\n" + encode_hr_envelope(
                "candidate_interview_plan", interview_payload
            )},
            candidate_scope=scope,
            artifact_specs=({},),
        )
        started.append(valid)

        retried = tasks.start(
            owner_id=owner_id, position_id=position.position_id,
            request_id=uuid4(), task_kind="candidate_interview_plan",
            context_version_id=scope["context"], material_ids=(),
            conversation_id=None, candidate_id=scope["candidate"],
            position_candidate_id=scope["relation"],
        )
        _, retried_versions = _finish_task(
            environment, retried, owner_id, position.position_id,
            "candidate_interview_plan",
            {"text": "# 面试题\n\n" + encode_hr_envelope(
                "candidate_interview_plan", interview_payload
            )},
            candidate_scope=scope,
            artifact_specs=({"prior_states": ("scanning",)},),
        )
        started.append(retried)

        with psycopg.connect(environment["admin"]) as connection:
            wrong_conversation = uuid4()
            connection.execute(
                "insert into platform_control.conversations("
                "conversation_id,owner_internal_user_id,started_by_client_request_id,"
                "mode,direct_agent_id,title) values ("
                "%s,%s,%s,'direct_agent','hr-bot','wrong conversation')",
                (wrong_conversation, owner_id, uuid4()),
            )

        invalid_cases = (
            ((), "hr-bot"),
            (({"state": "scanning"},), "hr-bot"),
            (({"state": "scanning", "prior_states": ("ready",)},), "hr-bot"),
            (({"mime": "text/plain"},), "hr-bot"),
            (({}, {}), "hr-bot"),
            (({"task_id": uuid4(), "bypass_context": True},), "hr-bot"),
            (({},), None),
            (({},), "wrong-agent"),
            (({"expired": True},), "hr-bot"),
            (({"erasure": True},), "hr-bot"),
            (({
                "artifact_owner_id": other_owner_id,
                "bypass_artifact_integrity": True,
            },), "hr-bot"),
            (({
                "conversation_id": wrong_conversation,
                "bypass_context": True,
            },), "hr-bot"),
        )
        invalid_task_ids = []
        invalid_versions = []
        scanning_task = None
        for case_index, (specs, grant_agent_id) in enumerate(invalid_cases):
            task = tasks.start(
                owner_id=owner_id, position_id=position.position_id,
                request_id=uuid4(), task_kind="candidate_interview_plan",
                context_version_id=scope["context"], material_ids=(),
                conversation_id=None, candidate_id=scope["candidate"],
                position_candidate_id=scope["relation"],
            )
            mission_task_id, versions = _finish_task(
                environment, task, owner_id, position.position_id,
                "candidate_interview_plan",
                {"text": "# 面试题\n\n" + encode_hr_envelope(
                    "candidate_interview_plan", interview_payload
                )},
                candidate_scope=scope,
                artifact_specs=specs,
                grant_agent_id=grant_agent_id,
            )
            started.append(task)
            invalid_task_ids.append(mission_task_id)
            invalid_versions.append(versions)
            if case_index == 1:
                scanning_task = task

        with psycopg.connect(environment["admin"]) as connection:
            assert connection.execute(
                "select agent_id,scope from platform_attachments.task_grants "
                "where task_id=%s order by agent_id,scope",
                (invalid_task_ids[6],),
            ).fetchall() == []
            assert connection.execute(
                "select agent_id,scope from platform_attachments.task_grants "
                "where task_id=%s order by agent_id,scope",
                (invalid_task_ids[7],),
            ).fetchall() == [("wrong-agent", "write_output")]
            assert connection.execute(
                "select attachment.retained_until<=now(),version.retained_until<=now() "
                "from platform_attachments.artifact_versions version "
                "join platform_attachments.attachments attachment "
                "on attachment.attachment_id=version.attachment_id "
                "where version.artifact_version_id=%s",
                (invalid_versions[8][0],),
            ).fetchone() == (True, True)
            assert connection.execute(
                "select count(*) from platform_attachments.erasure_jobs "
                "where attachment_id=(select attachment_id "
                "from platform_attachments.artifact_versions "
                "where artifact_version_id=%s)",
                (invalid_versions[9][0],),
            ).fetchone() == (1,)
            assert connection.execute(
                "select artifact.owner_internal_user_id,artifact.conversation_id,"
                "attachment.owner_internal_user_id,attachment.conversation_id "
                "from platform_attachments.artifact_versions version "
                "join platform_attachments.artifacts artifact "
                "on artifact.artifact_id=version.artifact_id "
                "join platform_attachments.attachments attachment "
                "on attachment.attachment_id=version.attachment_id "
                "where version.artifact_version_id=%s",
                (invalid_versions[10][0],),
            ).fetchone() == (
                other_owner_id, started[13].conversation_id,
                owner_id, started[13].conversation_id,
            )
            assert connection.execute(
                "select artifact.owner_internal_user_id,artifact.conversation_id,"
                "attachment.owner_internal_user_id,attachment.conversation_id "
                "from platform_attachments.artifact_versions version "
                "join platform_attachments.artifacts artifact "
                "on artifact.artifact_id=version.artifact_id "
                "join platform_attachments.attachments attachment "
                "on attachment.attachment_id=version.attachment_id "
                "where version.artifact_version_id=%s",
                (invalid_versions[11][0],),
            ).fetchone() == (
                owner_id, wrong_conversation, owner_id, wrong_conversation,
            )

        reconciler = HrTaskResultReconciler(
            HrTaskResultProjectionRepository(
                environment["urls"]["platform_control_app"]
            ),
            intelligence,
            candidates,
            _codec(),
            worker_id="candidate-projection-test",
        )
        for _ in started:
            assert reconciler.reconcile_one() is True
        assert reconciler.reconcile_one() is False

        with psycopg.connect(environment["admin"]) as connection:
            scanning_projection_request_id = connection.execute(
                "select projection_request_id "
                "from platform_hr.hr_task_result_projections "
                "where task_request_id=%s",
                (scanning_task.task_id,),
            ).fetchone()[0]
        with (
            psycopg.connect(
                environment["urls"]["platform_control_app"], autocommit=True
            ) as connection,
            pytest.raises(
                psycopg.errors.NoDataFound,
                match="candidate analysis artifact invalid",
            ),
        ):
            connection.execute(
                "select platform_hr.create_candidate_analysis_v77("
                "%s,%s,%s,%s,%s,'candidate_interview_plan',array[%s]::uuid[],"
                "array[]::uuid[],%s::jsonb,'[]'::jsonb,'[]'::jsonb,'[]'::jsonb,"
                "%s::jsonb,'hr-bot','hr-runtime-before-upgrade',%s)",
                (
                    uuid4(), owner_id, scope["relation"], scope["context"],
                    scanning_projection_request_id, scope["document"],
                    json.dumps(interview_payload, ensure_ascii=False),
                    json.dumps(["请说明量产挑战。"], ensure_ascii=False),
                    invalid_versions[1][0],
                ),
            )

        analyses = candidates.list_analyses(owner_id, scope["relation"])
        assert len(analyses) == 3
        projected_match = next(item for item in analyses if item.analysis_kind == "match")
        projected_interviews = [
            item for item in analyses
            if item.analysis_kind == "candidate_interview_plan"
        ]
        projected_interview = next(
            item for item in projected_interviews
            if item.source_artifact_version_id == valid_versions[0]
        )
        assert projected_match.result["summary"] == "总体匹配"
        assert projected_match.evidence == ({"resume_fact": "负责挤出系统"},)
        assert projected_match.unknowns == ("量产良率经验待验证",)
        assert projected_match.source_artifact_version_id is None
        assert projected_interview.result == interview_payload
        assert projected_interview.source_artifact_version_id == valid_versions[0]
        assert {item.source_artifact_version_id for item in projected_interviews} == {
            valid_versions[0], retried_versions[0]
        }
        with psycopg.connect(environment["admin"]) as connection:
            projection_request_id = connection.execute(
                "select client_request_id from platform_hr.candidate_analysis_versions "
                "where analysis_version_id=%s",
                (projected_interview.analysis_version_id,),
            ).fetchone()[0]
            connection.execute(
                "update platform_attachments.artifact_versions set "
                "state='scanning',result_status='pending' "
                "where artifact_version_id=%s",
                (valid_versions[0],),
            )

        replay_sql = (
            "select (platform_hr.create_candidate_analysis_v77("
            "%s,%s,%s,%s,%s,'candidate_interview_plan',array[%s]::uuid[],"
            "array[]::uuid[],%s::jsonb,'[]'::jsonb,'[]'::jsonb,'[]'::jsonb,"
            "%s::jsonb,'hr-bot','hr-runtime-before-upgrade',%s)).*"
        )
        replay_parameters = (
            projected_interview.analysis_version_id,
            owner_id,
            scope["relation"],
            scope["context"],
            projection_request_id,
            scope["document"],
            json.dumps(interview_payload, ensure_ascii=False),
            json.dumps(["请说明量产挑战。"], ensure_ascii=False),
            valid_versions[0],
        )

        def replay_analysis(_index):
            with psycopg.connect(
                environment["urls"]["platform_control_app"]
            ) as connection:
                return connection.execute(
                    replay_sql, replay_parameters
                ).fetchone()

        with ThreadPoolExecutor(max_workers=2) as pool:
            replays = tuple(pool.map(replay_analysis, range(2)))
        assert replays[0] == replays[1]
        assert replays[0][0] == projected_interview.analysis_version_id
        assert replays[0][-1] == valid_versions[0]
        with (
            psycopg.connect(environment["admin"]) as connection,
            pytest.raises(psycopg.errors.CheckViolation),
        ):
            connection.execute(
                "update platform_hr.candidate_analysis_versions set "
                "source_artifact_version_id=null where analysis_version_id=%s",
                (projected_interview.analysis_version_id,),
            )
        assert [
            tasks.get(owner_id, position.position_id, task.task_id).error
            for task in started[3:]
        ] == ["result_projection_failed"] * len(invalid_cases)
    finally:
        with psycopg.connect(environment["admin"]) as connection:
            for table in (
                "candidate_analysis_feedback", "candidate_analysis_documents",
                "candidate_analysis_versions",
            ):
                connection.execute(
                    f"alter table platform_hr.{table} disable trigger all"
                )
                connection.execute(
                    f"delete from platform_hr.{table} "
                    "where owner_internal_user_id=%s", (owner_id,),
                )
                connection.execute(
                    f"alter table platform_hr.{table} enable trigger all"
                )
            connection.execute(
                "delete from platform_hr.hr_task_result_projections "
                "where owner_internal_user_id=%s", (owner_id,),
            )
            connection.execute(
                "delete from platform_hr.position_task_records "
                "where owner_internal_user_id=%s", (owner_id,),
            )
            connection.execute(
                "delete from platform_hr.position_task_requests "
                "where owner_internal_user_id=%s", (owner_id,),
            )
            connection.execute(
                "delete from platform_hr.position_candidates "
                "where owner_internal_user_id=%s", (owner_id,),
            )
            connection.execute(
                "delete from platform_hr.candidate_documents "
                "where owner_internal_user_id=%s", (owner_id,),
            )
            connection.execute(
                "delete from platform_hr.candidates "
                "where owner_internal_user_id=%s", (owner_id,),
            )
            connection.execute(
                "delete from platform_hr.candidate_drafts "
                "where owner_internal_user_id=%s", (owner_id,),
            )
            connection.execute(
                "delete from platform_hr.candidate_draft_batches "
                "where owner_internal_user_id=%s", (owner_id,),
            )
            connection.execute(
                "update platform_hr.positions set current_context_version_id=null "
                "where owner_internal_user_id=%s", (owner_id,),
            )
            connection.execute(
                "alter table platform_hr.position_context_versions disable trigger all"
            )
            connection.execute(
                "delete from platform_hr.position_context_versions "
                "where owner_internal_user_id=%s", (owner_id,),
            )
            connection.execute(
                "alter table platform_hr.position_context_versions enable trigger all"
            )
            connection.execute(
                "delete from platform_hr.position_binding_events "
                "where owner_internal_user_id=%s", (owner_id,),
            )
            connection.execute(
                "delete from platform_hr.position_conversations "
                "where owner_internal_user_id=%s", (owner_id,),
            )
            connection.execute(
                "delete from platform_hr.positions where owner_internal_user_id=%s",
                (owner_id,),
            )
