from __future__ import annotations

# Pytest fixtures are imported into this module's namespace for discovery.
# ruff: noqa: F401,F811
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb
from test_control_plane_migration import control_database

MIGRATION = (
    Path(__file__).parents[1]
    / "control_migrations"
    / "064_conversation_attachments.sql"
)
TABLES = (
    "attachments",
    "uploads",
    "bindings",
    "artifacts",
    "artifact_versions",
    "derivatives",
    "task_grants",
    "access_events",
    "processing_jobs",
    "erasure_jobs",
    "message_citations",
    "conversation_read_state",
)
ATTACHMENT_STATES = {
    "uploading",
    "validating",
    "scanning",
    "ready",
    "quarantined",
    "rejected",
    "deleted",
}
BINDING_KINDS = {
    "conversation_material",
    "message_input",
    "turn_input",
    "task_input",
    "task_output",
    "message_output",
}


def _seed_task(
    connection: psycopg.Connection,
    *,
    agent_id: str = "hr-agent",
    task_status: str = "running",
) -> dict[str, object]:
    owner_id = uuid4()
    conversation_id = uuid4()
    message_id = uuid4()
    turn_id = uuid4()
    mission_id = uuid4()
    task_id = uuid4()
    connection.execute(
        "insert into platform_control.internal_users "
        "(internal_user_id,display_name,status) values (%s,'Attachment Owner','active')",
        (owner_id,),
    )
    connection.execute(
        "insert into platform_control.conversations "
        "(conversation_id,owner_internal_user_id,started_by_client_request_id,"
        "mode,title,status) values (%s,%s,%s,'brain','Attachment Test','active')",
        (conversation_id, owner_id, uuid4()),
    )
    connection.execute("set constraints all deferred")
    connection.execute(
        "insert into platform_control.conversation_messages "
        "(message_id,conversation_id,seq,role,content_ciphertext,"
        "encryption_key_version,turn_id,mission_id,delivery_status) "
        "values (%s,%s,1,'user',%s,1,%s,%s,'accepted')",
        (message_id, conversation_id, b"m" * 29, turn_id, mission_id),
    )
    connection.execute(
        "insert into platform_control.conversation_turns "
        "(turn_id,conversation_id,user_message_id,client_request_id,mission_id,status) "
        "values (%s,%s,%s,%s,%s,'accepted')",
        (turn_id, conversation_id, message_id, uuid4(), mission_id),
    )
    connection.execute(
        "insert into platform_control.missions "
        "(mission_id,owner_internal_user_id,client_request_id,mode,status,"
        "conversation_id,turn_id,triggering_message_id) "
        "values (%s,%s,%s,'brain','planning',%s,%s,%s)",
        (mission_id, owner_id, uuid4(), conversation_id, turn_id, message_id),
    )
    terminal_at = datetime.now(UTC) if task_status == "completed" else None
    connection.execute(
        "insert into platform_control.mission_tasks "
        "(task_id,mission_id,agent_id,objective_ciphertext,encryption_key_version,"
        "status,started_at,terminal_at) values (%s,%s,%s,%s,1,%s,now(),%s)",
        (task_id, mission_id, agent_id, b"o" * 29, task_status, terminal_at),
    )
    connection.commit()
    return {
        "owner_id": owner_id,
        "conversation_id": conversation_id,
        "message_id": message_id,
        "turn_id": turn_id,
        "mission_id": mission_id,
        "task_id": task_id,
        "agent_id": agent_id,
    }


def _insert_attachment(
    connection: psycopg.Connection,
    context: dict[str, object],
    *,
    state: str = "ready",
) -> object:
    attachment_id = uuid4()
    connection.execute(
        "insert into platform_attachments.attachments "
        "(attachment_id,owner_internal_user_id,conversation_id,source_kind,"
        "original_name_ciphertext,original_name_key_version,"
        "object_ref_ciphertext,object_ref_key_version,detected_mime,size_bytes,"
        "sha256,state,ready_at) values (%s,%s,%s,'user_input',%s,1,%s,1,"
        "'application/pdf',128,%s,%s,case when %s='ready' then now() end)",
        (
            attachment_id,
            context["owner_id"],
            context["conversation_id"],
            b"n" * 29,
            b"r" * 29,
            b"h" * 32,
            state,
            state,
        ),
    )
    connection.commit()
    return attachment_id


