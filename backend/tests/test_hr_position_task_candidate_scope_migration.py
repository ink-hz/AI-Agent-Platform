from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from test_control_plane_migration import control_database  # noqa: F401
from test_hr_task_result_projection_database import _seed_candidate_scope

from app.hr.models import CreateManualPosition
from app.hr.position_intelligence_models import CreatePositionTaskRequest
from app.hr.position_intelligence_repository import (
    PositionContextConflict,
    PositionContextNotFound,
    PositionIntelligenceRepository,
)
from app.hr.repository import HrPositionRepository

MIGRATION = (
    Path(__file__).parents[1]
    / "control_migrations"
    / "079_hr_position_task_candidate_scope.sql"
)


def test_v79_locks_and_validates_candidate_scope_before_request_insert() -> None:
    assert MIGRATION.is_file(), f"missing migration: {MIGRATION}"
    sql = " ".join(MIGRATION.read_text(encoding="utf-8").lower().split())

    assert "create function platform_hr.create_position_task_request_v79" in sql
    assert "from platform_hr.position_candidates" in sql
    assert "relation.status='active'" in sql
    assert "for update" in sql
    assert sql.index("for update") < sql.index(
        "platform_hr.create_position_task_request_v69"
    )
    assert "session_user not in ('platform_control_app','platform_control_app_preview')" in sql
    assert "revoke all on function platform_hr.create_position_task_request_v79" in sql
    assert "grant execute on function platform_hr.create_position_task_request_v79" in sql


def _candidate_request(
    owner_id, position_id, scope, *, feedback_ids=()
) -> CreatePositionTaskRequest:
    return CreatePositionTaskRequest(
        uuid4(), owner_id, position_id, uuid4(), "a" * 64,
        "candidate_match", scope["context"], (), scope["candidate"],
        scope["relation"], (scope["document"],), (scope["attachment"],),
        feedback_ids, "candidate snapshot",
    )


def _create_v79(connection, command):
    return connection.execute(
        "select (platform_hr.create_position_task_request_v79("
        "%s,%s,%s,%s,%s,%s,%s,%s::uuid[],%s,%s,%s::uuid[],%s::uuid[],"
        "%s::uuid[],%s)).*",
        (
            command.task_request_id, command.owner_id, command.position_id,
            command.client_request_id, command.canonical_payload_sha256,
            command.task_kind, command.expected_context_version_id,
            list(command.material_attachment_ids), command.candidate_id,
            command.position_candidate_id, list(command.document_ids),
            list(command.document_attachment_ids),
            list(command.human_feedback_ids), command.candidate_prompt_context,
        ),
    ).fetchone()


