from __future__ import annotations

import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
import test_control_plane_migration as control_migration
from app.hr.candidate_models import (
    AttachCandidateDraftExecution,
    ClaimNextCandidateDraft,
    CompleteCandidateDraft,
    FailCandidateDraft,
)
from app.hr.candidate_repository import (
    CandidateConflict,
    CandidateNotFound,
    CandidateRepository,
)

BACKEND = Path(__file__).parents[1]
WORKTREE = BACKEND.parent
ROOT_REPOSITORY = WORKTREE.parents[1]
POSITION_WORKTREE = WORKTREE.parent / "hr-r12-position"


@pytest.fixture(scope="module")
def candidate_database(tmp_path_factory):
    migrations = tmp_path_factory.mktemp("candidate-integrated-migrations")
    for source in (BACKEND / "control_migrations").glob("*.sql"):
        if int(source.name.split("_", 1)[0]) <= 66:
            shutil.copy2(source, migrations / source.name)
    for version in (67, 68):
        source = next((ROOT_REPOSITORY / "backend/control_migrations").glob(
            f"{version:03d}_*.sql"
        ))
        shutil.copy2(source, migrations / source.name)
    position_source = next(
        (POSITION_WORKTREE / "backend/control_migrations").glob(
            "*_hr_position_intelligence.sql"
        )
    )
    shutil.copy2(position_source, migrations / "069_hr_position_intelligence.sql")
    shutil.copy2(
        BACKEND / "control_migrations/070_hr_candidate_intelligence.sql",
        migrations / "070_hr_candidate_intelligence.sql",
    )

    original = control_migration.MIGRATIONS
    control_migration.MIGRATIONS = migrations
    fixture = control_migration.control_database.__wrapped__()
    try:
        yield next(fixture)
    finally:
        control_migration.MIGRATIONS = original
        try:
            next(fixture)
        except StopIteration:
            pass


def _seed_candidate_scope(environment):
    ids = {name: uuid4() for name in (
        "owner", "position", "context", "attachment", "batch", "draft",
        "draft_request", "confirmation", "candidate", "document", "relation",
    )}
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.internal_users("
            "internal_user_id,display_name,status) values (%s,'HR','active')",
            (ids["owner"],),
        )
        connection.execute(
            "insert into platform_hr.positions("
            "position_id,owner_internal_user_id,client_request_id,source_kind,title) "
            "values (%s,%s,%s,'manual','Engineer')",
            (ids["position"], ids["owner"], uuid4()),
        )
        connection.execute(
            "insert into platform_hr.position_context_versions("
            "context_version_id,owner_internal_user_id,position_id,client_request_id,"
            "version_number,state,modules,summary,created_by,confirmed_by,"
            "confirmed_at,confirmed_module_names) values ("
            "%s,%s,%s,%s,1,'confirmed','{\"jd\":{}}','Confirmed',%s,%s,now(),"
            "array['jd'])",
            (
                ids["context"], ids["owner"], ids["position"], uuid4(),
                ids["owner"], ids["owner"],
            ),
        )
        connection.execute(
            "update platform_hr.positions set current_context_version_id=%s "
            "where position_id=%s",
            (ids["context"], ids["position"]),
        )
        connection.execute(
            "insert into platform_attachments.attachments("
            "attachment_id,owner_internal_user_id,source_kind,"
            "original_name_ciphertext,original_name_key_version,"
            "object_ref_ciphertext,object_ref_key_version,immutable_locator,"
            "sha256,state,ready_at) values ("
            "%s,%s,'user_input',%s,1,%s,1,'etag:resume-v1',%s,'ready',now())",
            (ids["attachment"], ids["owner"], b"n" * 29, b"o" * 29, b"h" * 32),
        )
        connection.execute(
            "insert into platform_hr.candidate_draft_batches("
            "batch_request_id,owner_internal_user_id,position_id,attachment_ids) "
            "values (%s,%s,%s,array[%s]::uuid[])",
            (ids["batch"], ids["owner"], ids["position"], ids["attachment"]),
        )
        connection.execute(
            "insert into platform_hr.candidate_drafts("
            "draft_id,owner_internal_user_id,position_id,attachment_id,"
            "batch_request_id,client_request_id,state,extracted_facts,row_version) "
            "values (%s,%s,%s,%s,%s,%s,'ready','{\"skills\":[\"Python\"]}',2)",
            (
                ids["draft"], ids["owner"], ids["position"], ids["attachment"],
                ids["batch"], ids["draft_request"],
            ),
        )
    return ids