def _insert_task_input_binding(
    connection: psycopg.Connection,
    context: dict[str, object],
    attachment_id: object,
) -> None:
    connection.execute(
        "insert into platform_attachments.bindings "
        "(binding_id,attachment_id,owner_internal_user_id,kind,conversation_id,"
        "task_id,agent_id) values (%s,%s,%s,'task_input',%s,%s,%s)",
        (
            uuid4(),
            attachment_id,
            context["owner_id"],
            context["conversation_id"],
            context["task_id"],
            context["agent_id"],
        ),
    )
    connection.commit()


def _columns(
    connection: psycopg.Connection,
    table: str,
    schema: str = "platform_attachments",
) -> dict[str, str | None]:
    return {
        row[0]: row[1]
        for row in connection.execute(
            "select column_name,column_default from information_schema.columns "
            "where table_schema=%s and table_name=%s",
            (schema, table),
        )
    }


def _checks(connection: psycopg.Connection, schema: str, table: str) -> str:
    return "\n".join(
        row[0]
        for row in connection.execute(
            "select pg_get_constraintdef(oid) from pg_constraint "
            "where conrelid=(%s || '.' || %s)::regclass and contype='c'",
            (schema, table),
        )
    )


def test_v64_migration_exists_and_declares_encrypted_object_metadata() -> None:
    assert MIGRATION.is_file(), f"missing migration: {MIGRATION}"
    sql = " ".join(MIGRATION.read_text(encoding="utf-8").lower().split())
    assert "create schema platform_attachments" in sql
    for table in TABLES:
        assert f"create table platform_attachments.{table}" in sql
    for name in (
        "original_name_ciphertext",
        "original_name_key_version",
        "object_ref_ciphertext",
        "object_ref_key_version",
        "detected_mime",
        "size_bytes",
        "sha256",
        "retained_until",
        "state",
        "state_reason",
    ):
        assert name in sql
    assert "token_ciphertext" not in sql
    assert "token_key_version" not in sql
    assert "token_sha256 bytea" in sql
    assert "is_current" not in sql
    assert "create view platform_attachments.current_artifact_versions" in sql


@pytest.mark.postgres
def test_v64_tables_constraints_foreign_keys_and_indexes(control_database) -> None:
    for environment in control_database["environments"].values():
        with psycopg.connect(environment["admin"]) as connection:
            actual_tables = {
                row[0]
                for row in connection.execute(
                    "select table_name from information_schema.tables "
                    "where table_schema='platform_attachments'"
                )
            }
            assert set(TABLES) <= actual_tables

            attachment_checks = _checks(
                connection, "platform_attachments", "attachments"
            )
            for state in ATTACHMENT_STATES:
                assert f"'{state}'::text" in attachment_checks
            assert "'expired'::text" not in attachment_checks

            binding_checks = _checks(connection, "platform_attachments", "bindings")
            for kind in BINDING_KINDS:
                assert f"'{kind}'::text" in binding_checks

            constraints = {
                (row[0], row[1], row[2])
                for row in connection.execute(
                    "select cls.relname,con.contype,pg_get_constraintdef(con.oid) "
                    "from pg_constraint con join pg_class cls on cls.oid=con.conrelid "
                    "where con.connamespace='platform_attachments'::regnamespace"
                )
            }
            assert any(
                table == "artifact_versions"
                and kind == "u"
                and "UNIQUE (artifact_id, version_no)" in definition
                for table, kind, definition in constraints
            )
            referenced_tables = {
                row[0]
                for row in connection.execute(
                    "select distinct confrelid::regclass::text from pg_constraint "
                    "where connamespace='platform_attachments'::regnamespace "
                    "and contype='f'"
                )
            }
            assert {
                "platform_control.internal_users",
                "platform_control.conversations",
                "platform_control.conversation_messages",
                "platform_control.conversation_turns",
                "platform_control.mission_tasks",
            } <= referenced_tables

            indexes = "\n".join(
                row[0]
                for row in connection.execute(
                    "select indexdef from pg_indexes "
                    "where schemaname='platform_attachments'"
                )
            )
            for fragment in (
                "attachments_owner_created_v64",
                "bindings_conversation_kind_v64",
                "bindings_attachment_kind_v64",
                "artifact_versions_artifact_state_v64",
                "task_grants_token_v64",
                "one_active_task_grant_v64",
                "processing_jobs_claim_v64",
                "erasure_jobs_claim_v64",
                "message_citations_message_ordinal_v64",
            ):
                assert fragment in indexes