@pytest.mark.postgres
def test_v79_candidate_request_is_atomic_scoped_and_replay_stable(
    control_database,  # noqa: F811
) -> None:
    environment = control_database["environments"]["production"]
    owner_id = uuid4()
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id,display_name,status) values (%s,'v79 owner','active')",
            (owner_id,),
        )
    positions = HrPositionRepository(environment["urls"]["platform_control_app"])
    first_position = positions.create_manual(
        CreateManualPosition(owner_id, uuid4(), uuid4(), "v79 first")
    )
    second_position = positions.create_manual(
        CreateManualPosition(owner_id, uuid4(), uuid4(), "v79 second")
    )
    first = _seed_candidate_scope(environment, owner_id, first_position.position_id)
    second = _seed_candidate_scope(environment, owner_id, second_position.position_id)
    analysis_id, first_feedback_id = uuid4(), uuid4()
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_hr.candidate_analysis_versions("
            "analysis_version_id,owner_internal_user_id,position_candidate_id,"
            "position_id,candidate_id,context_version_id,client_request_id,"
            "version_number,analysis_kind,result,agent_version,model_version) "
            "values (%s,%s,%s,%s,%s,%s,%s,1,'match','{}','agent','model')",
            (analysis_id, owner_id, first["relation"], first_position.position_id,
             first["candidate"], first["context"], uuid4()),
        )
        connection.execute(
            "insert into platform_hr.human_feedback("
            "feedback_id,owner_internal_user_id,position_candidate_id,"
            "analysis_version_id,client_request_id,feedback_kind,conclusion_key,"
            "reason,canonical_payload,payload_sha256) values ("
            "%s,%s,%s,%s,%s,'accepted','scope','accepted','{}',"
            "sha256(convert_to('{}','UTF8')))",
            (first_feedback_id, owner_id, first["relation"], analysis_id, uuid4()),
        )
    repository = PositionIntelligenceRepository(
        environment["urls"]["platform_control_app"]
    )
    valid = _candidate_request(
        owner_id, first_position.position_id, first,
        feedback_ids=(first_feedback_id,),
    )

    created = repository.create_task_request(valid)
    assert created.candidate_snapshot_sha256 == valid.candidate_snapshot_sha256

    with (
        psycopg.connect(environment["admin"]) as connection,
        pytest.raises(psycopg.errors.CheckViolation),
    ):
        connection.execute(
            "update platform_hr.position_task_requests "
            "set candidate_prompt_context='tampered' where task_request_id=%s",
            (valid.task_request_id,),
        )
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_hr.position_candidates set status='archived' "
            "where position_candidate_id=%s",
            (first["relation"],),
        )
        connection.execute(
            "update platform_hr.candidate_documents set status='erased' "
            "where document_id=%s",
            (first["document"],),
        )
        connection.execute(
            "update platform_attachments.attachments set state='scanning' "
            "where attachment_id=%s",
            (first["attachment"],),
        )
        connection.execute(
            "insert into platform_hr.human_feedback("
            "feedback_id,owner_internal_user_id,position_candidate_id,"
            "analysis_version_id,client_request_id,feedback_kind,conclusion_key,"
            "reason,canonical_payload,payload_sha256) values ("
            "%s,%s,%s,%s,%s,'rejected','later','later','{}',"
            "sha256(convert_to('{}','UTF8')))",
            (uuid4(), owner_id, first["relation"], analysis_id, uuid4()),
        )
    assert repository.create_task_request(valid) == created
    with pytest.raises(PositionContextConflict):
        repository.create_task_request(replace(valid, canonical_payload_sha256="c" * 64))

    invalid_request_id = uuid4()
    invalid_client_id = uuid4()
    invalid = CreatePositionTaskRequest(
        invalid_request_id, owner_id, first_position.position_id,
        invalid_client_id, "b" * 64, "candidate_match", first["context"], (),
        first["candidate"], second["relation"], (first["document"],),
        (first["attachment"],), (), "candidate snapshot",
    )
    with pytest.raises(PositionContextNotFound):
        repository.create_task_request(invalid)

    with psycopg.connect(environment["admin"]) as connection:
        assert connection.execute(
            "select count(*) from platform_hr.position_task_requests "
            "where task_request_id=%s or client_request_id=%s",
            (invalid_request_id, invalid_client_id),
        ).fetchone()[0] == 0


@pytest.mark.postgres
def test_v79_holds_candidate_scope_locks_until_request_commit(
    control_database,  # noqa: F811
) -> None:
    environment = control_database["environments"]["production"]
    owner_id = uuid4()
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id,display_name,status) values (%s,'v79 lock','active')",
            (owner_id,),
        )
    position = HrPositionRepository(
        environment["urls"]["platform_control_app"]
    ).create_manual(CreateManualPosition(owner_id, uuid4(), uuid4(), "lock scope"))
    scope = _seed_candidate_scope(environment, owner_id, position.position_id)
    command = _candidate_request(owner_id, position.position_id, scope)

    app = psycopg.connect(environment["urls"]["platform_control_app"])
    try:
        assert _create_v79(app, command) is not None
        mutations = (
            (
                (
                    "update platform_hr.position_candidates set status='archived' "
                    "where position_candidate_id=%s"
                ),
                scope["relation"],
            ),
            (
                (
                    "update platform_hr.candidate_documents set status='erased' "
                    "where document_id=%s"
                ),
                scope["document"],
            ),
            (
                (
                    "update platform_attachments.attachments set state='scanning' "
                    "where attachment_id=%s"
                ),
                scope["attachment"],
            ),
        )
        for statement, identifier in mutations:
            with (
                psycopg.connect(environment["admin"]) as contender,
                pytest.raises(psycopg.errors.QueryCanceled),
            ):
                contender.execute("set statement_timeout='200ms'")
                contender.execute(statement, (identifier,))
        app.commit()
    finally:
        app.close()
    with psycopg.connect(environment["admin"]) as connection:
        assert connection.execute(
            "select count(*) from platform_hr.position_task_requests "
            "where task_request_id=%s and candidate_snapshot_sha256=%s",
            (command.task_request_id, command.candidate_snapshot_sha256),
        ).fetchone()[0] == 1