def _seed_execution_identity(connection, owner_id, request_id, *, agent_id="hr-bot"):
    ids = {name: uuid4() for name in (
        "mission", "task", "run", "job", "conversation", "message", "turn"
    )}
    connection.execute(
        "insert into platform_control.missions("
        "mission_id,owner_internal_user_id,client_request_id,mode,"
        "direct_agent_id,status) values (%s,%s,%s,'direct_agent',%s,'delegated')",
        (ids["mission"], owner_id, request_id, agent_id),
    )
    connection.execute(
        "insert into platform_control.mission_tasks("
        "task_id,mission_id,agent_id,objective_ciphertext,"
        "encryption_key_version,status) values ("
        "%s,%s,%s,%s,1,'queued')",
        (ids["task"], ids["mission"], agent_id, b"o" * 29),
    )
    connection.execute(
        "insert into platform_control.mission_runs("
        "run_id,mission_id,task_id,phase,agent_id,status,input_ciphertext,"
        "encryption_key_version) values ("
        "%s,%s,%s,'direct',%s,'queued',%s,1)",
        (ids["run"], ids["mission"], ids["task"], agent_id, b"i" * 29),
    )
    connection.execute(
        "insert into platform_control.execution_jobs("
        "job_id,run_id,agent_id,payload_ciphertext,encryption_key_version,status) "
        "values (%s,%s,%s,%s,1,'queued')",
        (ids["job"], ids["run"], agent_id, b"payload"),
    )
    connection.execute(
        "insert into platform_control.conversations("
        "conversation_id,owner_internal_user_id,started_by_client_request_id,"
        "mode,direct_agent_id,title) values ("
        "%s,%s,%s,'direct_agent',%s,'Resume extraction')",
        (ids["conversation"], owner_id, request_id, agent_id),
    )
    connection.execute("set constraints all deferred")
    connection.execute(
        "insert into platform_control.conversation_messages("
        "message_id,conversation_id,seq,role,content_ciphertext,"
        "encryption_key_version,turn_id,mission_id,delivery_status) values ("
        "%s,%s,1,'user',%s,1,%s,%s,'accepted')",
        (
            ids["message"], ids["conversation"], b"m" * 29,
            ids["turn"], ids["mission"],
        ),
    )
    connection.execute(
        "insert into platform_control.conversation_turns("
        "turn_id,conversation_id,user_message_id,client_request_id,mission_id,status) "
        "values (%s,%s,%s,%s,%s,'accepted')",
        (
            ids["turn"], ids["conversation"], ids["message"], request_id,
            ids["mission"],
        ),
    )
    return ids


def _bind_turn_input(connection, owner_id, execution, attachment_id):
    connection.execute(
        "update platform_attachments.attachments set conversation_id=%s "
        "where attachment_id=%s and owner_internal_user_id=%s",
        (execution["conversation"], attachment_id, owner_id),
    )
    connection.execute(
        "insert into platform_attachments.bindings("
        "binding_id,attachment_id,owner_internal_user_id,kind,conversation_id,turn_id) "
        "values (%s,%s,%s,'turn_input',%s,%s)",
        (
            uuid4(), attachment_id, owner_id,
            execution["conversation"], execution["turn"],
        ),
    )


def _complete_execution_identity(connection, execution, relay_worker):
    connection.execute(
        "update platform_control.execution_jobs set status='completed',"
        "lease_worker_id=%s,terminal_at=now() where job_id=%s",
        (relay_worker, execution["job"]),
    )
    connection.execute(
        "update platform_control.mission_runs set status='completed',terminal_at=now() "
        "where run_id=%s", (execution["run"],),
    )
    connection.execute(
        "update platform_control.missions set status='completed',terminal_at=now() "
        "where mission_id=%s", (execution["mission"],),
    )
    connection.execute(
        "update platform_control.conversation_turns set status='completed' "
        "where turn_id=%s", (execution["turn"],),
    )