@pytest.mark.postgres
def test_v64_retention_versions_grants_feedback_and_read_state(
    control_database,
) -> None:
    for environment in control_database["environments"].values():
        with psycopg.connect(environment["admin"]) as connection:
            for table in (
                "attachments",
                "uploads",
                "artifact_versions",
                "derivatives",
            ):
                columns = _columns(connection, table)
                assert "retained_until" in columns
                assert columns["retained_until"] is not None
                assert "365 days" in columns["retained_until"]

            grant_columns = set(_columns(connection, "task_grants"))
            assert {
                "token_sha256",
                "task_id",
                "attachment_id",
                "agent_id",
                "scope",
                "expires_at",
                "max_reads",
                "read_count",
                "max_bytes",
                "bytes_read",
                "max_files",
                "file_count",
                "max_file_bytes",
                "revoked_at",
            } <= grant_columns
            assert (
                not {
                    "token",
                    "token_ciphertext",
                    "object_ref_ciphertext",
                }
                & grant_columns
            )
            grant_checks = _checks(connection, "platform_attachments", "task_grants")
            assert "octet_length(token_sha256) = 32" in grant_checks
            assert "read_count <= max_reads" in grant_checks
            assert "bytes_read <= max_bytes" in grant_checks

            version_columns = set(_columns(connection, "artifact_versions"))
            assert "is_current" not in version_columns
            view = connection.execute(
                "select definition from pg_views where "
                "schemaname='platform_attachments' "
                "and viewname='current_artifact_versions'"
            ).fetchone()
            assert view is not None
            assert "state = 'ready'::text" in view[0]
            assert "row_number()" in view[0]

            feedback_columns = _columns(
                connection, "conversation_feedback", "platform_control"
            )
            assert feedback_columns["triage_status"] is None
            feedback_checks = _checks(
                connection, "platform_control", "conversation_feedback"
            )
            for reason in (
                "inaccurate",
                "incomplete",
                "unclear",
                "unresolved",
                "file_format",
                "source_timeliness",
                "other",
            ):
                assert f"'{reason}'::text" in feedback_checks
            for status in ("pending_triage", "triaged", "dismissed"):
                assert f"'{status}'::text" in feedback_checks


@pytest.mark.postgres
def test_v64_security_definer_functions_and_roles_are_least_privilege(
    control_database,
) -> None:
    role_functions = {
        "control_app": {
            "finalize_upload_v64",
            "issue_task_grant_v64",
            "revoke_task_grant_v64",
            "upsert_conversation_read_state_v64",
        },
        "brain_worker": {
            "claim_attachment_processing_job_v64",
            "record_attachment_processing_result_v64",
            "record_attachment_derivative_v64",
            "consume_task_grant_v64",
            "consume_output_write_grant_v64",
            "bind_artifact_version_v64",
        },
        "audit_append": {"append_attachment_access_event_v64"},
        "control_maintenance": {
            "claim_attachment_erasure_job_v64",
            "record_attachment_erasure_result_v64",
        },
    }
    all_functions = set().union(*role_functions.values())
    for environment in control_database["environments"].values():
        with psycopg.connect(environment["admin"]) as connection:
            rows = connection.execute(
                "select proc.proname,proc.prosecdef,proc.proconfig,"
                "pg_get_functiondef(proc.oid) from pg_proc proc "
                "where proc.pronamespace='platform_attachments'::regnamespace "
                "and proc.proname=any(%s)",
                (list(all_functions),),
            ).fetchall()
            assert {row[0] for row in rows} == all_functions
            for _, security_definer, config, definition in rows:
                assert security_definer is True
                assert "search_path=pg_catalog, platform_attachments" in config
                assert "current_user" in definition
                assert "session_user" in definition

            for role_fragment, allowed in role_functions.items():
                role = next(
                    value for value in environment["roles"] if role_fragment in value
                )
                grants = {
                    row[0]: row[1]
                    for row in connection.execute(
                        "select proc.proname,has_function_privilege(%s,proc.oid,'execute') "
                        "from pg_proc proc where "
                        "proc.pronamespace='platform_attachments'::regnamespace "
                        "and proc.proname=any(%s)",
                        (role, list(all_functions)),
                    )
                }
                assert {name for name, granted in grants.items() if granted} == allowed

            for role in environment["roles"]:
                if any(fragment in role for fragment in role_functions):
                    continue
                assert connection.execute(
                    "select bool_and(not has_function_privilege(%s,proc.oid,'execute')) "
                    "from pg_proc proc where "
                    "proc.pronamespace='platform_attachments'::regnamespace "
                    "and proc.proname=any(%s)",
                    (role, list(all_functions)),
                ).fetchone() == (True,)

            app_role = next(
                role for role in environment["roles"] if "control_app" in role
            )
            assert connection.execute(
                "select has_column_privilege(%s,"
                "'platform_control.conversation_feedback','triage_status','update')",
                (app_role,),
            ).fetchone() == (True,)

            assert connection.execute(
                "select bool_and(not has_table_privilege(%s,"
                "'platform_attachments.' || table_name,'insert,update,delete')) "
                "from unnest(%s::text[]) table_name",
                (
                    next(
                        role for role in environment["roles"] if "audit_append" in role
                    ),
                    list(TABLES),
                ),
            ).fetchone() == (True,)


