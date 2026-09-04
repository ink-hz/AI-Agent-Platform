from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from test_agent_brain_conversation_repository import _codec
from test_control_plane_migration import control_database  # noqa: F401
from test_hr_position_package_projection import _seed_conversation, _seed_turn
from test_hr_task_result_projection_database import _finish_task

from app.agent_brain.conversation_context import ConversationContextBuilder
from app.agent_brain.conversation_projection import ConversationProjection
from app.agent_brain.conversation_repository import (
    ConversationRepository,
    message_subject,
)
from app.agent_brain.conversation_service import ConversationCommandService
from app.agent_brain.models import load_capability_cards
from app.agent_brain.orchestrator import MissionOrchestrator
from app.agent_brain.repository import MissionRepository
from app.control_plane.models import AuthContext, Role
from app.execution_relay.content_crypto import SealedContent
from app.execution_relay.models import RelayEvent
from app.execution_relay.repository import ExecutionRelayRepository
from app.hr.candidate_context import CandidateEnvelopeProvider
from app.hr.candidate_models import ClaimNextCandidateDraft
from app.hr.candidate_parser_queue import CandidateParserQueue
from app.hr.candidate_parser_runtime import (
    CandidateParserAppRepository,
    CandidateParserInputProvider,
    CandidateParserRuntime,
    CandidateParserSubmissionCoordinator,
    PostgresCandidateParserResultReader,
)
from app.hr.candidate_repository import CandidateRepository
from app.hr.candidate_service import CandidateService
from app.hr.context import HrPositionScope
from app.hr.position_intelligence_repository import PositionIntelligenceRepository
from app.hr.position_intelligence_service import PositionIntelligenceService
from app.hr.position_package_projection import (
    PositionPackageProjectionRepository,
    PositionPackageProjector,
)
from app.hr.repository import HrPositionRepository
from app.hr.resource_service import (
    HrPositionResourceService,
    PsycopgPositionResourceRepository,
)
from app.hr.service import HrPositionService
from app.hr.structured_output import encode_hr_envelope
from app.hr.task_context import HrTaskContextProvider, PostgresHrTaskContextSource
from app.hr.task_repository import PostgresHrPositionTaskRepository
from app.hr.task_result_projection import (
    HrTaskResultProjectionRepository,
    HrTaskResultReconciler,
)
from app.hr.task_service import HrPositionTaskService
from app.main import create_app

POSITION_PACKAGE = {
    "title": "高级结构工程师",
    "modules": {
        "mission": {"text": "负责高可靠挤出系统交付。"},
        "jd": {"text": "负责喷嘴与挤出系统结构设计和量产。"},
        "jr": {"text": "具备五年以上精密机械量产经验。"},
    },
}
MATCH_PAYLOADS = (
    {
        "summary": "候选人甲总体匹配",
        "dimensions": {"technical": "强匹配"},
        "evidence": [{"resume_fact": "负责挤出系统量产", "document": "resume-v1"}],
        "gaps": ["海外交付经历未说明"],
        "risks": ["团队规模待核实"],
        "unknowns": ["量产良率经验待验证"],
        "verification_questions": ["请说明量产良率提升过程。"],
    },
    {
        "summary": "候选人乙具备相邻能力",
        "dimensions": {"technical": "部分匹配"},
        "evidence": [{"resume_fact": "负责精密机械设计", "document": "resume-v2"}],
        "gaps": ["挤出系统经验未说明"],
        "risks": ["量产规模待核实"],
        "unknowns": ["喷嘴热设计经验待验证"],
        "verification_questions": ["请说明精密结构量产案例。"],
    },
)
INTERVIEW_PAYLOAD = {
    "title": "高级结构工程师-候选人甲-面试题",
    "questions": [
        {
            "verification_goal": "验证挤出系统量产经验",
            "candidate_reason": "简历明确提及挤出系统量产",
            "question": "请说明一次挤出系统量产良率提升过程。",
            "follow_ups": ["你负责的部分是什么？", "良率提升了多少？"],
            "strong_evidence": ["给出基线、措施和量化结果"],
            "risk_signals": ["无法区分个人贡献与团队成果"],
        }
    ],
}