@pytest.mark.postgres
def test_v79_rejects_each_nonexact_document_state_without_ghost_request(
    control_database,  # noqa: F811
) -> None:
    environment = control_database["environments"]["production"]
    owner_id = uuid4()
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id,display_name,status) values (%s,'v79 docs','active')",
            (owner_id,),
        )
    position = HrPositionRepository(
        environment["urls"]["platform_control_app"]
    ).create_manual(CreateManualPosition(owner_id, uuid4(), uuid4(), "doc scope"))
    scope = _seed_candidate_scope(environment, owner_id, position.position_id)
    repository = PositionIntelligenceRepository(
        environment["urls"]["platform_control_app"]
    )
    base = _candidate_request(owner_id, position.position_id, scope)
    rejected = []

    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_hr.candidate_documents set status='erased' "
            "where document_id=%s",
            (scope["document"],),
        )
    with pytest.raises(PositionContextNotFound):
        repository.create_task_request(base)
    rejected.append(base.task_request_id)
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_hr.candidate_documents set status='active' "
            "where document_id=%s",
            (scope["document"],),
        )
        connection.execute(
            "update platform_attachments.attachments set state='scanning' "
            "where attachment_id=%s",
            (scope["attachment"],),
        )
    scanning = replace(
        base, task_request_id=uuid4(), client_request_id=uuid4()
    )
    with pytest.raises(PositionContextNotFound):
        repository.create_task_request(scanning)
    rejected.append(scanning.task_request_id)
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_attachments.attachments set state='ready' "
            "where attachment_id=%s",
            (scope["attachment"],),
        )
    missing = replace(
        base,
        task_request_id=uuid4(),
        client_request_id=uuid4(),
        document_ids=(uuid4(),),
        candidate_snapshot_sha256=None,
    )
    wrong_attachment = replace(
        base,
        task_request_id=uuid4(),
        client_request_id=uuid4(),
        document_attachment_ids=(uuid4(),),
        candidate_snapshot_sha256=None,
    )
    for command in (missing, wrong_attachment):
        with pytest.raises(PositionContextNotFound):
            repository.create_task_request(command)
        rejected.append(command.task_request_id)
    with psycopg.connect(environment["admin"]) as connection:
        assert connection.execute(
            "select count(*) from platform_hr.position_task_requests "
            "where task_request_id=any(%s::uuid[])",
            (rejected,),
        ).fetchone()[0] == 0


@pytest.mark.postgres
def test_v69_request_entrypoint_is_not_callable_by_application_role(
    control_database,  # noqa: F811
) -> None:
    environment = control_database["environments"]["production"]
    with (
        psycopg.connect(
            environment["urls"]["platform_control_app"]
        ) as connection,
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        connection.execute(
            "select platform_hr.create_position_task_request_v69("
            "%s,%s,%s,%s,%s,'freeform',null,'{}'::uuid[],null,null)",
            (uuid4(), uuid4(), uuid4(), uuid4(), "a" * 64),
        )


@pytest.mark.postgres
@pytest.mark.parametrize("environment_name", ("production", "preview"))
def test_legacy_task_record_entrypoints_are_denied_to_every_runtime_writer(
    control_database,  # noqa: F811
    environment_name,
) -> None:
    environment = control_database["environments"][environment_name]
    app_role = environment["roles"][1]
    maintenance_role = environment["roles"][5]
    brain_role = environment["roles"][6]
    calls = (
        (
            (
                "select platform_hr.create_position_task_record_v69("
                "null::uuid,null::uuid,null::uuid,null::uuid,'freeform',"
                "null::uuid,null::uuid,'{}'::uuid[],null::uuid,null::uuid,"
                "'{}'::uuid[],'{}'::uuid[],null::uuid,null::uuid,'prompt',%s)"
            ),
            "a" * 64,
        ),
        (
            (
                "select platform_hr.create_position_task_record_v71("
                "null::uuid,null::uuid,null::uuid,null::uuid,'freeform',"
                "null::uuid,null::uuid,'{}'::uuid[],null::uuid,null::uuid,"
                "'{}'::uuid[],'{}'::uuid[],null::uuid,null::uuid,'prompt',"
                "%s,'model')"
            ),
            "a" * 64,
        ),
    )
    for role in (app_role, brain_role):
        for statement, canonical_hash in calls:
            with (
                psycopg.connect(environment["urls"][role]) as connection,
                pytest.raises(psycopg.errors.InsufficientPrivilege),
            ):
                connection.execute(statement, (canonical_hash,))
    with psycopg.connect(environment["admin"]) as connection:
        signatures = (
            (
                "platform_hr.create_position_task_record_v69(uuid,uuid,uuid,uuid,"
                "text,uuid,uuid,uuid[],uuid,uuid,uuid[],uuid[],uuid,uuid,text,text)"
            ),
            (
                "platform_hr.create_position_task_record_v71(uuid,uuid,uuid,uuid,"
                "text,uuid,uuid,uuid[],uuid,uuid,uuid[],uuid[],uuid,uuid,text,"
                "text,text)"
            ),
        )
        for role in (app_role, brain_role, maintenance_role):
            assert all(
                connection.execute(
                    "select has_function_privilege(%s,%s,'EXECUTE')",
                    (role, signature),
                ).fetchone()[0] is False
                for signature in signatures
            )