@pytest.mark.postgres
def test_v64_processing_pipeline_advances_retries_and_persists_derivative(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        context = _seed_task(admin)
        attachment_id = _insert_attachment(admin, context, state="uploading")
        upload_id = uuid4()
        admin.execute(
            "insert into platform_attachments.uploads "
            "(upload_id,attachment_id,owner_internal_user_id,conversation_id,"
            "object_ref_ciphertext,object_ref_key_version,state) "
            "values (%s,%s,%s,%s,%s,1,'uploading')",
            (
                upload_id,
                attachment_id,
                context["owner_id"],
                context["conversation_id"],
                b"r" * 29,
            ),
        )
        admin.commit()

    with psycopg.connect(environment["urls"]["platform_control_app"]) as app:
        assert app.execute(
            "select platform_attachments.finalize_upload_v64(%s,%s,%s,%s,%s)",
            (
                upload_id,
                context["owner_id"],
                "application/pdf",
                128,
                b"h" * 32,
            ),
        ).fetchone() == (attachment_id,)
        app.commit()

    brain_url = environment["urls"]["platform_brain_worker"]
    with psycopg.connect(brain_url) as brain:
        validate_job_id = brain.execute(
            "select (platform_attachments."
            "claim_attachment_processing_job_v64('worker-1')).processing_job_id"
        ).fetchone()[0]
        assert validate_job_id is not None
        brain.execute(
            "select platform_attachments."
            "record_attachment_processing_result_v64(%s,'scanning',null)",
            (validate_job_id,),
        )
        scan_job_id = brain.execute(
            "select (platform_attachments."
            "claim_attachment_processing_job_v64('worker-1')).processing_job_id"
        ).fetchone()[0]
        assert scan_job_id is not None
        brain.execute(
            "select platform_attachments."
            "record_attachment_processing_result_v64(%s,'ready',null)",
            (scan_job_id,),
        )
        derivative_job_id = brain.execute(
            "select (platform_attachments."
            "claim_attachment_processing_job_v64('worker-1')).processing_job_id"
        ).fetchone()[0]
        assert derivative_job_id is not None
        derivative_id = uuid4()
        assert brain.execute(
            "select platform_attachments.record_attachment_derivative_v64("
            "%s,%s,'preview',%s,1,'application/pdf',64,%s,null)",
            (derivative_job_id, derivative_id, b"d" * 29, b"p" * 32),
        ).fetchone() == (derivative_id,)
        brain.commit()

    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select state from platform_attachments.attachments where attachment_id=%s",
            (attachment_id,),
        ).fetchone() == ("ready",)
        assert admin.execute(
            "select state from platform_attachments.derivatives where derivative_id=%s",
            (derivative_id,),
        ).fetchone() == ("ready",)