def _assistant_text(environment, codec, conversation_id: UUID, turn_id: UUID) -> str:
    with psycopg.connect(environment["admin"]) as connection:
        row = connection.execute(
            "select message.message_id,message.content_ciphertext,"
            "message.encryption_key_version from "
            "platform_control.conversation_messages message where "
            "message.conversation_id=%s and message.turn_id=%s "
            "and message.role='assistant'",
            (conversation_id, turn_id),
        ).fetchone()
    assert row is not None
    opened = codec.unseal_json(
        message_subject(conversation_id, row[0]),
        SealedContent(bytes(row[1]), row[2]),
    )
    assert isinstance(opened.get("text"), str)
    return opened["text"]


class _AllowHrAgent:
    def decide_for_user_id(self, owner_id: UUID, agent_id: str):
        assert isinstance(owner_id, UUID)
        assert agent_id == "hr-bot"
        return SimpleNamespace(allowed=True)

    def permitted_catalog_for_user_id(self, _owner_id: UUID):
        return ()


class _StorageBoundary:
    def attachment(self, owner_id: UUID, attachment_id: UUID):
        return SimpleNamespace(
            owner_internal_user_id=owner_id,
            attachment_id=attachment_id,
            original_name="高级结构工程师-候选人甲-面试题-v1.pdf",
            detected_mime="application/pdf",
            declared_mime="application/pdf",
            size_bytes=128,
            state="ready",
            created_at=datetime(2026, 9, 4, tzinfo=UTC),
        )

    def issue_ticket(self, owner_id: UUID, attachment_id: UUID, purpose: str):
        assert isinstance(owner_id, UUID)
        assert isinstance(attachment_id, UUID)
        assert purpose == "download"
        return {
            "content_path": f"/api/v1/attachments/content/{'a' * 32}",
            "expires_at": "2026-09-04T10:05:00Z",
        }


def _client(
    owner_id: UUID,
    positions: HrPositionService,
    candidates: CandidateService,
    tasks: HrPositionTaskService,
    resources: HrPositionResourceService,
) -> TestClient:
    app = create_app(
        start_poller=False,
        hr_position_service=positions,
        hr_candidate_service=candidates,
        hr_position_task_service=tasks,
        hr_resource_service=resources,
        agent_use_authorization=_AllowHrAgent(),
    )

    @app.middleware("http")
    async def identity(request: Request, call_next):
        request.state.auth_context = AuthContext(owner_id, Role.MEMBER, uuid4(), False)
        return await call_next(request)

    return TestClient(app)


def _seed_resume_attachments(environment, owner_id: UUID) -> tuple[UUID, ...]:
    attachment_ids = tuple(uuid4() for _ in range(3))
    with psycopg.connect(environment["admin"]) as connection:
        for index, attachment_id in enumerate(attachment_ids, start=1):
            connection.execute(
                "insert into platform_attachments.attachments("
                "attachment_id,owner_internal_user_id,source_kind,"
                "original_name_ciphertext,original_name_key_version,"
                "object_ref_ciphertext,object_ref_key_version,immutable_locator,"
                "declared_mime,detected_mime,size_bytes,sha256,state,ready_at) "
                "values (%s,%s,'user_input',%s,1,%s,1,%s,'application/pdf',"
                "'application/pdf',128,%s,'ready',now())",
                (
                    attachment_id,
                    owner_id,
                    b"n" * 29,
                    b"o" * 29,
                    f"etag:p0-resume-{index}-{attachment_id}",
                    bytes([index]) * 32,
                ),
            )
    return attachment_ids


