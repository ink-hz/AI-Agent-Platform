from __future__ import annotations

from uuid import uuid4

import psycopg
import pytest
from test_agent_brain_conversation_repository import _codec
from test_hr_candidate_database import _seed_candidate_scope, candidate_database

from app.agent_brain.conversation_context import ConversationContextBuilder
from app.agent_brain.conversation_repository import (
    ConversationRepository,
    message_subject,
)
from app.agent_brain.conversation_service import ConversationCommandService
from app.agent_brain.repository import MissionRepository
from app.hr.candidate_models import ClaimNextCandidateDraft
from app.hr.candidate_parser_queue import CandidateParserQueue
from app.hr.candidate_parser_runtime import (
    CandidateParserAppRepository,
    CandidateParserInputProvider,
    CandidateParserRuntime,
    CandidateParserSubmissionCoordinator,
    PostgresCandidateParserResultReader,
)
from app.hr.candidate_repository import CandidateNotFound, CandidateRepository


@pytest.mark.postgres
def test_real_candidate_parser_submission_context_and_terminal_recovery(
    candidate_database,
) -> None:
    environment = candidate_database["environments"]["production"]
    ids = _seed_candidate_scope(environment)
    queued = {name: uuid4() for name in (
        "attachment", "batch", "draft", "request", "attempt",
    )}
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_attachments.attachments("
            "attachment_id,owner_internal_user_id,source_kind,"
            "original_name_ciphertext,original_name_key_version,"
            "object_ref_ciphertext,object_ref_key_version,immutable_locator,"
            "sha256,state,ready_at) values ("
            "%s,%s,'user_input',%s,1,%s,1,'etag:parser-runtime',%s,'ready',now())",
            (queued["attachment"], ids["owner"], b"n" * 29, b"o" * 29, b"p" * 32),
        )
        connection.execute(
            "insert into platform_hr.candidate_draft_batches("
            "batch_request_id,owner_internal_user_id,position_id,attachment_ids) "
            "values (%s,%s,%s,array[%s]::uuid[])",
            (queued["batch"], ids["owner"], ids["position"], queued["attachment"]),
        )
        connection.execute(
            "insert into platform_hr.candidate_drafts("
            "draft_id,owner_internal_user_id,position_id,attachment_id,"
            "batch_request_id,client_request_id,state) "
            "values (%s,%s,%s,%s,%s,%s,'pending')",
            (
                queued["draft"], ids["owner"], ids["position"],
                queued["attachment"], queued["batch"], queued["request"],
            ),
        )

    brain_url = environment["urls"]["platform_brain_worker"]
    app_url = environment["urls"]["platform_control_app"]
    worker_id = "candidate-parser.integration"
    queue = CandidateParserQueue(CandidateRepository(brain_url))
    attempt = queue.claim_next(ClaimNextCandidateDraft(
        queued["attempt"], worker_id, 900
    ))

    codec = _codec()
    conversations = ConversationRepository(
        app_url,
        content_codec=codec,
        mission_repository=MissionRepository(app_url, content_codec=codec),
    )
    commands = ConversationCommandService(conversations, v2_enabled=True)
    app_repository = CandidateParserAppRepository(app_url)
    coordinator = CandidateParserSubmissionCoordinator(app_repository, commands)

    assert coordinator.submit_one() is True
    assert coordinator.submit_one() is False
    with psycopg.connect(environment["admin"]) as connection:
        conversation = connection.execute(
            "select conversation_id from platform_control.conversations "
            "where owner_internal_user_id=%s and started_by_client_request_id=%s",
            (ids["owner"], queued["request"]),
        ).fetchone()
        turn = connection.execute(
            "select turn_id,mission_id from platform_control.conversation_turns "
            "where conversation_id=%s and client_request_id=%s",
            (conversation[0], queued["request"]),
        ).fetchone()
        assert connection.execute(
            "select count(*) from platform_hr.position_conversations "
            "where conversation_id=%s", (conversation[0],),
        ).fetchone()[0] == 0

    provider = CandidateParserInputProvider(app_repository)
    context = ConversationContextBuilder(
        conversations, candidate_parser_input_provider=provider
    ).build(conversation[0], turn[0])
    assert context.active_attachment_ids == (queued["attachment"],)
    assert provider.for_turn(uuid4(), conversation[0], turn[0]) is None

    wrong_attachment = uuid4()
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_attachments.attachments("
            "attachment_id,owner_internal_user_id,conversation_id,source_kind,"
            "original_name_ciphertext,original_name_key_version,"
            "object_ref_ciphertext,object_ref_key_version,immutable_locator,"
            "sha256,state,ready_at) values ("
            "%s,%s,%s,'user_input',%s,1,%s,1,'etag:wrong-parser-input',"
            "%s,'ready',now())",
            (
                wrong_attachment, ids["owner"], conversation[0],
                b"n" * 29, b"o" * 29, b"w" * 32,
            ),
        )
        connection.execute(
            "insert into platform_attachments.bindings("
            "binding_id,attachment_id,owner_internal_user_id,kind,"
            "conversation_id,turn_id) values (%s,%s,%s,'turn_input',%s,%s)",
            (uuid4(), wrong_attachment, ids["owner"], conversation[0], turn[0]),
        )
    with pytest.raises(ValueError):
        provider.for_turn(ids["owner"], conversation[0], turn[0])
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "delete from platform_attachments.bindings where attachment_id=%s",
            (wrong_attachment,),
        )

    ordinary = commands.start(
        ids["owner"], uuid4(), "ordinary HR request",
        mode="direct_agent", direct_agent_id="hr-bot",
    )
    assert provider.for_turn(
        ids["owner"], ordinary.conversation.conversation_id, ordinary.turn.turn_id
    ) is None

    with pytest.raises(CandidateNotFound):
        queue.discover_execution(attempt.attempt_id, worker_id)

    task_id, run_id, job_id, assistant_message_id, relay_worker = (
        uuid4(), uuid4(), uuid4(), uuid4(), f"parser-{uuid4().hex[:12]}"
    )
    sealed = codec.seal_json(
        message_subject(conversation[0], assistant_message_id),
        {
            "text": (
                '{"extracted_facts":{"stable_name":"Lin",'
                '"skills":["Python"]},"identity_candidate_ids":[]}'
            )
        },
    )
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute("set constraints all deferred")
        connection.execute(
            "insert into platform_control.execution_workers("
            "worker_id,allowed_agent_ids,status) values (%s,array['hr-bot'],'active')",
            (relay_worker,),
        )
        connection.execute(
            "insert into platform_control.mission_tasks("
            "task_id,mission_id,agent_id,objective_ciphertext,"
            "encryption_key_version,status,terminal_at) values ("
            "%s,%s,'hr-bot',%s,1,'completed',now())",
            (task_id, turn[1], b"t" * 29),
        )
        connection.execute(
            "insert into platform_control.mission_runs("
            "run_id,mission_id,task_id,phase,agent_id,status,input_ciphertext,"
            "encryption_key_version,terminal_at) values ("
            "%s,%s,%s,'direct','hr-bot','completed',%s,1,now())",
            (run_id, turn[1], task_id, b"r" * 29),
        )
        connection.execute(
            "insert into platform_control.execution_jobs("
            "job_id,run_id,agent_id,payload_ciphertext,encryption_key_version,"
            "status,lease_worker_id,terminal_at) values ("
            "%s,%s,'hr-bot',%s,1,'completed',%s,now())",
            (job_id, run_id, b"payload", relay_worker),
        )
        connection.execute(
            "insert into platform_control.conversation_messages("
            "message_id,conversation_id,seq,role,content_ciphertext,"
            "encryption_key_version,turn_id,mission_id,delivery_status,completed_at) "
            "values (%s,%s,2,'assistant',%s,%s,%s,%s,'completed',now())",
            (
                assistant_message_id, conversation[0], sealed.ciphertext,
                sealed.key_version, turn[0], turn[1],
            ),
        )
        connection.execute(
            "update platform_control.conversation_turns set status='completed',"
            "assistant_message_id=%s,updated_at=now() where turn_id=%s",
            (assistant_message_id, turn[0]),
        )
        connection.execute(
            "update platform_control.missions set status='completed',"
            "terminal_at=now(),updated_at=now() where mission_id=%s",
            (turn[1],),
        )

    runtime = CandidateParserRuntime(
        queue,
        PostgresCandidateParserResultReader(brain_url, codec),
        worker_id=worker_id,
    )
    assert runtime.tick() is True
    ready = CandidateRepository(app_url).draft_for_owner(
        ids["owner"], queued["draft"]
    )
    assert ready.state == "ready"
    assert ready.extracted_facts == {
        "stable_name": "Lin",
        "skills": ["Python"],
    }

    with psycopg.connect(app_url) as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(
                "select * from "
                "platform_hr.read_candidate_draft_execution_result_v70(%s,%s)",
                (attempt.attempt_id, worker_id),
            )