@pytest.mark.postgres
def test_v64_processing_retry_is_bounded_without_worker_table_dml(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        context = _seed_task(admin)
        attachment_id = _insert_attachment(admin, context, state="validating")
        admin.execute(
            "insert into platform_attachments.processing_jobs "
            "(processing_job_id,attachment_id,job_kind,max_attempts) "
            "values (%s,%s,'validate',3)",
            (uuid4(), attachment_id),
        )
        admin.commit()

    brain_url = environment["urls"]["platform_brain_worker"]
    for expected_state in ("queued", "queued", "failed"):
        with psycopg.connect(brain_url) as brain:
            job_id = brain.execute(
                "select (platform_attachments."
                "claim_attachment_processing_job_v64('retry-worker'))."
                "processing_job_id"
            ).fetchone()[0]
            brain.execute(
                "select platform_attachments."
                "record_attachment_processing_result_v64(%s,'retry','temporary')",
                (job_id,),
            )
            brain.commit()
        with psycopg.connect(environment["admin"]) as admin:
            assert admin.execute(
                "select state from platform_attachments.processing_jobs "
                "where processing_job_id=%s",
                (job_id,),
            ).fetchone() == (expected_state,)

    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select state from platform_attachments.attachments where attachment_id=%s",
            (attachment_id,),
        ).fetchone() == ("rejected",)
    with (
        psycopg.connect(brain_url) as brain,
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        brain.execute("update platform_attachments.processing_jobs set state='queued'")


@pytest.mark.postgres
def test_v64_output_grant_is_task_scoped_without_a_ready_attachment(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        context = _seed_task(admin)
    token_sha256 = b"w" * 32
    grant_id = uuid4()
    with psycopg.connect(environment["urls"]["platform_control_app"]) as app:
        assert app.execute(
            "select platform_attachments.issue_task_grant_v64("
            "%s,%s,%s,null,%s,'write_output',%s,0,%s,20,%s)",
            (
                grant_id,
                token_sha256,
                context["task_id"],
                context["agent_id"],
                datetime.now(UTC) + timedelta(minutes=15),
                250 * 1024 * 1024,
                50 * 1024 * 1024,
            ),
        ).fetchone() == (grant_id,)
        app.commit()
    with psycopg.connect(environment["urls"]["platform_brain_worker"]) as brain:
        assert brain.execute(
            "select platform_attachments.consume_output_write_grant_v64(%s,%s,%s,%s)",
            (token_sha256, context["task_id"], context["agent_id"], 1024),
        ).fetchone() == (grant_id,)
        brain.commit()
    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select attachment_id,file_count,bytes_read,max_file_bytes "
            "from platform_attachments.task_grants where grant_id=%s",
            (grant_id,),
        ).fetchone() == (None, 1, 1024, 50 * 1024 * 1024)


@pytest.mark.postgres
def test_v64_read_grants_require_active_bound_task_input_and_replace_expired(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        context = _seed_task(admin)
        attachment_id = _insert_attachment(admin, context)

    app_url = environment["urls"]["platform_control_app"]
    grant_args = (
        b"a" * 32,
        context["task_id"],
        attachment_id,
        context["agent_id"],
        "read",
        datetime.now(UTC) + timedelta(minutes=15),
        2,
        1024,
    )
    with (
        psycopg.connect(app_url) as app,
        pytest.raises(psycopg.errors.CheckViolation, match="task_input"),
    ):
        app.execute(
            "select platform_attachments.issue_task_grant_v64("
            "%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (uuid4(), *grant_args),
        )

    with psycopg.connect(environment["admin"]) as admin:
        _insert_task_input_binding(admin, context, attachment_id)

    expired_id = uuid4()
    with psycopg.connect(environment["admin"]) as admin:
        admin.execute(
            "insert into platform_attachments.task_grants "
            "(grant_id,token_sha256,task_id,attachment_id,agent_id,scope,"
            "expires_at,max_reads,max_bytes,max_files,max_file_bytes) "
            "values (%s,%s,%s,%s,%s,'read',%s,1,1024,0,0)",
            (
                expired_id,
                b"e" * 32,
                context["task_id"],
                attachment_id,
                context["agent_id"],
                datetime.now(UTC) - timedelta(minutes=1),
            ),
        )
        admin.commit()
    replacement_id = uuid4()
    with psycopg.connect(app_url) as app:
        assert app.execute(
            "select platform_attachments.issue_task_grant_v64("
            "%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (replacement_id, *grant_args),
        ).fetchone() == (replacement_id,)
        app.commit()
    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select revoked_at is not null from platform_attachments.task_grants "
            "where grant_id=%s",
            (expired_id,),
        ).fetchone() == (True,)
        admin.execute(
            "update platform_control.mission_tasks set status='completed',"
            "terminal_at=now() where task_id=%s",
            (context["task_id"],),
        )
        admin.commit()
    with (
        psycopg.connect(environment["urls"]["platform_brain_worker"]) as brain,
        pytest.raises(psycopg.errors.InsufficientPrivilege, match="terminal"),
    ):
        brain.execute(
            "select platform_attachments.consume_task_grant_v64(%s,%s,%s,%s,'read',1)",
            (
                grant_args[0],
                context["task_id"],
                attachment_id,
                context["agent_id"],
            ),
        )


@pytest.mark.postgres
def test_v64_terminal_task_cannot_receive_input_grant(control_database) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        context = _seed_task(admin, task_status="completed")
        attachment_id = _insert_attachment(admin, context)
        _insert_task_input_binding(admin, context, attachment_id)
    with (
        psycopg.connect(environment["urls"]["platform_control_app"]) as app,
        pytest.raises(psycopg.errors.CheckViolation, match="active task"),
    ):
        app.execute(
            "select platform_attachments.issue_task_grant_v64("
            "%s,%s,%s,%s,%s,'read',%s,1,1024)",
            (
                uuid4(),
                b"t" * 32,
                context["task_id"],
                attachment_id,
                context["agent_id"],
                datetime.now(UTC) + timedelta(minutes=15),
            ),
        )


@pytest.mark.postgres
def test_v64_owner_and_conversation_integrity_rejects_cross_owner_rows(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        first = _seed_task(admin)
        second = _seed_task(admin)
        attachment_id = _insert_attachment(admin, first, state="uploading")
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            admin.execute(
                "insert into platform_attachments.uploads "
                "(upload_id,attachment_id,owner_internal_user_id,conversation_id,"
                "object_ref_ciphertext,object_ref_key_version,state) "
                "values (%s,%s,%s,%s,%s,1,'uploading')",
                (
                    uuid4(),
                    attachment_id,
                    second["owner_id"],
                    second["conversation_id"],
                    b"u" * 29,
                ),
            )
        admin.rollback()
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            admin.execute(
                "insert into platform_attachments.bindings "
                "(binding_id,attachment_id,owner_internal_user_id,kind,conversation_id) "
                "values (%s,%s,%s,'conversation_material',%s)",
                (
                    uuid4(),
                    attachment_id,
                    second["owner_id"],
                    second["conversation_id"],
                ),
            )


@pytest.mark.postgres
def test_v64_artifact_idempotency_and_citation_claim_locations(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        context = _seed_task(admin)
        attachment_id = _insert_attachment(admin, context)
        artifact_id = uuid4()
        admin.execute(
            "insert into platform_attachments.artifacts "
            "(artifact_id,artifact_key,owner_internal_user_id,conversation_id,"
            "task_id,agent_id) values (%s,'candidate-report',%s,%s,%s,%s)",
            (
                artifact_id,
                context["owner_id"],
                context["conversation_id"],
                context["task_id"],
                context["agent_id"],
            ),
        )
        version_values = (
            uuid4(),
            artifact_id,
            attachment_id,
            "producer-version-1",
            b"n" * 29,
            b"r" * 29,
            b"h" * 32,
        )
        admin.execute(
            "insert into platform_attachments.artifact_versions "
            "(artifact_version_id,artifact_id,attachment_id,version_no,"
            "producer_version_id,original_name_ciphertext,original_name_key_version,"
            "object_ref_ciphertext,object_ref_key_version,detected_mime,size_bytes,"
            "sha256,state,result_status) values (%s,%s,%s,1,%s,%s,1,%s,1,"
            "'application/pdf',128,%s,'ready','succeeded')",
            version_values,
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            admin.execute(
                "insert into platform_attachments.artifact_versions "
                "(artifact_version_id,artifact_id,attachment_id,version_no,"
                "producer_version_id,original_name_ciphertext,"
                "original_name_key_version,object_ref_ciphertext,"
                "object_ref_key_version,detected_mime,size_bytes,sha256,state,"
                "result_status) values (%s,%s,%s,2,%s,%s,1,%s,1,"
                "'application/pdf',128,%s,'ready','succeeded')",
                (
                    uuid4(),
                    artifact_id,
                    uuid4(),
                    "producer-version-1",
                    b"n" * 29,
                    b"r" * 29,
                    b"h" * 32,
                ),
            )
        admin.rollback()

        admin.execute(
            "insert into platform_attachments.message_citations "
            "(citation_id,conversation_id,message_id,ordinal,url_ciphertext,"
            "url_key_version,site_ciphertext,site_key_version,"
            "supported_claim_locations,retrieved_at) "
            "values (%s,%s,%s,1,%s,1,%s,1,%s,now())",
            (
                uuid4(),
                context["conversation_id"],
                context["message_id"],
                b"l" * 29,
                b"s" * 29,
                Jsonb([{"start": 0, "end": 12}]),
            ),
        )
        with pytest.raises(psycopg.errors.CheckViolation):
            admin.execute(
                "insert into platform_attachments.message_citations "
                "(citation_id,conversation_id,message_id,ordinal,url_ciphertext,"
                "url_key_version,site_ciphertext,site_key_version,"
                "supported_claim_locations,retrieved_at) "
                "values (%s,%s,%s,2,%s,1,%s,1,'[]'::jsonb,now())",
                (
                    uuid4(),
                    context["conversation_id"],
                    context["message_id"],
                    b"l" * 29,
                    b"s" * 29,
                ),
            )


@pytest.mark.postgres
def test_v64_erasure_removes_deleted_version_from_current_view(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        context = _seed_task(admin)
        attachment_id = _insert_attachment(admin, context)
        artifact_id = uuid4()
        version_id = uuid4()
        admin.execute(
            "insert into platform_attachments.artifacts "
            "(artifact_id,artifact_key,owner_internal_user_id,conversation_id,"
            "task_id,agent_id) values (%s,'erase-report',%s,%s,%s,%s)",
            (
                artifact_id,
                context["owner_id"],
                context["conversation_id"],
                context["task_id"],
                context["agent_id"],
            ),
        )
        admin.execute(
            "insert into platform_attachments.artifact_versions "
            "(artifact_version_id,artifact_id,attachment_id,version_no,"
            "producer_version_id,original_name_ciphertext,original_name_key_version,"
            "object_ref_ciphertext,object_ref_key_version,detected_mime,size_bytes,"
            "sha256,state,result_status) values (%s,%s,%s,1,'erase-version',%s,1,"
            "%s,1,'application/pdf',128,%s,'ready','succeeded')",
            (version_id, artifact_id, attachment_id, b"n" * 29, b"r" * 29, b"h" * 32),
        )
        erasure_job_id = uuid4()
        admin.execute(
            "insert into platform_attachments.erasure_jobs "
            "(erasure_job_id,attachment_id,requested_by_internal_user_id,"
            "reason_ciphertext,reason_key_version,reason_sha256) "
            "values (%s,%s,%s,%s,1,%s)",
            (
                erasure_job_id,
                attachment_id,
                context["owner_id"],
                b"e" * 29,
                b"e" * 32,
            ),
        )
        admin.commit()
        assert admin.execute(
            "select artifact_version_id from "
            "platform_attachments.current_artifact_versions "
            "where artifact_id=%s",
            (artifact_id,),
        ).fetchone() == (version_id,)

    maintenance_url = environment["urls"]["platform_control_maintenance"]
    with psycopg.connect(maintenance_url) as maintenance:
        assert maintenance.execute(
            "select (platform_attachments."
            "claim_attachment_erasure_job_v64('erase-worker')).erasure_job_id"
        ).fetchone() == (erasure_job_id,)
        maintenance.execute(
            "select platform_attachments.record_attachment_erasure_result_v64("
            "%s,'completed',null,'{}'::jsonb)",
            (erasure_job_id,),
        )
        maintenance.commit()
    with psycopg.connect(environment["admin"]) as admin:
        assert (
            admin.execute(
                "select artifact_version_id from "
                "platform_attachments.current_artifact_versions "
                "where artifact_id=%s",
                (artifact_id,),
            ).fetchone()
            is None
        )
        assert admin.execute(
            "select state from platform_attachments.artifact_versions "
            "where artifact_version_id=%s",
            (version_id,),
        ).fetchone() == ("deleted",)


def _seed_feedback_target(connection: psycopg.Connection) -> dict[str, object]:
    context = _seed_task(connection)
    assistant_message_id = uuid4()
    connection.execute(
        "insert into platform_control.conversation_messages "
        "(message_id,conversation_id,seq,role,content_ciphertext,"
        "encryption_key_version,turn_id,mission_id,delivery_status,completed_at) "
        "values (%s,%s,2,'assistant',%s,1,%s,%s,'completed',now())",
        (
            assistant_message_id,
            context["conversation_id"],
            b"a" * 29,
            context["turn_id"],
            context["mission_id"],
        ),
    )
    connection.execute(
        "update platform_control.conversation_turns set assistant_message_id=%s "
        "where turn_id=%s",
        (assistant_message_id, context["turn_id"]),
    )
    connection.commit()
    return {**context, "assistant_message_id": assistant_message_id}


@pytest.mark.postgres
def test_v64_feedback_triage_only_defaults_for_unhelpful(control_database) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        helpful = _seed_feedback_target(admin)
        unhelpful = _seed_feedback_target(admin)
        helpful_id = uuid4()
        unhelpful_id = uuid4()
        admin.execute(
            "insert into platform_control.conversation_feedback "
            "(feedback_id,owner_internal_user_id,conversation_id,message_id,"
            "turn_id,mission_id,rating) values (%s,%s,%s,%s,%s,%s,'helpful')",
            (
                helpful_id,
                helpful["owner_id"],
                helpful["conversation_id"],
                helpful["assistant_message_id"],
                helpful["turn_id"],
                helpful["mission_id"],
            ),
        )
        admin.execute(
            "insert into platform_control.conversation_feedback "
            "(feedback_id,owner_internal_user_id,conversation_id,message_id,"
            "turn_id,mission_id,rating,reason) "
            "values (%s,%s,%s,%s,%s,%s,'unhelpful','file_format')",
            (
                unhelpful_id,
                unhelpful["owner_id"],
                unhelpful["conversation_id"],
                unhelpful["assistant_message_id"],
                unhelpful["turn_id"],
                unhelpful["mission_id"],
            ),
        )
        assert admin.execute(
            "select feedback_id,triage_status from "
            "platform_control.conversation_feedback "
            "where feedback_id=any(%s) order by feedback_id",
            ([helpful_id, unhelpful_id],),
        ).fetchall() == sorted([(helpful_id, None), (unhelpful_id, "pending_triage")])


@pytest.mark.postgres
def test_v64_read_state_returns_persisted_monotonic_max(control_database) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        context = _seed_task(admin)
    with psycopg.connect(environment["urls"]["platform_control_app"]) as app:
        assert app.execute(
            "select platform_attachments.upsert_conversation_read_state_v64(%s,%s,5)",
            (context["owner_id"], context["conversation_id"]),
        ).fetchone() == (5,)
        assert app.execute(
            "select platform_attachments.upsert_conversation_read_state_v64(%s,%s,2)",
            (context["owner_id"], context["conversation_id"]),
        ).fetchone() == (5,)


@pytest.mark.postgres
def test_v64_access_audit_is_append_only_through_audit_role(control_database) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        context = _seed_task(admin)
        attachment_id = _insert_attachment(admin, context)
    event_id = uuid4()
    with psycopg.connect(environment["urls"]["platform_audit_append"]) as audit:
        assert audit.execute(
            "select platform_attachments.append_attachment_access_event_v64("
            "%s,%s,null,%s,%s,%s,'agent_read','allowed',128,%s)",
            (
                event_id,
                attachment_id,
                context["owner_id"],
                context["task_id"],
                context["agent_id"],
                b"a" * 32,
            ),
        ).fetchone() == (event_id,)
        audit.commit()
    with (
        psycopg.connect(environment["urls"]["platform_control_app"]) as app,
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        app.execute(
            "select platform_attachments.append_attachment_access_event_v64("
            "%s,%s,null,%s,%s,%s,'agent_read','allowed',128,%s)",
            (
                uuid4(),
                attachment_id,
                context["owner_id"],
                context["task_id"],
                context["agent_id"],
                b"a" * 32,
            ),
        )