def _complete_parser_agent_result(harness, attempt, text: str, worker_id: str) -> None:
    with psycopg.connect(harness.environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.execution_workers("
            "worker_id,allowed_agent_ids,status) values "
            "(%s,array['hr-bot'],'active')",
            (worker_id,),
        )
    assert harness.coordinator.submit_one() is True
    with psycopg.connect(harness.environment["admin"]) as connection:
        conversation_id, turn_id = connection.execute(
            "select conversation.conversation_id,turn.turn_id from "
            "platform_control.conversations conversation join "
            "platform_control.conversation_turns turn using (conversation_id) "
            "where conversation.owner_internal_user_id=%s and "
            "conversation.started_by_client_request_id=%s and "
            "turn.client_request_id=%s",
            (attempt.owner_id, attempt.attempt_id, attempt.attempt_id),
        ).fetchone()
    context = harness.context_builder.build(conversation_id, turn_id)
    assert context.active_attachment_ids == (attempt.attachment_id,)
    assert harness.orchestrator.advance_pending(limit=50) == 1
    lease = harness.relay.lease(worker_id, ("hr-bot",), 300, ("direct_agent",))
    assert lease is not None
    harness.relay.mark_dispatched(worker_id, lease.payload.run_id)
    harness.relay.append_events(
        worker_id,
        (
            RelayEvent(
                run_id=lease.payload.run_id,
                seq=1,
                event_type="agent.complete",
                created_at=datetime.now(UTC),
                payload={
                    "result": {
                        "contractVersion": "core_chat_result_v2",
                        "success": True,
                        "outputText": text,
                        "publicAnswerMarkdown": text,
                    }
                },
            ),
        ),
    )
    harness.relay.finish(worker_id, lease.payload.run_id, "completed")
    assert harness.orchestrator.advance_pending(limit=50) >= 1
    harness.orchestrator.advance_pending(limit=50)
    discovered = harness.queue.discover_execution(attempt.attempt_id, worker_id)
    harness.queue.attach_execution(discovered)
    reader = PostgresCandidateParserResultReader(
        harness.environment["urls"]["platform_brain_worker"], harness.codec
    )
    result = reader.read(attempt.attempt_id, worker_id)
    assert (result.execution_status, result.turn_status) == (
        "completed",
        "completed",
    )
    assert result.assistant_content == text
    runtime = CandidateParserRuntime(
        harness.queue,
        reader,
        worker_id=worker_id,
    )
    assert runtime.tick() is True


def _project_and_confirm_position(
    environment,
    codec,
    owner_id: UUID,
    positions: HrPositionService,
    candidates: CandidateService,
    tasks: HrPositionTaskService,
    resources: HrPositionResourceService,
) -> tuple[TestClient, UUID, UUID, UUID]:
    app_url = environment["urls"]["platform_control_app"]
    started_at = datetime(2026, 9, 4, tzinfo=UTC)
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.internal_users("
            "internal_user_id,display_name,status) values (%s,'P0 HR','active')",
            (owner_id,),
        )
        conversation_id, _, clarification_message_id = _seed_conversation(
            connection,
            owner_id,
            "请补充岗位地点和量产范围。",
            created_at=started_at,
        )
        _, package_message_id = _seed_turn(
            connection,
            conversation_id,
            "岗位方案已生成。\n\n"
            + encode_hr_envelope("position_package", POSITION_PACKAGE),
            seq=3,
            created_at=started_at + timedelta(seconds=1),
        )

    projector = PositionPackageProjector(
        PositionPackageProjectionRepository(app_url),
        positions,
        codec,
        worker_id="hr-p0-position-projector",
        model_version="hr-p0-deterministic-agent",
    )
    assert projector.reconcile_one() is True
    assert projector.reconcile_one() is True
    assert projector.reconcile_one() is False
    with psycopg.connect(environment["admin"]) as connection:
        projections = dict(
            connection.execute(
                "select assistant_message_id,state from "
                "platform_hr.position_package_projections where "
                "assistant_message_id in (%s,%s)",
                (clarification_message_id, package_message_id),
            ).fetchall()
        )
    assert projections == {
        clarification_message_id: "skipped",
        package_message_id: "completed",
    }

    client = _client(owner_id, positions, candidates, tasks, resources)
    package_response = client.get(
        f"/api/hr/conversations/{conversation_id}/position-package"
    )
    assert package_response.status_code == 200
    package = package_response.json()
    assert package["modules"] == POSITION_PACKAGE["modules"]

    confirmation = client.post(
        f"/api/hr/position-drafts/{package['draft_id']}/versions/"
        f"{package['draft_version_id']}/confirm",
        json={"expected_row_version": package["row_version"]},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert confirmation.status_code == 200
    confirmed = confirmation.json()
    assert confirmed["conversation_id"] == str(conversation_id)
    position_id = UUID(confirmed["position_id"])
    context_id = UUID(confirmed["context_version_id"])
    with psycopg.connect(environment["admin"]) as connection:
        assert connection.execute(
            "select current_context_version_id from platform_hr.positions "
            "where owner_internal_user_id=%s and position_id=%s",
            (owner_id, position_id),
        ).fetchone() == (context_id,)
        assert connection.execute(
            "select binding_kind from platform_hr.position_conversations "
            "where owner_internal_user_id=%s and position_id=%s "
            "and conversation_id=%s",
            (owner_id, position_id, conversation_id),
        ).fetchone() == ("draft_confirmed",)
        assert connection.execute(
            "select state,resolved_position_id,row_version from "
            "platform_hr.position_drafts where owner_internal_user_id=%s "
            "and draft_id=%s",
            (owner_id, UUID(package["draft_id"])),
        ).fetchone() == ("confirmed", position_id, package["row_version"] + 1)
        assert (
            connection.execute(
                "select modules from platform_hr.position_context_versions "
                "where context_version_id=%s",
                (context_id,),
            ).fetchone()[0]
            == POSITION_PACKAGE["modules"]
        )
    return client, conversation_id, position_id, context_id


def _parse_resumes(
    environment,
    codec,
    owner_id: UUID,
    client: TestClient,
    position_id: UUID,
) -> tuple[UUID, ...]:
    attachments = _seed_resume_attachments(environment, owner_id)
    batch_response = client.post(
        f"/api/hr/positions/{position_id}/candidate-drafts:batch",
        json={"attachment_ids": [str(value) for value in attachments]},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert batch_response.status_code == 202
    draft_ids = tuple(UUID(item["draft_id"]) for item in batch_response.json()["items"])

    queue = CandidateParserQueue(
        CandidateRepository(environment["urls"]["platform_brain_worker"])
    )
    app_repository = CandidateParserAppRepository(
        environment["urls"]["platform_control_app"]
    )
    missions = MissionRepository(
        environment["urls"]["platform_control_app"], content_codec=codec
    )
    conversations = ConversationRepository(
        environment["urls"]["platform_control_app"],
        content_codec=codec,
        mission_repository=missions,
    )
    context_builder = ConversationContextBuilder(
        conversations,
        candidate_parser_input_provider=CandidateParserInputProvider(app_repository),
    )
    relay = ExecutionRelayRepository(
        environment["urls"]["platform_control_app"], content_codec=codec
    )
    harness = SimpleNamespace(
        environment=environment,
        codec=codec,
        queue=queue,
        coordinator=CandidateParserSubmissionCoordinator(
            app_repository,
            ConversationCommandService(conversations, v2_enabled=True),
        ),
        context_builder=context_builder,
        relay=relay,
        orchestrator=MissionOrchestrator(
            missions,
            relay,
            capability_provider=lambda _owner: tuple(
                card for card in load_capability_cards() if card.agent_id == "hr-bot"
            ),
            conversation_context_builder=context_builder,
            conversation_projection=ConversationProjection(conversations),
            mission_modes=("direct_agent",),
        ),
    )
    parse_results = (
        {"stable_name": "候选人甲", "skills": ["挤出系统", "量产"]},
        {"stable_name": "候选人乙", "skills": ["精密机械"]},
        None,
    )
    for index, expected_draft_id in enumerate(draft_ids):
        worker_id = f"hr-p0-resume-parser-{index}"
        attempt = queue.claim_next(ClaimNextCandidateDraft(uuid4(), worker_id, 300))
        assert attempt.draft_id == expected_draft_id
        facts = parse_results[index]
        text = (
            json.dumps(
                {
                    "extracted_facts": facts,
                    "identity_candidate_ids": [],
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            if facts is not None
            else "deterministic invalid resume result"
        )
        _complete_parser_agent_result(harness, attempt, text, worker_id)

    states = {
        UUID(item["draft_id"]): item
        for item in client.get(
            f"/api/hr/positions/{position_id}/candidate-drafts"
        ).json()["items"]
    }
    assert [states[value]["state"] for value in draft_ids] == [
        "ready",
        "ready",
        "failed",
    ], [(states[value]["state"], states[value]["error_code"]) for value in draft_ids]
    assert states[draft_ids[2]]["error_code"] == "parser_response_invalid"
    retry = client.post(
        f"/api/hr/candidate-drafts/{draft_ids[2]}:retry",
        json={"expected_row_version": states[draft_ids[2]]["row_version"]},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert retry.status_code == 200
    assert retry.json()["state"] == "pending"
    retry_worker = "hr-p0-resume-parser-retry"
    retried_attempt = queue.claim_next(
        ClaimNextCandidateDraft(uuid4(), retry_worker, 300)
    )
    assert retried_attempt.draft_id == draft_ids[2]
    _complete_parser_agent_result(
        harness,
        retried_attempt,
        '{"extracted_facts":{"stable_name":"候选人丙","skills":["测试"]},'
        '"identity_candidate_ids":[]}',
        retry_worker,
    )
    retried = client.get(f"/api/hr/candidate-drafts/{draft_ids[2]}").json()
    assert retried["state"] == "ready"
    assert retried["extracted_facts"] == {
        "stable_name": "候选人丙",
        "skills": ["测试"],
    }
    preserved = client.get(f"/api/hr/positions/{position_id}/candidate-drafts").json()[
        "items"
    ]
    assert [item["state"] for item in preserved] == ["ready", "ready", "ready"]
    return draft_ids


def _confirm_candidates_and_project_matches(
    environment,
    codec,
    owner_id: UUID,
    client: TestClient,
    positions: HrPositionService,
    candidates: CandidateService,
    intelligence: PositionIntelligenceService,
    tasks: HrPositionTaskService,
    task_contexts: HrTaskContextProvider,
    conversation_id: UUID,
    position_id: UUID,
    context_id: UUID,
    draft_ids: tuple[UUID, ...],
) -> tuple[list[dict], HrTaskResultReconciler]:
    app_url = environment["urls"]["platform_control_app"]
    confirmed_candidates = []
    for draft_id, stable_name in zip(
        draft_ids[:2], ("候选人甲", "候选人乙"), strict=True
    ):
        draft = client.get(f"/api/hr/candidate-drafts/{draft_id}").json()
        response = client.post(
            f"/api/hr/candidate-drafts/{draft_id}:confirm",
            json={
                "expected_row_version": draft["row_version"],
                "context_version_id": str(context_id),
                "stable_name": stable_name,
                "confirmed_facts": draft["extracted_facts"],
                "merge_candidate_id": None,
            },
            headers={"Idempotency-Key": str(uuid4())},
        )
        assert response.status_code == 201
        confirmed_candidates.append(response.json())
    relations = client.get(f"/api/hr/positions/{position_id}/candidates").json()[
        "items"
    ]
    assert len(relations) == 2
    wrong_binding = client.post(
        f"/api/hr/positions/{position_id}/tasks",
        json={
            "task_kind": "candidate_match",
            "context_version_id": str(context_id),
            "candidate_id": relations[0]["candidate_id"],
            "position_candidate_id": relations[1]["position_candidate_id"],
            "material_ids": [],
            "conversation_id": str(conversation_id),
        },
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert (wrong_binding.status_code, wrong_binding.json()) == (
        409,
        {"detail": "HR position task conflict"},
    )

    reconciler = HrTaskResultReconciler(
        HrTaskResultProjectionRepository(app_url),
        intelligence,
        candidates,
        codec,
        worker_id="hr-p0-task-projector",
    )
    for candidate_index, (candidate, match_payload) in enumerate(zip(
        confirmed_candidates, MATCH_PAYLOADS, strict=True
    )):
        relation = candidate["position_candidate"]
        document = candidate["document"]
        task_response = client.post(
            f"/api/hr/positions/{position_id}/tasks",
            json={
                "task_kind": "candidate_match",
                "context_version_id": str(context_id),
                "candidate_id": relation["candidate_id"],
                "position_candidate_id": relation["position_candidate_id"],
                "material_ids": [],
                "conversation_id": str(conversation_id),
            },
            headers={"Idempotency-Key": str(uuid4())},
        )
        assert task_response.status_code == 202
        task = tasks.get(owner_id, position_id, UUID(task_response.json()["task_id"]))
        if candidate_index == 1:
            with psycopg.connect(environment["admin"]) as connection:
                connection.execute(
                    "update platform_hr.position_candidates set status='archived' "
                    "where position_candidate_id=%s",
                    (UUID(relation["position_candidate_id"]),),
                )
                connection.execute(
                    "update platform_hr.candidate_documents set status='erased' "
                    "where document_id=%s",
                    (UUID(document["document_id"]),),
                )
                connection.execute(
                    "update platform_attachments.attachments set state='scanning' "
                    "where attachment_id=%s",
                    (UUID(document["attachment_id"]),),
                )
        envelope = task_contexts.build_for_turn(
            owner_id, task.conversation_id, task.turn_id
        )
        assert (
            envelope.position_id,
            envelope.context_version_id,
            envelope.candidate_id,
            envelope.position_candidate_id,
            envelope.document_attachment_ids,
            envelope.human_feedback_ids,
        ) == (
            position_id,
            context_id,
            UUID(relation["candidate_id"]),
            UUID(relation["position_candidate_id"]),
            (UUID(document["attachment_id"]),),
            (),
        )
        if candidate_index == 0:
            with psycopg.connect(environment["admin"]) as connection:
                persisted_record = connection.execute(
                    "select record.task_record_id,request.client_request_id "
                    "from platform_hr.position_task_records record join "
                    "platform_hr.position_task_requests request on "
                    "request.owner_internal_user_id=record.owner_internal_user_id "
                    "and request.client_request_id=record.client_request_id "
                    "where record.owner_internal_user_id=%s "
                    "and record.conversation_id=%s and record.turn_id=%s",
                    (owner_id, task.conversation_id, task.turn_id),
                ).fetchone()
            with (
                psycopg.connect(app_url) as connection,
                pytest.raises(psycopg.errors.UniqueViolation),
            ):
                connection.execute(
                    "select platform_hr.create_position_task_record_v79("
                    "%s,%s,%s,%s,%s,%s,%s,%s::uuid[],%s,%s,%s::uuid[],"
                    "%s::uuid[],%s,%s,%s,%s,%s)",
                    (
                        persisted_record[0], owner_id, position_id,
                        persisted_record[1], "candidate_match",
                        envelope.official_version_id, context_id, [],
                        UUID(relation["candidate_id"]),
                        UUID(relation["position_candidate_id"]),
                        [uuid4()], [], task.conversation_id, task.turn_id,
                        envelope.prompt_context, envelope.canonical_sha256,
                        "hr-runtime-before-upgrade",
                    ),
                )
        if candidate_index == 1:
            with psycopg.connect(environment["admin"]) as connection:
                connection.execute(
                    "update platform_hr.position_candidates set status='active' "
                    "where position_candidate_id=%s",
                    (UUID(relation["position_candidate_id"]),),
                )
                connection.execute(
                    "update platform_hr.candidate_documents set status='active' "
                    "where document_id=%s",
                    (UUID(document["document_id"]),),
                )
                connection.execute(
                    "update platform_attachments.attachments set state='ready' "
                    "where attachment_id=%s",
                    (UUID(document["attachment_id"]),),
                )
        _finish_task(
            environment,
            task,
            owner_id,
            position_id,
            "candidate_match",
            {
                "text": "# 岗位匹配分析\n\n"
                + match_payload["summary"]
                + "\n\n证据："
                + match_payload["evidence"][0]["resume_fact"]
                + "\n\n"
                + encode_hr_envelope("candidate_match", match_payload)
            },
            candidate_scope={
                "context": context_id,
                "candidate": UUID(relation["candidate_id"]),
                "relation": UUID(relation["position_candidate_id"]),
                "attachment": UUID(document["attachment_id"]),
            },
            existing_task_context=True,
        )
        preserved_text = _assistant_text(
            environment, codec, task.conversation_id, task.turn_id
        )
        assert match_payload["summary"] in preserved_text
        assert match_payload["evidence"][0]["resume_fact"] in preserved_text
        with psycopg.connect(environment["admin"]) as connection:
            snapshot = connection.execute(
                "select position_id,context_version_id,candidate_id,"
                "position_candidate_id,document_attachment_ids,"
                "human_feedback_ids from platform_hr.position_task_records "
                "where owner_internal_user_id=%s and conversation_id=%s "
                "and turn_id=%s",
                (owner_id, task.conversation_id, task.turn_id),
            ).fetchone()
        assert snapshot == (
            position_id,
            context_id,
            UUID(relation["candidate_id"]),
            UUID(relation["position_candidate_id"]),
            [UUID(document["attachment_id"])],
            [],
        )
        assert reconciler.reconcile_one() is True
        projected = client.get(
            f"/api/hr/position-candidates/{relation['position_candidate_id']}/analyses"
        ).json()["items"]
        assert projected[-1]["result"] == match_payload
        assert projected[-1]["evidence"] == match_payload["evidence"]
        assert projected[-1]["unknowns"] == match_payload["unknowns"]
    return confirmed_candidates, reconciler


def _project_interview_pdf(
    environment,
    owner_id: UUID,
    client: TestClient,
    tasks: HrPositionTaskService,
    task_contexts: HrTaskContextProvider,
    reconciler: HrTaskResultReconciler,
    conversation_id: UUID,
    position_id: UUID,
    context_id: UUID,
    confirmed_candidates: list[dict],
) -> None:
    primary = confirmed_candidates[0]
    primary_relation = primary["position_candidate"]
    primary_document = primary["document"]
    interview_start = client.post(
        f"/api/hr/positions/{position_id}/tasks",
        json={
            "task_kind": "candidate_interview_plan",
            "context_version_id": str(context_id),
            "candidate_id": primary_relation["candidate_id"],
            "position_candidate_id": primary_relation["position_candidate_id"],
            "material_ids": [],
            "conversation_id": str(conversation_id),
        },
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert interview_start.status_code == 202
    interview_task = tasks.get(
        owner_id, position_id, UUID(interview_start.json()["task_id"])
    )
    envelope = task_contexts.build_for_turn(
        owner_id, interview_task.conversation_id, interview_task.turn_id
    )
    assert envelope.document_attachment_ids == (
        UUID(primary_document["attachment_id"]),
    )
    assert envelope.position_candidate_id == UUID(
        primary_relation["position_candidate_id"]
    )
    _, artifact_versions = _finish_task(
        environment,
        interview_task,
        owner_id,
        position_id,
        "candidate_interview_plan",
        {
            "text": "# 候选人专属面试题\n\n"
            + INTERVIEW_PAYLOAD["title"]
            + "\n\n"
            + INTERVIEW_PAYLOAD["questions"][0]["question"]
            + "\n\n"
            + encode_hr_envelope("candidate_interview_plan", INTERVIEW_PAYLOAD)
        },
        candidate_scope={
            "context": context_id,
            "candidate": UUID(primary_relation["candidate_id"]),
            "relation": UUID(primary_relation["position_candidate_id"]),
            "attachment": UUID(primary_document["attachment_id"]),
        },
        artifact_specs=({},),
        existing_task_context=True,
    )
    preserved_text = _assistant_text(
        environment, _codec(), interview_task.conversation_id, interview_task.turn_id
    )
    assert INTERVIEW_PAYLOAD["title"] in preserved_text
    assert INTERVIEW_PAYLOAD["questions"][0]["question"] in preserved_text
    with psycopg.connect(environment["admin"]) as connection:
        interview_snapshot = connection.execute(
            "select position_id,context_version_id,candidate_id,"
            "position_candidate_id,document_attachment_ids,"
            "human_feedback_ids from platform_hr.position_task_records "
            "where owner_internal_user_id=%s and conversation_id=%s "
            "and turn_id=%s",
            (owner_id, interview_task.conversation_id, interview_task.turn_id),
        ).fetchone()
    assert interview_snapshot == (
        position_id,
        context_id,
        UUID(primary_relation["candidate_id"]),
        UUID(primary_relation["position_candidate_id"]),
        [UUID(primary_document["attachment_id"])],
        [],
    )
    assert len(artifact_versions) == 1
    with psycopg.connect(environment["admin"]) as connection:
        artifact_id = connection.execute(
            "select artifact_id from platform_attachments.artifact_versions "
            "where artifact_version_id=%s",
            (artifact_versions[0],),
        ).fetchone()[0]
    assert HrPositionScope(
        HrPositionRepository(environment["urls"]["platform_control_app"])
    ).link_artifact(owner_id, interview_task.conversation_id, artifact_id)
    assert reconciler.reconcile_one() is True

    analyses = client.get(
        f"/api/hr/position-candidates/"
        f"{primary_relation['position_candidate_id']}/analyses"
    ).json()["items"]
    interview = next(
        item for item in analyses if item["analysis_kind"] == "candidate_interview_plan"
    )
    assert interview["result"] == INTERVIEW_PAYLOAD
    assert interview["verification_questions"] == [
        INTERVIEW_PAYLOAD["questions"][0]["question"]
    ]
    assert interview["source_artifact_version_id"] == str(artifact_versions[0])
    with psycopg.connect(environment["admin"]) as connection:
        assert connection.execute(
            "select state,result_status,detected_mime from "
            "platform_attachments.artifact_versions "
            "where artifact_version_id=%s",
            (artifact_versions[0],),
        ).fetchone() == ("ready", "succeeded", "application/pdf")
        assert (
            connection.execute(
                "select count(*) from platform_hr.candidates "
                "where owner_internal_user_id=%s",
                (owner_id,),
            ).fetchone()[0]
            == 2
        )
    resource_response = client.get(f"/api/hr/positions/{position_id}/resources")
    assert resource_response.status_code == 200
    pdf = next(
        item
        for item in resource_response.json()["artifacts"]
        if item["artifact_version_id"] == str(artifact_versions[0])
    )
    assert (pdf["media_type"], pdf["state"], pdf["download_available"]) == (
        "application/pdf",
        "ready",
        True,
    )
    ticket = client.post(
        f"/api/hr/positions/{position_id}/resources/{pdf['attachment_id']}/ticket",
        json={"purpose": "download"},
    )
    assert ticket.status_code == 200
    assert ticket.json()["content_path"] == (f"/api/v1/attachments/content/{'a' * 32}")


@pytest.mark.postgres
def test_recruiting_loop_projects_agent_results_through_public_hr_apis(
    control_database,  # noqa: F811
) -> None:
    environment = control_database["environments"]["production"]
    app_url = environment["urls"]["platform_control_app"]
    codec = _codec()
    positions = HrPositionService(HrPositionRepository(app_url))
    candidate_repository = CandidateRepository(app_url)
    candidates = CandidateService(candidate_repository)
    intelligence_repository = PositionIntelligenceRepository(app_url)
    intelligence = PositionIntelligenceService(intelligence_repository)
    conversation_repository = ConversationRepository(
        app_url,
        content_codec=codec,
        mission_repository=MissionRepository(app_url, content_codec=codec),
    )
    candidate_provider = CandidateEnvelopeProvider(
        candidate_repository,
        lambda selected_owner, selected_position, selected_context: (
            (
                current := intelligence_repository.current(
                    selected_owner, selected_position
                )
            )
            is not None
            and current.state == "confirmed"
            and current.context_version_id == selected_context
        ),
    )
    tasks = HrPositionTaskService(
        intelligence,
        ConversationCommandService(conversation_repository, v2_enabled=True),
        HrPositionScope(HrPositionRepository(app_url)),
        PostgresHrPositionTaskRepository(app_url),
        candidate_validator=candidate_provider,
    )
    task_contexts = HrTaskContextProvider(
        PostgresHrTaskContextSource(
            app_url, execution_model_version="hr-runtime-before-upgrade"
        ),
        candidate_provider=candidate_provider,
    )
    storage = _StorageBoundary()
    resources = HrPositionResourceService(
        PsycopgPositionResourceRepository(
            lambda: psycopg.connect(app_url, row_factory=dict_row), storage
        ),
        storage,
    )
    owner_id = uuid4()

    client, conversation_id, position_id, context_id = _project_and_confirm_position(
        environment, codec, owner_id, positions, candidates, tasks, resources
    )
    draft_ids = _parse_resumes(environment, codec, owner_id, client, position_id)
    confirmed_candidates, reconciler = _confirm_candidates_and_project_matches(
        environment,
        codec,
        owner_id,
        client,
        positions,
        candidates,
        intelligence,
        tasks,
        task_contexts,
        conversation_id,
        position_id,
        context_id,
        draft_ids,
    )
    _project_interview_pdf(
        environment,
        owner_id,
        client,
        tasks,
        task_contexts,
        reconciler,
        conversation_id,
        position_id,
        context_id,
        confirmed_candidates,
    )