@pytest.mark.postgres
def test_real_068_069_070_confirmation_replay_rebase_and_erasure_boundary(
    candidate_database,
) -> None:
    environment = candidate_database["environments"]["production"]
    ids = _seed_candidate_scope(environment)
    app_url = environment["urls"]["platform_control_app"]
    parameters = (
        ids["owner"], ids["draft"], ids["confirmation"], 2,
        ids["candidate"], None, ids["document"], ids["relation"],
        ids["context"], "Candidate", '{"skills":["Python"]}',
    )
    statement = (
        "select (platform_hr.confirm_candidate_draft_v70("
        "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)).*"
    )
    with psycopg.connect(app_url) as connection:
        first = connection.execute(statement, parameters).fetchone()
        replay = connection.execute(statement, parameters).fetchone()
        assert first == replay
        connection.commit()

        with pytest.raises(psycopg.errors.UniqueViolation):
            connection.execute(statement, (*parameters[:-1], '{"skills":["Go"]}'))
        connection.rollback()

        analysis_id = uuid4()
        connection.execute(
            "select platform_hr.create_candidate_analysis_v70("
            "%s,%s,%s,%s,%s,'match',array[%s]::uuid[],array[]::uuid[],"
            "'{\"summary\":\"fit\"}'::jsonb,'[]'::jsonb,'[]'::jsonb,"
            "'[]'::jsonb,'[]'::jsonb,'hr-r12','model-v1')",
            (
                analysis_id, ids["owner"], ids["relation"], ids["context"],
                uuid4(), ids["document"],
            ),
        )
        stale_feedback_id = uuid4()
        connection.execute(
            "select platform_hr.append_human_feedback_v70("
            "%s,%s,%s,%s,%s,'accepted','summary',null,'old context')",
            (
                stale_feedback_id, ids["owner"], ids["relation"],
                analysis_id, uuid4(),
            ),
        )
        connection.commit()

    second = {name: uuid4() for name in (
        "context", "attachment", "batch", "draft", "draft_request",
        "confirmation", "candidate", "document", "relation",
    )}
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_hr.position_context_versions set state='superseded' "
            "where context_version_id=%s",
            (ids["context"],),
        )
        connection.execute(
            "insert into platform_hr.position_context_versions("
            "context_version_id,owner_internal_user_id,position_id,client_request_id,"
            "version_number,state,modules,summary,created_by,confirmed_by,"
            "confirmed_at,confirmed_module_names) values ("
            "%s,%s,%s,%s,2,'confirmed','{\"jd\":{}}','Rebased',%s,%s,now(),"
            "array['jd'])",
            (
                second["context"], ids["owner"], ids["position"], uuid4(),
                ids["owner"], ids["owner"],
            ),
        )
        connection.execute(
            "update platform_hr.positions set current_context_version_id=%s "
            "where position_id=%s",
            (second["context"], ids["position"]),
        )
        connection.execute(
            "insert into platform_attachments.attachments("
            "attachment_id,owner_internal_user_id,source_kind,"
            "original_name_ciphertext,original_name_key_version,"
            "object_ref_ciphertext,object_ref_key_version,immutable_locator,"
            "sha256,state,ready_at) values ("
            "%s,%s,'user_input',%s,1,%s,1,'etag:resume-v2',%s,'ready',now())",
            (
                second["attachment"], ids["owner"], b"n" * 29,
                b"o" * 29, b"i" * 32,
            ),
        )
        connection.execute(
            "insert into platform_hr.candidate_draft_batches("
            "batch_request_id,owner_internal_user_id,position_id,attachment_ids) "
            "values (%s,%s,%s,array[%s]::uuid[])",
            (second["batch"], ids["owner"], ids["position"], second["attachment"]),
        )
        connection.execute(
            "insert into platform_hr.candidate_drafts("
            "draft_id,owner_internal_user_id,position_id,attachment_id,"
            "batch_request_id,client_request_id,state,extracted_facts,"
            "identity_candidates,row_version) values ("
            "%s,%s,%s,%s,%s,%s,'ready','{\"skills\":[\"Python\"]}',"
            "array[%s]::uuid[],2)",
            (
                second["draft"], ids["owner"], ids["position"],
                second["attachment"], second["batch"], second["draft_request"],
                ids["candidate"],
            ),
        )

    second_parameters = (
        ids["owner"], second["draft"], second["confirmation"], 2,
        second["candidate"], ids["candidate"], second["document"],
        second["relation"], second["context"], "Candidate",
        '{"skills":["Python"]}',
    )
    with psycopg.connect(app_url) as connection:
        rebased = connection.execute(statement, second_parameters).fetchone()
        assert rebased[0] == ids["relation"]
        assert rebased[4] == second["context"]
        assert rebased[8] == 2
        assert connection.execute(
            "select context_version_id from platform_hr.candidate_analysis_versions "
            "where analysis_version_id=%s", (analysis_id,),
        ).fetchone()[0] == ids["context"]
        assert connection.execute(statement, parameters).fetchone() == first
        current_analysis_id, feedback_id = uuid4(), uuid4()
        connection.execute(
            "select platform_hr.create_candidate_analysis_v70("
            "%s,%s,%s,%s,%s,'match',array[%s]::uuid[],array[]::uuid[],"
            "'{\"summary\":\"current\"}'::jsonb,'[]'::jsonb,'[]'::jsonb,"
            "'[]'::jsonb,'[]'::jsonb,'hr-r12','model-v1')",
            (
                current_analysis_id, ids["owner"], ids["relation"],
                second["context"], uuid4(), second["document"],
            ),
        )
        connection.execute(
            "select platform_hr.append_human_feedback_v70("
            "%s,%s,%s,%s,%s,'accepted','summary',null,'reviewed')",
            (
                feedback_id, ids["owner"], ids["relation"],
                current_analysis_id, uuid4(),
            ),
        )
        connection.commit()

    scoped_feedback = CandidateRepository(app_url).feedback_for_candidate_context(
        ids["owner"], ids["relation"], second["context"]
    )
    assert tuple(item.feedback_id for item in scoped_feedback) == (feedback_id,)
    with psycopg.connect(app_url) as connection:
        with pytest.raises(psycopg.errors.NoDataFound):
            connection.execute(
                "select platform_hr.create_candidate_analysis_v70("
                "%s,%s,%s,%s,%s,'match',array[%s]::uuid[],array[%s]::uuid[],"
                "'{\"summary\":\"must reject stale feedback\"}'::jsonb,"
                "'[]'::jsonb,'[]'::jsonb,'[]'::jsonb,'[]'::jsonb,"
                "'hr-r12','model-v1')",
                (
                    uuid4(), ids["owner"], ids["relation"], second["context"],
                    uuid4(), second["document"], stale_feedback_id,
                ),
            )

    with psycopg.connect(environment["admin"]) as connection:
        assert connection.execute(
            "select platform_hr.validate_candidate_task_inputs_v69("
            "%s,%s,%s,%s,%s,array[%s]::uuid[],array[%s]::uuid[])",
            (
                ids["owner"], ids["position"], second["context"],
                ids["candidate"], ids["relation"], second["attachment"],
                feedback_id,
            ),
        ).fetchone()[0] is True
        assert connection.execute(
            "select platform_hr.validate_candidate_task_inputs_v69("
            "%s,%s,%s,%s,%s,array[%s]::uuid[],array[]::uuid[])",
            (
                ids["owner"], ids["position"], ids["context"],
                ids["candidate"], ids["relation"], second["attachment"],
            ),
        ).fetchone()[0] is False

    with psycopg.connect(environment["admin"]) as connection:
        document = CandidateRepository(app_url).document_for_owner(
            ids["owner"], ids["document"]
        )
        assert document.status == "active"
        connection.execute(
            "insert into platform_attachments.erasure_jobs("
            "erasure_job_id,attachment_id,requested_by_internal_user_id,"
            "reason_ciphertext,reason_key_version,reason_sha256) "
            "values (%s,%s,%s,%s,1,%s)",
            (uuid4(), ids["attachment"], ids["owner"], b"r" * 29, b"s" * 32),
        )

    erased = CandidateRepository(app_url).document_for_owner(
        ids["owner"], ids["document"]
    )
    assert erased.status == "erased"
    with psycopg.connect(environment["admin"]) as connection:
        assert connection.execute(
            "select platform_hr.validate_candidate_task_inputs_v69("
            "%s,%s,%s,%s,%s,array[%s]::uuid[],array[]::uuid[])",
            (
                ids["owner"], ids["position"], second["context"],
                ids["candidate"], ids["relation"], ids["attachment"],
            ),
        ).fetchone()[0] is False
    with psycopg.connect(app_url) as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(
                "select platform_hr.start_candidate_draft_v70(%s,%s,%s,%s)",
                (ids["owner"], ids["draft"], uuid4(), 3),
            )

    queued = {name: uuid4() for name in (
        "attachment", "batch", "draft", "draft_request", "attempt"
    )}
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_attachments.attachments("
            "attachment_id,owner_internal_user_id,source_kind,"
            "original_name_ciphertext,original_name_key_version,"
            "object_ref_ciphertext,object_ref_key_version,immutable_locator,"
            "sha256,state,ready_at) values ("
            "%s,%s,'user_input',%s,1,%s,1,'etag:queued-resume',%s,'ready',now())",
            (
                queued["attachment"], ids["owner"], b"n" * 29,
                b"o" * 29, b"j" * 32,
            ),
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
                queued["attachment"], queued["batch"], queued["draft_request"],
            ),
        )
        execution = _seed_execution_identity(
            connection, ids["owner"], queued["draft_request"]
        )
        attacker_owner = uuid4()
        connection.execute(
            "insert into platform_control.internal_users("
            "internal_user_id,display_name,status) values (%s,'Other HR','active')",
            (attacker_owner,),
        )
        attacker = _seed_execution_identity(
            connection, attacker_owner, queued["draft_request"]
        )
        wrong_request = _seed_execution_identity(
            connection, ids["owner"], uuid4()
        )
        relay_worker = f"candidate-test-{uuid4().hex[:12]}"
        connection.execute(
            "insert into platform_control.execution_workers("
            "worker_id,allowed_agent_ids,status) values (%s,array['hr-bot'],'active')",
            (relay_worker,),
        )

    brain_url = environment["urls"]["platform_brain_worker"]
    brain_repository = CandidateRepository(brain_url)
    claim = ClaimNextCandidateDraft(
        queued["attempt"], "candidate-parser-1", 300,
    )
    attempt = brain_repository.claim_next_draft(claim)

    assert attempt.owner_id == ids["owner"]
    assert attempt.draft_id == queued["draft"]
    assert attempt.position_id == ids["position"]
    assert attempt.attachment_id == queued["attachment"]
    assert attempt.draft_client_request_id == queued["draft_request"]
    assert attempt.execution_job_id is None
    recovered = CandidateRepository(brain_url).recover_draft_attempt(
        attempt.attempt_id, attempt.worker_id
    )
    assert recovered == attempt

    with pytest.raises(CandidateNotFound):
        brain_repository.attach_draft_execution(AttachCandidateDraftExecution(
            attempt.attempt_id, attempt.worker_id,
            execution["job"], execution["conversation"], execution["turn"],
        ))
    with psycopg.connect(environment["admin"]) as connection:
        _complete_execution_identity(connection, attacker, relay_worker)
        _complete_execution_identity(connection, execution, relay_worker)
        _complete_execution_identity(connection, wrong_request, relay_worker)

    with pytest.raises(CandidateNotFound):
        brain_repository.attach_draft_execution(AttachCandidateDraftExecution(
            attempt.attempt_id, attempt.worker_id,
            attacker["job"], attacker["conversation"], attacker["turn"],
        ))
    with pytest.raises(CandidateNotFound):
        brain_repository.attach_draft_execution(AttachCandidateDraftExecution(
            attempt.attempt_id, attempt.worker_id,
            wrong_request["job"], wrong_request["conversation"],
            wrong_request["turn"],
        ))
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_control.execution_jobs set agent_id='unregistered-parser' "
            "where job_id=%s", (execution["job"],),
        )
    with pytest.raises(CandidateNotFound):
        brain_repository.attach_draft_execution(AttachCandidateDraftExecution(
            attempt.attempt_id, attempt.worker_id,
            execution["job"], execution["conversation"], execution["turn"],
        ))
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_control.execution_jobs set agent_id='hr-bot' "
            "where job_id=%s", (execution["job"],),
        )
    extra_attachment = uuid4()
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_attachments.attachments("
            "attachment_id,owner_internal_user_id,conversation_id,source_kind,"
            "original_name_ciphertext,original_name_key_version,"
            "object_ref_ciphertext,object_ref_key_version,immutable_locator,"
            "sha256,state,ready_at) values ("
            "%s,%s,%s,'user_input',%s,1,%s,1,'etag:extra-input',%s,'ready',now())",
            (
                extra_attachment, ids["owner"], execution["conversation"],
                b"n" * 29, b"o" * 29, b"x" * 32,
            ),
        )
        _bind_turn_input(connection, ids["owner"], execution, extra_attachment)
    with pytest.raises(CandidateNotFound):
        brain_repository.attach_draft_execution(AttachCandidateDraftExecution(
            attempt.attempt_id, attempt.worker_id,
            execution["job"], execution["conversation"], execution["turn"],
        ))
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "delete from platform_attachments.bindings where attachment_id=%s "
            "and kind='turn_input'", (extra_attachment,),
        )
    bound = brain_repository.attach_draft_execution(AttachCandidateDraftExecution(
        attempt.attempt_id, attempt.worker_id,
        execution["job"], execution["conversation"], execution["turn"],
    ))
    complete = CompleteCandidateDraft(
        ids["owner"], queued["draft"], uuid4(), attempt.claimed_row_version,
        {"stable_name": "Queued Candidate", "skills": ["SQL"]}, (),
    )

    with pytest.raises(CandidateNotFound):
        brain_repository.complete_claimed_draft(
            attempt.attempt_id, "different-worker", complete
        )
    with pytest.raises(CandidateNotFound):
        brain_repository.complete_claimed_draft(
            attempt.attempt_id, attempt.worker_id,
            replace(complete, owner_id=attacker_owner),
        )
    with pytest.raises(CandidateNotFound):
        brain_repository.complete_claimed_draft(
            attempt.attempt_id, attempt.worker_id,
            replace(complete, draft_id=uuid4()),
        )
    ready = brain_repository.complete_claimed_draft(
        attempt.attempt_id, attempt.worker_id, complete
    )
    replay_ready = brain_repository.complete_claimed_draft(
        attempt.attempt_id, attempt.worker_id, complete
    )
    with pytest.raises(CandidateConflict):
        brain_repository.complete_claimed_draft(
            attempt.attempt_id, attempt.worker_id,
            replace(complete, extracted_facts={"stable_name": "Changed"}),
        )
    persisted = brain_repository.recover_draft_attempt(
        attempt.attempt_id, attempt.worker_id
    )

    assert ready == replay_ready
    assert ready.state == "ready"
    assert persisted.state == "completed"
    assert bound.execution_job_id == execution["job"]

    racing = {name: uuid4() for name in (
        "attachment", "batch", "draft", "draft_request", "erasure"
    )}
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_attachments.attachments("
            "attachment_id,owner_internal_user_id,source_kind,"
            "original_name_ciphertext,original_name_key_version,"
            "object_ref_ciphertext,object_ref_key_version,immutable_locator,"
            "sha256,state,ready_at) values ("
            "%s,%s,'user_input',%s,1,%s,1,'etag:racing-resume',%s,'ready',now())",
            (
                racing["attachment"], ids["owner"], b"n" * 29,
                b"o" * 29, b"k" * 32,
            ),
        )
        connection.execute(
            "insert into platform_hr.candidate_draft_batches("
            "batch_request_id,owner_internal_user_id,position_id,attachment_ids) "
            "values (%s,%s,%s,array[%s]::uuid[])",
            (racing["batch"], ids["owner"], ids["position"], racing["attachment"]),
        )
        connection.execute(
            "insert into platform_hr.candidate_drafts("
            "draft_id,owner_internal_user_id,position_id,attachment_id,"
            "batch_request_id,client_request_id,state) "
            "values (%s,%s,%s,%s,%s,%s,'pending')",
            (
                racing["draft"], ids["owner"], ids["position"],
                racing["attachment"], racing["batch"], racing["draft_request"],
            ),
        )

    def claim_racing_draft():
        return CandidateRepository(brain_url).claim_next_draft(
            ClaimNextCandidateDraft(uuid4(), "candidate-parser-erasure", 300)
        )

    with psycopg.connect(app_url) as erasure_connection:
        erasure_connection.execute(
            "select platform_attachments.request_attachment_erasure_v64("
            "%s,%s,%s,%s,%s,1,%s)",
            (
                racing["attachment"], ids["owner"], None, racing["erasure"],
                b"r" * 29, b"s" * 32,
            ),
        )
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(claim_racing_draft)
            erasure_connection.commit()
            with pytest.raises(CandidateNotFound):
                future.result(timeout=5)

    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_attachments.erasure_jobs set state='completed',"
            "completed_at=now() where erasure_job_id=%s",
            (racing["erasure"],),
        )
    with pytest.raises(CandidateNotFound):
        claim_racing_draft()

    lease = {name: uuid4() for name in (
        "attachment", "batch", "draft", "draft_request", "first", "second"
    )}
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_attachments.attachments("
            "attachment_id,owner_internal_user_id,source_kind,"
            "original_name_ciphertext,original_name_key_version,"
            "object_ref_ciphertext,object_ref_key_version,immutable_locator,"
            "sha256,state,ready_at) values ("
            "%s,%s,'user_input',%s,1,%s,1,'etag:lease-resume',%s,'ready',now())",
            (lease["attachment"], ids["owner"], b"n" * 29, b"o" * 29, b"l" * 32),
        )
        connection.execute(
            "insert into platform_hr.candidate_draft_batches("
            "batch_request_id,owner_internal_user_id,position_id,attachment_ids) "
            "values (%s,%s,%s,array[%s]::uuid[])",
            (lease["batch"], ids["owner"], ids["position"], lease["attachment"]),
        )
        connection.execute(
            "insert into platform_hr.candidate_drafts("
            "draft_id,owner_internal_user_id,position_id,attachment_id,"
            "batch_request_id,client_request_id,state) "
            "values (%s,%s,%s,%s,%s,%s,'pending')",
            (
                lease["draft"], ids["owner"], ids["position"], lease["attachment"],
                lease["batch"], lease["draft_request"],
            ),
        )
    first = brain_repository.claim_next_draft(ClaimNextCandidateDraft(
        lease["first"], "candidate-parser-crashed", 30,
    ))
    with psycopg.connect(environment["admin"]) as connection:
        recovered_execution = _seed_execution_identity(
            connection, ids["owner"], lease["draft_request"]
        )
        _complete_execution_identity(connection, recovered_execution, relay_worker)
    first_bound = brain_repository.attach_draft_execution(
        AttachCandidateDraftExecution(
            first.attempt_id, first.worker_id, recovered_execution["job"],
            recovered_execution["conversation"], recovered_execution["turn"],
        )
    )
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_hr.candidate_draft_processing_attempts "
            "set lease_expires_at=now()-interval '1 second' where attempt_id=%s",
            (first.attempt_id,),
        )
    second = CandidateRepository(brain_url).claim_next_draft(ClaimNextCandidateDraft(
        lease["second"], "candidate-parser-restarted", 300,
    ))
    expired = brain_repository.recover_draft_attempt(
        first.attempt_id, first.worker_id
    )

    assert expired.state == "expired"
    assert first_bound.execution_job_id == recovered_execution["job"]
    assert second.draft_id == first.draft_id
    assert second.claimed_row_version == first.claimed_row_version + 2

    recovered_bound = brain_repository.attach_draft_execution(
        AttachCandidateDraftExecution(
            second.attempt_id, second.worker_id, recovered_execution["job"],
            recovered_execution["conversation"], recovered_execution["turn"],
        )
    )
    assert recovered_bound.execution_job_id == recovered_execution["job"]
    failure = FailCandidateDraft(
        ids["owner"], lease["draft"], uuid4(),
        second.claimed_row_version, "parse_failed",
    )
    failed = brain_repository.fail_claimed_draft(
        second.attempt_id, second.worker_id, failure
    )
    replay_failed = brain_repository.fail_claimed_draft(
        second.attempt_id, second.worker_id, failure
    )
    with pytest.raises(CandidateConflict):
        brain_repository.fail_claimed_draft(
            second.attempt_id, second.worker_id,
            replace(failure, error_code="different_failure"),
        )
    assert failed == replay_failed
    assert failed.state == "failed"
