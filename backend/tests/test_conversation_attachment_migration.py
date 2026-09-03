from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

# Pytest fixtures are imported into this module's namespace for discovery.
# ruff: noqa: F401,F811
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
    "upload_write_attempts",
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
    source_kind: str = "user_input",
) -> object:
    attachment_id = uuid4()
    connection.execute(
        "insert into platform_attachments.attachments "
        "(attachment_id,owner_internal_user_id,conversation_id,source_kind,"
        "original_name_ciphertext,original_name_key_version,"
        "object_ref_ciphertext,object_ref_key_version,detected_mime,size_bytes,"
        "sha256,state,ready_at) values (%s,%s,%s,%s,%s,1,%s,1,"
        "'application/pdf',128,%s,%s,case when %s='ready' then now() end)",
        (
            attachment_id,
            context["owner_id"],
            context["conversation_id"],
            source_kind,
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


def _insert_task_output_binding(
    connection: psycopg.Connection,
    context: dict[str, object],
    attachment_id: object,
) -> None:
    connection.execute(
        "insert into platform_attachments.bindings "
        "(binding_id,attachment_id,owner_internal_user_id,kind,conversation_id,"
        "task_id,agent_id) values (%s,%s,%s,'task_output',%s,%s,%s)",
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


def _insert_artifact(
    connection: psycopg.Connection,
    context: dict[str, object],
    artifact_key: str,
) -> object:
    artifact_id = uuid4()
    connection.execute(
        "insert into platform_attachments.artifacts "
        "(artifact_id,artifact_key,owner_internal_user_id,conversation_id,"
        "task_id,agent_id) values (%s,%s,%s,%s,%s,%s)",
        (
            artifact_id,
            artifact_key,
            context["owner_id"],
            context["conversation_id"],
            context["task_id"],
            context["agent_id"],
        ),
    )
    connection.commit()
    return artifact_id


def _insert_related_task(
    connection: psycopg.Connection,
    context: dict[str, object],
    *,
    agent_id: str | None = None,
) -> object:
    mission_id = uuid4()
    task_id = uuid4()
    connection.execute(
        "insert into platform_control.missions "
        "(mission_id,owner_internal_user_id,client_request_id,mode,status,"
        "conversation_id,turn_id,triggering_message_id) "
        "values (%s,%s,%s,'brain','planning',%s,%s,%s)",
        (
            mission_id,
            context["owner_id"],
            uuid4(),
            context["conversation_id"],
            context["turn_id"],
            context["message_id"],
        ),
    )
    connection.execute(
        "insert into platform_control.mission_tasks "
        "(task_id,mission_id,agent_id,objective_ciphertext,"
        "encryption_key_version,status,started_at) "
        "values (%s,%s,%s,%s,1,'running',now())",
        (task_id, mission_id, agent_id or context["agent_id"], b"o" * 29),
    )
    connection.commit()
    return task_id


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


def _processing_attempt(connection: psycopg.Connection, job_id):
    return connection.execute(
        "select attempt_token from platform_attachments.processing_jobs "
        "where processing_job_id=%s",
        (job_id,),
    ).fetchone()[0]


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
            } <= referenced_tables
            assert "platform_control.mission_tasks" not in referenced_tables
            task_context_triggers = {
                row[0]
                for row in connection.execute(
                    "select trigger_name from information_schema.triggers "
                    "where event_object_schema in ('platform_attachments',"
                    "'platform_control','platform_brain')"
                )
            }
            assert {
                "enforce_binding_task_context_v64",
                "enforce_artifact_task_context_v64",
                "revoke_terminal_mission_task_grants_v64",
                "revoke_terminal_brain_task_grants_v64",
            } <= task_context_triggers

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
            assert feedback_columns["triaged_by_internal_user_id"] is None
            assert feedback_columns["triaged_at"] is None
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
            "create_upload_v64",
            "bind_conversation_turn_v64",
            "request_attachment_erasure_v64",
            "claim_upload_write_v64",
            "abandon_upload_write_v64",
            "finalize_upload_v64",
            "acknowledge_upload_write_cleanup_v64",
            "issue_task_grant_v64",
            "revoke_task_grant_v64",
            "authorize_review_attachment_access_v64",
            "upsert_conversation_read_state_v64",
        },
        "brain_worker": {
            "issue_task_grant_v64",
            "claim_attachment_processing_job_v64",
            "record_attachment_processing_result_v64",
            "record_attachment_derivative_v64",
            "consume_task_grant_v64",
            "consume_output_write_grant_v64",
            "bind_artifact_version_v64",
            "fail_artifact_version_v64",
        },
        "audit_append": {"append_attachment_access_event_v64"},
        "control_maintenance": {
            "claim_attachment_erasure_job_v64",
            "record_attachment_erasure_result_v64",
        },
    }
    owner_only_functions = {"claim_conversation_attachment_v64"}
    all_functions = set().union(*role_functions.values(), owner_only_functions)
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
                "select has_function_privilege(%s,"
                "'platform_control.triage_conversation_feedback_v64(uuid,uuid,text)',"
                "'execute')",
                (app_role,),
            ).fetchone() == (True,)
            assert connection.execute(
                "select has_column_privilege(%s,"
                "'platform_control.conversation_feedback','triage_status','update')",
                (app_role,),
            ).fetchone() == (False,)

            assert connection.execute(
                "select bool_and(not has_table_privilege(%s,"
                "'platform_attachments.' || table_name,'insert,update,delete')) "
                "from unnest(%s::text[]) table_name",
                (
                    app_role,
                    [
                        "attachments",
                        "uploads",
                        "upload_write_attempts",
                        "erasure_jobs",
                    ],
                ),
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

            brain_role = next(
                role for role in environment["roles"] if "brain_worker" in role
            )
            assert connection.execute(
                "select has_table_privilege(%s,"
                "'platform_attachments.uploads','select')",
                (brain_role,),
            ).fetchone() == (True,)
            assert connection.execute(
                "select bool_and(not has_table_privilege(%s,"
                "'platform_attachments.' || table_name,'insert,update,delete')) "
                "from unnest(%s::text[]) table_name",
                (brain_role, list(TABLES)),
            ).fetchone() == (True,)

        with psycopg.connect(
            environment["urls"][app_role]
        ) as app_connection, pytest.raises(psycopg.errors.InsufficientPrivilege):
            app_connection.execute(
                "select platform_attachments.claim_conversation_attachment_v64("
                "%s,%s,%s)",
                (uuid4(), uuid4(), uuid4()),
            )


@pytest.mark.postgres
def test_v64_upload_write_attempt_is_leased_and_finalize_keeps_mime_untrusted(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        context = _seed_task(admin)
        attachment_id = _insert_attachment(admin, context, state="uploading")
        upload_id = uuid4()
        admin.execute(
            "update platform_attachments.attachments set declared_mime=%s,"
            "detected_mime=null,size_bytes=7 where attachment_id=%s",
            ("application/pdf", attachment_id),
        )
        admin.execute(
            "insert into platform_attachments.uploads "
            "(upload_id,attachment_id,owner_internal_user_id,conversation_id,"
            "object_ref_ciphertext,object_ref_key_version,declared_mime,size_bytes) "
            "values (%s,%s,%s,%s,%s,1,%s,7)",
            (
                upload_id,
                attachment_id,
                context["owner_id"],
                context["conversation_id"],
                b"r" * 29,
                "application/pdf",
            ),
        )
        admin.commit()

    attempt_id = uuid4()
    app_url = environment["urls"]["platform_control_app"]
    with psycopg.connect(app_url) as app:
        assert app.execute(
            "select platform_attachments.claim_upload_write_v64("
            "%s,%s,%s,%s,2,now()+interval '5 minutes')",
            (upload_id, context["owner_id"], attempt_id, b"w" * 29),
        ).fetchone() == (attachment_id,)
        app.commit()
    with psycopg.connect(app_url) as app:
        assert app.execute(
            "select platform_attachments.finalize_upload_v64("
            "%s,%s,%s,%s,%s,%s)",
            (
                upload_id,
                context["owner_id"],
                attempt_id,
                "application/pdf",
                7,
                b"h" * 32,
            ),
        ).fetchone() == (attachment_id,)
        app.commit()

    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select declared_mime,detected_mime,state,write_attempt_id "
            "from platform_attachments.uploads where upload_id=%s",
            (upload_id,),
        ).fetchone() == ("application/pdf", None, "validating", attempt_id)
        assert admin.execute(
            "select declared_mime,detected_mime,state "
            "from platform_attachments.attachments where attachment_id=%s",
            (attachment_id,),
        ).fetchone() == ("application/pdf", None, "validating")
        assert admin.execute(
            "select state,size_bytes,sha256 from "
            "platform_attachments.upload_write_attempts where attempt_id=%s",
            (attempt_id,),
        ).fetchone() == ("canonical", 7, b"h" * 32)
        admin.execute(
            "delete from platform_attachments.processing_jobs "
            "where attachment_id=%s",
            (attachment_id,),
        )
        admin.execute(
            "delete from platform_attachments.uploads where upload_id=%s",
            (upload_id,),
        )
        admin.execute(
            "delete from platform_attachments.attachments where attachment_id=%s",
            (attachment_id,),
        )


@pytest.mark.postgres
def test_v64_create_upload_rejects_null_and_malformed_required_limits(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        context = _seed_task(admin)

    statement = (
        "select (platform_attachments.create_upload_v64("
        "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)).upload_id"
    )
    valid = [
        uuid4(),
        uuid4(),
        context["owner_id"],
        context["conversation_id"],
        b"n" * 29,
        1,
        b"o" * 29,
        1,
        "application/pdf",
        7,
        datetime.now(UTC) + timedelta(hours=1),
        50 * 1024 * 1024,
        50,
        500 * 1024 * 1024,
    ]
    cases = [(index, None) for index in (0, 1, 2, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13)]
    cases.extend(
        [
            (4, b"n" * 28),
            (5, 0),
            (6, b"o" * 28),
            (7, 0),
            (8, ""),
            (8, " "),
            (8, "\t"),
            (8, "\n"),
            (8, " text/plain"),
            (8, "text/plain "),
            (8, "not-a-mime"),
            (8, "text/"),
            (8, "/plain"),
            (8, "text/plain; charset=utf-8"),
            (9, 0),
            (11, 0),
            (11, 50 * 1024 * 1024 + 1),
            (12, 0),
            (12, 51),
            (13, 0),
            (13, 500 * 1024 * 1024 + 1),
        ]
    )
    with psycopg.connect(environment["urls"]["platform_control_app"]) as app:
        for index, value in cases:
            arguments = valid.copy()
            arguments[0] = uuid4()
            arguments[1] = uuid4()
            arguments[index] = value
            with pytest.raises(
                psycopg.errors.CheckViolation, match="reservation invalid"
            ), app.transaction():
                app.execute(statement, arguments)
        arguments = valid.copy()
        arguments[0] = uuid4()
        arguments[1] = uuid4()
        arguments[11] = 6
        with pytest.raises(
            psycopg.errors.CheckViolation, match="reservation invalid"
        ), app.transaction():
            app.execute(statement, arguments)


@pytest.mark.postgres
def test_v64_finalize_rejects_invalid_receipts_and_noncurrent_ownership(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        context = _seed_task(admin)
    upload_id = uuid4()
    attachment_id = uuid4()
    attempt_id = uuid4()
    app_url = environment["urls"]["platform_control_app"]
    with psycopg.connect(app_url) as app:
        assert app.execute(
            "select (platform_attachments.create_upload_v64("
            "%s,%s,%s,%s,%s,1,%s,1,%s,7,now()+interval '1 hour',"
            "%s,%s,%s)).upload_id",
            (
                upload_id,
                attachment_id,
                context["owner_id"],
                context["conversation_id"],
                b"n" * 29,
                b"o" * 29,
                "application/pdf",
                50 * 1024 * 1024,
                50,
                500 * 1024 * 1024,
            ),
        ).fetchone() == (upload_id,)
        assert app.execute(
            "select platform_attachments.claim_upload_write_v64("
            "%s,%s,%s,%s,1,now()+interval '5 minutes')",
            (upload_id, context["owner_id"], attempt_id, b"w" * 29),
        ).fetchone() == (attachment_id,)
        app.commit()

        for size, digest in (
            (None, b"h" * 32),
            (-1, b"h" * 32),
            (8, b"h" * 32),
            (50 * 1024 * 1024 + 1, b"h" * 32),
            (7, None),
            (7, b"h" * 31),
        ):
            with pytest.raises(psycopg.Error), app.transaction():
                app.execute(
                    "select platform_attachments.finalize_upload_v64("
                    "%s,%s,%s,%s,%s,%s)",
                    (
                        upload_id,
                        context["owner_id"],
                        attempt_id,
                        "application/pdf",
                        size,
                        digest,
                    ),
                )

        for owner_id, selected_attempt_id in (
            (uuid4(), attempt_id),
            (context["owner_id"], uuid4()),
        ):
            with (
                pytest.raises(psycopg.errors.NoDataFound),
                app.transaction(),
            ):
                app.execute(
                    "select platform_attachments.finalize_upload_v64("
                    "%s,%s,%s,%s,7,%s)",
                    (
                        upload_id,
                        owner_id,
                        selected_attempt_id,
                        "application/pdf",
                        b"h" * 32,
                    ),
                )

    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select upload.state,attempt.state,count(job.processing_job_id) "
            "from platform_attachments.uploads upload "
            "join platform_attachments.upload_write_attempts attempt "
            "on attempt.attempt_id=upload.write_attempt_id "
            "left join platform_attachments.processing_jobs job "
            "on job.attachment_id=upload.attachment_id "
            "where upload.upload_id=%s group by upload.state,attempt.state",
            (upload_id,),
        ).fetchone() == ("uploading", "claimed", 0)
        admin.execute(
            "update platform_attachments.attachments set size_bytes=8 "
            "where attachment_id=%s",
            (attachment_id,),
        )

    with psycopg.connect(app_url) as app, pytest.raises(psycopg.errors.NoDataFound):
        app.execute(
            "select platform_attachments.finalize_upload_v64("
            "%s,%s,%s,%s,7,%s)",
            (
                upload_id,
                context["owner_id"],
                attempt_id,
                "application/pdf",
                b"h" * 32,
            ),
        )

    with psycopg.connect(environment["admin"]) as admin:
        admin.execute(
            "update platform_attachments.attachments set size_bytes=7 "
            "where attachment_id=%s",
            (attachment_id,),
        )

    with psycopg.connect(app_url) as app:
        assert app.execute(
            "select platform_attachments.finalize_upload_v64("
            "%s,%s,%s,%s,7,%s)",
            (
                upload_id,
                context["owner_id"],
                attempt_id,
                "application/pdf",
                b"h" * 32,
            ),
        ).fetchone() == (attachment_id,)
        app.commit()
        with pytest.raises(psycopg.errors.NoDataFound):
            app.execute(
                "select platform_attachments.abandon_upload_write_v64(%s,%s,%s)",
                (upload_id, context["owner_id"], attempt_id),
            )

    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select upload.state,attempt.state "
            "from platform_attachments.uploads upload "
            "join platform_attachments.upload_write_attempts attempt "
            "on attempt.attempt_id=upload.write_attempt_id "
            "where upload.upload_id=%s",
            (upload_id,),
        ).fetchone() == ("validating", "canonical")
        admin.execute(
            "delete from platform_attachments.processing_jobs where attachment_id=%s",
            (attachment_id,),
        )
        admin.execute(
            "delete from platform_attachments.uploads where upload_id=%s",
            (upload_id,),
        )
        admin.execute(
            "delete from platform_attachments.attachments where attachment_id=%s",
            (attachment_id,),
        )


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("max_bytes", "max_files", "max_file_bytes"),
    [
        (250 * 1024 * 1024 + 1, 20, 50 * 1024 * 1024),
        (250 * 1024 * 1024, 21, 50 * 1024 * 1024),
        (250 * 1024 * 1024, 20, 50 * 1024 * 1024 + 1),
    ],
)
def test_v64_output_grant_rejects_limits_above_product_hard_caps(
    control_database, max_bytes, max_files, max_file_bytes
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        context = _seed_task(admin)
    with (
        psycopg.connect(environment["urls"]["platform_control_app"]) as app,
        pytest.raises(psycopg.errors.CheckViolation, match="grant invalid"),
    ):
        app.execute(
            "select platform_attachments.issue_task_grant_v64("
            "%s,%s,%s,null,%s,'write_output',%s,0,%s,%s,%s)",
            (
                uuid4(),
                b"q" * 32,
                context["task_id"],
                context["agent_id"],
                datetime.now(UTC) + timedelta(minutes=15),
                max_bytes,
                max_files,
                max_file_bytes,
            ),
        )


@pytest.mark.postgres
def test_v64_output_consumer_rejects_oversized_tampered_grant(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        context = _seed_task(admin)
        token_sha256 = b"z" * 32
        admin.execute(
            "insert into platform_attachments.task_grants "
            "(grant_id,token_sha256,task_id,agent_id,scope,expires_at,"
            "max_reads,max_bytes,max_files,max_file_bytes) "
            "values (%s,%s,%s,%s,'write_output',now()+interval '5 minutes',"
            "0,%s,21,%s)",
            (
                uuid4(),
                token_sha256,
                context["task_id"],
                context["agent_id"],
                251 * 1024 * 1024,
                51 * 1024 * 1024,
            ),
        )
        admin.commit()
    with (
        psycopg.connect(environment["urls"]["platform_brain_worker"]) as brain,
        pytest.raises(psycopg.errors.InsufficientPrivilege, match="unavailable"),
    ):
        brain.execute(
            "select platform_attachments.consume_output_write_grant_v64("
            "%s,%s,%s,1)",
            (token_sha256, context["task_id"], context["agent_id"]),
        )

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
            "update platform_attachments.attachments set declared_mime=%s "
            "where attachment_id=%s",
            ("application/pdf", attachment_id),
        )
        admin.execute(
            "insert into platform_attachments.uploads "
            "(upload_id,attachment_id,owner_internal_user_id,conversation_id,"
            "object_ref_ciphertext,object_ref_key_version,declared_mime,size_bytes,"
            "state) values (%s,%s,%s,%s,%s,1,%s,128,'uploading')",
            (
                upload_id,
                attachment_id,
                context["owner_id"],
                context["conversation_id"],
                b"r" * 29,
                "application/pdf",
            ),
        )
        admin.commit()

    with psycopg.connect(environment["urls"]["platform_control_app"]) as app:
        attempt_id = uuid4()
        assert app.execute(
            "select platform_attachments.claim_upload_write_v64("
            "%s,%s,%s,%s,1,now()+interval '5 minutes')",
            (upload_id, context["owner_id"], attempt_id, b"w" * 29),
        ).fetchone() == (attachment_id,)
        assert app.execute(
            "select platform_attachments.finalize_upload_v64("
            "%s,%s,%s,%s,%s,%s)",
            (
                upload_id,
                context["owner_id"],
                attempt_id,
                "application/pdf",
                128,
                b"h" * 32,
            ),
        ).fetchone() == (attachment_id,)
        app.commit()

    brain_url = environment["urls"]["platform_brain_worker"]
    with psycopg.connect(brain_url) as brain:
        validate_job_id, validate_attempt = brain.execute(
            "select processing_job_id,attempt_token from platform_attachments."
            "claim_attachment_processing_job_v64('worker-1')"
        ).fetchone()
        assert validate_job_id is not None
        with (
            pytest.raises(
                psycopg.errors.CheckViolation, match="validation result invalid"
            ),
            brain.transaction(),
        ):
            brain.execute(
                "select platform_attachments."
                "record_attachment_processing_result_v64("
                "%s,%s,'scanning',null,%s,%s::jsonb,'version:v1')",
                (
                    validate_job_id,
                    validate_attempt,
                    "application/pdf",
                    (
                        '{"coverage":"metadata_only","download":true,'
                        '"inline_preview":false}'
                    ),
                ),
            )
        brain.execute(
            "select platform_attachments."
            "record_attachment_processing_result_v64("
            "%s,%s,'scanning',null,%s,%s::jsonb,'version:v1')",
            (
                validate_job_id,
                validate_attempt,
                "application/pdf",
                ('{"coverage":"first_page","download":true,"inline_preview":true}'),
            ),
        )
        scan_job_id, scan_attempt = brain.execute(
            "select processing_job_id,attempt_token from platform_attachments."
            "claim_attachment_processing_job_v64('worker-1')"
        ).fetchone()
        assert scan_job_id is not None
        brain.execute(
            "select platform_attachments."
            "record_attachment_processing_result_v64(%s,%s,'ready',null)",
            (scan_job_id, scan_attempt),
        )
        derivative_job_id, derivative_attempt = brain.execute(
            "select processing_job_id,attempt_token from platform_attachments."
            "claim_attachment_processing_job_v64('worker-1')"
        ).fetchone()
        assert derivative_job_id is not None
        derivative_id = uuid4()
        assert brain.execute(
            "select platform_attachments.record_attachment_derivative_v64("
            "%s,%s,%s,'preview',%s,1,'image/png',64,%s,null)",
            (derivative_job_id, derivative_attempt, derivative_id, b"d" * 29, b"p" * 32),
        ).fetchone() == (derivative_id,)
        brain.commit()

    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select state,detected_mime,coverage_metadata,immutable_locator "
            "from platform_attachments.attachments where attachment_id=%s",
            (attachment_id,),
        ).fetchone() == (
            "ready",
            "application/pdf",
            {"coverage": "first_page", "download": True, "inline_preview": True},
            "version:v1",
        )
        assert admin.execute(
            "select state from platform_attachments.derivatives where derivative_id=%s",
            (derivative_id,),
        ).fetchone() == ("ready",)


@pytest.mark.postgres
def test_v64_processing_requires_exact_predecessor_and_safe_derivative_shape(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        context = _seed_task(admin)
        wrong_validate_attachment = _insert_attachment(admin, context, state="scanning")
        wrong_scan_attachment = _insert_attachment(admin, context, state="validating")
        ready_attachment = _insert_attachment(admin, context, state="ready")
        validate_job, scan_job, derivative_job, metadata_job = (
            uuid4(), uuid4(), uuid4(), uuid4()
        )
        admin.execute(
            "insert into platform_attachments.processing_jobs "
            "(processing_job_id,attachment_id,job_kind,state,attempt_count) values "
            "(%s,%s,'validate','running',1),(%s,%s,'scan','running',1)",
            (
                validate_job,
                wrong_validate_attachment,
                scan_job,
                wrong_scan_attachment,
            ),
        )
        admin.execute(
            "insert into platform_attachments.processing_jobs "
            "(processing_job_id,attachment_id,job_kind,derivative_kind,state,attempt_count) "
            "values (%s,%s,'derive','preview','running',1)",
            (derivative_job, ready_attachment),
        )
        admin.execute(
            "insert into platform_attachments.processing_jobs "
            "(processing_job_id,attachment_id,job_kind,derivative_kind,state,attempt_count) "
            "values (%s,%s,'derive','metadata','running',1)",
            (metadata_job, ready_attachment),
        )
        admin.commit()

    with psycopg.connect(environment["urls"]["platform_brain_worker"]) as brain:
        for job_id in (validate_job, scan_job):
            attempt = _processing_attempt(brain, job_id)
            with (
                pytest.raises(psycopg.errors.CheckViolation, match="predecessor"),
                brain.transaction(),
            ):
                brain.execute(
                    "select platform_attachments.record_attachment_processing_result_v64("
                    "%s,%s,'retry','temporary')",
                    (job_id, attempt),
                )
        for mime, size in (
            ("application/pdf", 64),
            ("image/png", 0),
            ("image/png", 10 * 1024 * 1024 + 1),
        ):
            derivative_attempt = _processing_attempt(brain, derivative_job)
            with (
                pytest.raises(psycopg.errors.CheckViolation, match="derivative invalid"),
                brain.transaction(),
            ):
                brain.execute(
                    "select platform_attachments.record_attachment_derivative_v64("
                    "%s,%s,%s,'preview',%s,1,%s,%s,%s,null)",
                    (derivative_job, derivative_attempt, uuid4(), b"d" * 29, mime, size, b"p" * 32),
                )
        metadata_id = uuid4()
        metadata_attempt = _processing_attempt(brain, metadata_job)
        assert brain.execute(
            "select platform_attachments.record_attachment_derivative_v64("
            "%s,%s,%s,'metadata',%s,1,'application/json',128,%s,null)",
            (metadata_job, metadata_attempt, metadata_id, b"d" * 29, b"p" * 32),
        ).fetchone() == (metadata_id,)


@pytest.mark.postgres
def test_v64_processing_retry_is_bounded_without_worker_table_dml(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        context = _seed_task(admin)
        attachment_id = _insert_attachment(admin, context, state="validating")
        upload_id = uuid4()
        admin.execute(
            "insert into platform_attachments.uploads "
            "(upload_id,attachment_id,owner_internal_user_id,conversation_id,"
            "object_ref_ciphertext,object_ref_key_version,state) "
            "values (%s,%s,%s,%s,%s,1,'validating')",
            (
                upload_id,
                attachment_id,
                context["owner_id"],
                context["conversation_id"],
                b"r" * 29,
            ),
        )
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
            job_id, attempt = brain.execute(
                "select processing_job_id,attempt_token from platform_attachments."
                "claim_attachment_processing_job_v64('retry-worker')"
            ).fetchone()
            brain.execute(
                "select platform_attachments."
                "record_attachment_processing_result_v64(%s,%s,'retry','temporary')",
                (job_id, attempt),
            )
            brain.commit()
        with psycopg.connect(environment["admin"]) as admin:
            state, available_in_future = admin.execute(
                "select state,available_at > now() "
                "from platform_attachments.processing_jobs "
                "where processing_job_id=%s",
                (job_id,),
            ).fetchone()
            assert state == expected_state
            assert available_in_future is (expected_state == "queued")
            if expected_state == "queued":
                admin.execute(
                    "update platform_attachments.processing_jobs "
                    "set available_at=now() where processing_job_id=%s",
                    (job_id,),
                )
                admin.commit()

    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select state from platform_attachments.attachments where attachment_id=%s",
            (attachment_id,),
        ).fetchone() == ("rejected",)
        assert admin.execute(
            "select state,state_reason from platform_attachments.uploads "
            "where upload_id=%s",
            (upload_id,),
        ).fetchone() == ("rejected", "processing_retries_exhausted")
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
def test_v64_citation_requires_site_and_supported_claim_locations(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        context = _seed_task(admin)
        admin.execute(
            "insert into platform_attachments.message_citations "
            "(citation_id,conversation_id,message_id,ordinal,citation_key,url_ciphertext,"
            "url_key_version,site_ciphertext,site_key_version,"
            "supported_claim_locations,retrieved_at) "
            "values (%s,%s,%s,1,'source-1',%s,1,%s,1,%s,now())",
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
                "(citation_id,conversation_id,message_id,ordinal,citation_key,url_ciphertext,"
                "url_key_version,site_ciphertext,site_key_version,"
                "supported_claim_locations,retrieved_at) "
                "values (%s,%s,%s,2,'source-2',%s,1,%s,1,'[]'::jsonb,now())",
                (
                    uuid4(),
                    context["conversation_id"],
                    context["message_id"],
                    b"l" * 29,
                    b"s" * 29,
                ),
            )


@pytest.mark.postgres
def test_v64_artifact_binding_is_idempotent_and_provenance_checked(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        context = _seed_task(admin)
        artifact_id = _insert_artifact(admin, context, "candidate-report")
        ready_output_id = _insert_attachment(
            admin, context, source_kind="agent_output"
        )
        _insert_task_output_binding(admin, context, ready_output_id)
        other_output_id = _insert_attachment(
            admin, context, source_kind="agent_output"
        )
        _insert_task_output_binding(admin, context, other_output_id)
        user_input_id = _insert_attachment(admin, context)
        _insert_task_output_binding(admin, context, user_input_id)
        related_task_id = _insert_related_task(admin, context)
        related_context = {**context, "task_id": related_task_id}
        unrelated_output_id = _insert_attachment(
            admin, related_context, source_kind="agent_output"
        )
        _insert_task_output_binding(admin, related_context, unrelated_output_id)
        other_agent_task_id = _insert_related_task(
            admin, context, agent_id="other-agent"
        )
        other_agent_context = {
            **context,
            "task_id": other_agent_task_id,
            "agent_id": "other-agent",
        }
        other_agent_output_id = _insert_attachment(
            admin, other_agent_context, source_kind="agent_output"
        )
        _insert_task_output_binding(admin, other_agent_context, other_agent_output_id)
        cross_owner = _seed_task(admin)
        cross_owner_output_id = _insert_attachment(
            admin, cross_owner, source_kind="agent_output"
        )
        _insert_task_output_binding(admin, cross_owner, cross_owner_output_id)

    brain_url = environment["urls"]["platform_brain_worker"]
    version_id = uuid4()
    with psycopg.connect(brain_url) as brain:
        bind_sql = (
            "select platform_attachments.bind_artifact_version_v64("
            "%s,%s,%s,%s,%s)"
        )
        args = (version_id, artifact_id, ready_output_id, 1, "producer-version-1")
        assert brain.execute(bind_sql, args).fetchone() == (version_id,)
        assert brain.execute(bind_sql, args).fetchone() == (version_id,)
        brain.commit()

    with (
        psycopg.connect(brain_url) as brain,
        pytest.raises(psycopg.errors.CheckViolation, match="replay conflict"),
    ):
        brain.execute(
            bind_sql,
            (uuid4(), artifact_id, other_output_id, 2, "producer-version-1"),
        )

    with (
        psycopg.connect(brain_url) as brain,
        pytest.raises(psycopg.errors.CheckViolation, match="agent_output"),
    ):
        brain.execute(
            bind_sql,
            (uuid4(), artifact_id, user_input_id, 2, "user-input-version"),
        )

    with (
        psycopg.connect(brain_url) as brain,
        pytest.raises(psycopg.errors.CheckViolation, match="task_output"),
    ):
        brain.execute(
            bind_sql,
            (uuid4(), artifact_id, unrelated_output_id, 2, "unrelated-version"),
        )

    with (
        psycopg.connect(brain_url) as brain,
        pytest.raises(psycopg.errors.CheckViolation, match="task_output"),
    ):
        brain.execute(
            bind_sql,
            (uuid4(), artifact_id, other_agent_output_id, 2, "other-agent-version"),
        )

    with (
        psycopg.connect(brain_url) as brain,
        pytest.raises(psycopg.errors.CheckViolation, match="attachment invalid"),
    ):
        brain.execute(
            bind_sql,
            (uuid4(), artifact_id, cross_owner_output_id, 2, "cross-owner-version"),
        )

    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select count(*) from platform_attachments.artifact_versions "
            "where artifact_id=%s",
            (artifact_id,),
        ).fetchone() == (1,)


@pytest.mark.postgres
def test_v64_artifact_versions_have_protected_success_and_failure_paths(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        context = _seed_task(admin)
        artifact_id = _insert_artifact(admin, context, "determined-report")
        ready_output_id = _insert_attachment(
            admin, context, source_kind="agent_output"
        )
        _insert_task_output_binding(admin, context, ready_output_id)
        pending_output_id = _insert_attachment(
            admin, context, state="scanning", source_kind="agent_output"
        )
        _insert_task_output_binding(admin, context, pending_output_id)
        pending_job_id = uuid4()
        admin.execute(
            "insert into platform_attachments.processing_jobs "
            "(processing_job_id,attachment_id,job_kind,state,attempt_count) "
            "values (%s,%s,'scan','running',1)",
            (pending_job_id, pending_output_id),
        )
        admin.commit()

    brain_url = environment["urls"]["platform_brain_worker"]
    ready_version_id = uuid4()
    failed_version_id = uuid4()
    with psycopg.connect(brain_url) as brain:
        assert brain.execute(
            "select platform_attachments.bind_artifact_version_v64("
            "%s,%s,%s,1,'ready-producer')",
            (ready_version_id, artifact_id, ready_output_id),
        ).fetchone() == (ready_version_id,)
        assert brain.execute(
            "select platform_attachments.bind_artifact_version_v64("
            "%s,%s,%s,2,'failed-producer')",
            (failed_version_id, artifact_id, pending_output_id),
        ).fetchone() == (failed_version_id,)
        assert brain.execute(
            "select platform_attachments.fail_artifact_version_v64("
            "%s,'rejected','output_processing_failed')",
            (failed_version_id,),
        ).fetchone() == (failed_version_id,)
        brain.commit()

    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select state,result_status from "
            "platform_attachments.artifact_versions where artifact_version_id=%s",
            (ready_version_id,),
        ).fetchone() == ("ready", "succeeded")
        assert admin.execute(
            "select state,result_status,state_reason from "
            "platform_attachments.artifact_versions where artifact_version_id=%s",
            (failed_version_id,),
        ).fetchone() == ("rejected", "failed", "output_processing_failed")
        assert admin.execute(
            "select count(*) from platform_attachments.artifact_versions "
            "where artifact_id=%s and result_status='pending'",
            (artifact_id,),
        ).fetchone() == (0,)
        assert admin.execute(
            "select state,state_reason from platform_attachments.processing_jobs "
            "where processing_job_id=%s",
            (pending_job_id,),
        ).fetchone() == ("failed", "output_processing_failed")


@pytest.mark.postgres
def test_v64_processing_rejection_determines_bound_artifact_version(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        context = _seed_task(admin)
        artifact_id = _insert_artifact(admin, context, "rejected-report")
        attachment_id = _insert_attachment(
            admin, context, state="scanning", source_kind="agent_output"
        )
        _insert_task_output_binding(admin, context, attachment_id)
        job_id = uuid4()
        admin.execute(
            "insert into platform_attachments.processing_jobs "
            "(processing_job_id,attachment_id,job_kind,state,attempt_count) "
            "values (%s,%s,'scan','running',1)",
            (job_id, attachment_id),
        )
        admin.commit()

    version_id = uuid4()
    with psycopg.connect(
        environment["urls"]["platform_brain_worker"]
    ) as brain:
        attempt = _processing_attempt(brain, job_id)
        brain.execute(
            "select platform_attachments.bind_artifact_version_v64("
            "%s,%s,%s,1,'rejected-producer')",
            (version_id, artifact_id, attachment_id),
        )
        brain.execute(
            "select platform_attachments.record_attachment_processing_result_v64("
            "%s,%s,'rejected','malware_detected')",
            (job_id, attempt),
        )
        brain.commit()

    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select state,result_status,state_reason from "
            "platform_attachments.artifact_versions where artifact_version_id=%s",
            (version_id,),
        ).fetchone() == ("rejected", "failed", "malware_detected")


@pytest.mark.postgres
def test_v64_deleted_attachment_is_terminal_for_stale_processing_results(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        context = _seed_task(admin)
        attachment_id = _insert_attachment(admin, context, state="scanning")
        processing_job_id = uuid4()
        processing_attempt = uuid4()
        erasure_job_id = uuid4()
        admin.execute(
            "insert into platform_attachments.processing_jobs "
            "(processing_job_id,attachment_id,job_kind,state,attempt_count,attempt_token) "
            "values (%s,%s,'scan','running',1,%s)",
            (processing_job_id, attachment_id, processing_attempt),
        )
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

    with psycopg.connect(
        environment["urls"]["platform_control_maintenance"]
    ) as maintenance:
        claimed_id = maintenance.execute(
            "select (platform_attachments."
            "claim_attachment_erasure_job_v64('terminal-worker')).erasure_job_id"
        ).fetchone()[0]
        assert claimed_id == erasure_job_id
        maintenance.execute(
            "select platform_attachments.record_attachment_erasure_result_v64("
            "%s,'completed','owner_erased','{}'::jsonb)",
            (erasure_job_id,),
        )
        maintenance.commit()

    with (
        psycopg.connect(environment["urls"]["platform_brain_worker"]) as brain,
        pytest.raises(
            (psycopg.errors.CheckViolation, psycopg.errors.NoDataFound),
            match="deleted|unavailable",
        ),
    ):
        brain.execute(
            "select platform_attachments.record_attachment_processing_result_v64("
            "%s,%s,'ready',null)",
            (processing_job_id, processing_attempt),
        )

    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select state,deleted_at is not null from "
            "platform_attachments.attachments where attachment_id=%s",
            (attachment_id,),
        ).fetchone() == ("deleted", True)
        assert admin.execute(
            "select count(*) from platform_attachments.processing_jobs "
            "where attachment_id=%s and job_kind='derive'",
            (attachment_id,),
        ).fetchone() == (0,)


@pytest.mark.postgres
def test_v64_erasure_serializes_against_inflight_derivative_persistence(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        context = _seed_task(admin)
        attachment_id = _insert_attachment(admin, context)
        processing_job_id = uuid4()
        processing_attempt = uuid4()
        erasure_job_id = uuid4()
        admin.execute(
            "insert into platform_attachments.processing_jobs "
            "(processing_job_id,attachment_id,job_kind,derivative_kind,state,"
            "attempt_count,attempt_token) values (%s,%s,'derive','preview','running',1,%s)",
            (processing_job_id, attachment_id, processing_attempt),
        )
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

    brain_url = environment["urls"]["platform_brain_worker"]
    with psycopg.connect(brain_url) as brain:
        brain.execute("set transaction isolation level repeatable read")
        assert brain.execute(
            "select state from platform_attachments.attachments "
            "where attachment_id=%s",
            (attachment_id,),
        ).fetchone() == ("ready",)

        with psycopg.connect(
            environment["urls"]["platform_control_maintenance"]
        ) as maintenance:
            assert maintenance.execute(
                "select (platform_attachments."
                "claim_attachment_erasure_job_v64('derivative-eraser'))."
                "erasure_job_id"
            ).fetchone() == (erasure_job_id,)
            maintenance.execute(
                "select platform_attachments.record_attachment_erasure_result_v64("
                "%s,'completed','owner_erased','{}'::jsonb)",
                (erasure_job_id,),
            )
            maintenance.commit()

        with pytest.raises(
            (
                psycopg.errors.SerializationFailure,
                psycopg.errors.CheckViolation,
                psycopg.errors.NoDataFound,
            )
        ):
            brain.execute(
                "select platform_attachments.record_attachment_derivative_v64("
                    "%s,%s,%s,'preview',%s,1,'application/pdf',64,%s,null)",
                    (processing_job_id, processing_attempt, uuid4(), b"d" * 29, b"p" * 32),
            )
        brain.rollback()

    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select count(*) from platform_attachments.derivatives "
            "where attachment_id=%s and state='ready'",
            (attachment_id,),
        ).fetchone() == (0,)


@pytest.mark.postgres
def test_v64_claim_result_and_erasure_use_deadlock_free_lock_order(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        context = _seed_task(admin)
        attachment_id = _insert_attachment(admin, context, state="scanning")
        processing_job_id = uuid4()
        erasure_job_id = uuid4()
        admin.execute(
            "insert into platform_attachments.processing_jobs "
            "(processing_job_id,attachment_id,job_kind) values (%s,%s,'scan')",
            (processing_job_id, attachment_id),
        )
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

    maintenance_url = environment["urls"]["platform_control_maintenance"]
    with psycopg.connect(maintenance_url) as maintenance:
        assert maintenance.execute(
            "select (platform_attachments."
            "claim_attachment_erasure_job_v64('lock-order-eraser')).erasure_job_id"
        ).fetchone() == (erasure_job_id,)
        maintenance.commit()

    marker = f"attachment-erasure-{uuid4()}"

    def erase_attachment() -> None:
        with psycopg.connect(maintenance_url, application_name=marker) as maintenance:
            maintenance.execute(
                "select platform_attachments.record_attachment_erasure_result_v64("
                "%s,'completed','owner_erased','{}'::jsonb)",
                (erasure_job_id,),
            )
            maintenance.commit()

    brain_url = environment["urls"]["platform_brain_worker"]
    with psycopg.connect(brain_url) as brain:
        claimed_job_id, processing_attempt = brain.execute(
            "select processing_job_id,attempt_token from platform_attachments."
            "claim_attachment_processing_job_v64('lock-order-worker')"
        ).fetchone()
        assert claimed_job_id == processing_job_id

        with ThreadPoolExecutor(max_workers=1) as executor:
            erasure_future = executor.submit(erase_attachment)
            deadline = time.monotonic() + 3
            with psycopg.connect(environment["admin"]) as admin:
                while time.monotonic() < deadline:
                    wait_event_type = admin.execute(
                        "select wait_event_type from pg_stat_activity "
                        "where application_name=%s",
                        (marker,),
                    ).fetchone()
                    if wait_event_type == ("Lock",):
                        break
                    time.sleep(0.01)
                else:
                    pytest.fail("erasure did not block on the claimed processing job")

            brain.execute(
                "select platform_attachments.record_attachment_processing_result_v64("
                "%s,%s,'ready',null)",
                (processing_job_id, processing_attempt),
            )
            brain.commit()
            erasure_future.result(timeout=3)

    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select state,deleted_at is not null from "
            "platform_attachments.attachments where attachment_id=%s",
            (attachment_id,),
        ).fetchone() == ("deleted", True)
        assert admin.execute(
            "select count(*) from platform_attachments.derivatives "
            "where attachment_id=%s and state='ready'",
            (attachment_id,),
        ).fetchone() == (0,)


@pytest.mark.postgres
def test_v64_processing_attempt_token_prevents_aba_stale_result(control_database) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        context = _seed_task(admin)
        attachment_id = _insert_attachment(admin, context, state="validating")
        processing_job_id = uuid4()
        admin.execute(
            "insert into platform_attachments.processing_jobs "
            "(processing_job_id,attachment_id,job_kind) values (%s,%s,'validate')",
            (processing_job_id, attachment_id),
        )
        admin.commit()

    brain_url = environment["urls"]["platform_brain_worker"]
    with psycopg.connect(brain_url) as brain:
        first = brain.execute(
            "select processing_job_id,attempt_token from platform_attachments."
            "claim_attachment_processing_job_v64('aba-worker-1')"
        ).fetchone()
        assert first[0] == processing_job_id
        assert first[1] is not None
        brain.execute(
            "select platform_attachments.record_attachment_processing_result_v64("
            "%s,%s,'retry','temporary')",
            first,
        )
        brain.commit()

    with psycopg.connect(environment["admin"]) as admin:
        admin.execute(
            "update platform_attachments.processing_jobs set available_at=now() "
            "where processing_job_id=%s",
            (processing_job_id,),
        )
        admin.commit()

    with psycopg.connect(brain_url) as brain:
        second = brain.execute(
            "select processing_job_id,attempt_token from platform_attachments."
            "claim_attachment_processing_job_v64('aba-worker-2')"
        ).fetchone()
        assert second[0] == processing_job_id
        assert second[1] != first[1]
        with pytest.raises(psycopg.errors.NoDataFound), brain.transaction():
            brain.execute(
                "select platform_attachments.record_attachment_processing_result_v64("
                "%s,%s,'rejected','stale')",
                first,
            )

    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select state,attempt_token,claimed_by from "
            "platform_attachments.processing_jobs where processing_job_id=%s",
            (processing_job_id,),
        ).fetchone() == ("running", second[1], "aba-worker-2")
        assert admin.execute(
            "select state from platform_attachments.attachments where attachment_id=%s",
            (attachment_id,),
        ).fetchone() == ("validating",)


@pytest.mark.postgres
def test_v64_persists_immutable_locator_on_attachment_upload_and_artifact(control_database) -> None:
    for environment in control_database["environments"].values():
        with psycopg.connect(environment["admin"]) as connection:
            for table in ("attachments", "uploads", "artifact_versions"):
                assert "immutable_locator" in _columns(connection, table)
                checks = _checks(connection, "platform_attachments", table)
                assert "immutable_locator" in checks
                assert "version|etag" in checks


@pytest.mark.postgres
def test_v64_artifact_version_copies_downloadable_immutable_locator(control_database) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        context = _seed_task(admin)
        artifact_id = _insert_artifact(admin, context, "immutable-output")
        attachment_id = _insert_attachment(admin, context, source_kind="agent_output")
        _insert_task_output_binding(admin, context, attachment_id)
        admin.execute(
            "update platform_attachments.attachments set immutable_locator='etag:\"v1\"' "
            "where attachment_id=%s",
            (attachment_id,),
        )
        admin.commit()

    version_id = uuid4()
    with psycopg.connect(environment["urls"]["platform_brain_worker"]) as brain:
        assert brain.execute(
            "select platform_attachments.bind_artifact_version_v64("
            "%s,%s,%s,1,'immutable-producer')",
            (version_id, artifact_id, attachment_id),
        ).fetchone() == (version_id,)
        brain.commit()

    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select immutable_locator from platform_attachments.artifact_versions "
            "where artifact_version_id=%s",
            (version_id,),
        ).fetchone() == ('etag:"v1"',)


@pytest.mark.postgres
def test_v64_erasure_removes_deleted_version_from_current_view(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        context = _seed_task(admin)
        attachment_id = _insert_attachment(admin, context)
        upload_id = uuid4()
        admin.execute(
            "insert into platform_attachments.uploads "
            "(upload_id,attachment_id,owner_internal_user_id,conversation_id,"
            "object_ref_ciphertext,object_ref_key_version,state) "
            "values (%s,%s,%s,%s,%s,1,'ready')",
            (
                upload_id,
                attachment_id,
                context["owner_id"],
                context["conversation_id"],
                b"r" * 29,
            ),
        )
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
            "select state,result_status from platform_attachments.artifact_versions "
            "where artifact_version_id=%s",
            (version_id,),
        ).fetchone() == ("deleted", "succeeded")
        assert admin.execute(
            "select state,state_reason from platform_attachments.uploads "
            "where upload_id=%s",
            (upload_id,),
        ).fetchone() == ("deleted", None)


@pytest.mark.postgres
def test_v64_erasure_determines_pending_artifact_version_as_failed(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        context = _seed_task(admin)
        artifact_id = _insert_artifact(admin, context, "pending-erasure-report")
        attachment_id = _insert_attachment(
            admin, context, state="scanning", source_kind="agent_output"
        )
        _insert_task_output_binding(admin, context, attachment_id)
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

    version_id = uuid4()
    with psycopg.connect(
        environment["urls"]["platform_brain_worker"]
    ) as brain:
        brain.execute(
            "select platform_attachments.bind_artifact_version_v64("
            "%s,%s,%s,1,'pending-erasure-producer')",
            (version_id, artifact_id, attachment_id),
        )
        brain.commit()

    with psycopg.connect(
        environment["urls"]["platform_control_maintenance"]
    ) as maintenance:
        assert maintenance.execute(
            "select (platform_attachments."
            "claim_attachment_erasure_job_v64('pending-version-eraser'))."
            "erasure_job_id"
        ).fetchone() == (erasure_job_id,)
        maintenance.execute(
            "select platform_attachments.record_attachment_erasure_result_v64("
            "%s,'completed','owner_erased','{}'::jsonb)",
            (erasure_job_id,),
        )
        maintenance.commit()

    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select state,result_status,state_reason from "
            "platform_attachments.artifact_versions where artifact_version_id=%s",
            (version_id,),
        ).fetchone() == ("deleted", "failed", "owner_erased")


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
        actor_id = uuid4()
        admin.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id,role,display_name,status) values "
            "(%s,'platform_owner','Review Owner','active')",
            (actor_id,),
        )
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
    with psycopg.connect(environment["urls"]["platform_control_app"]) as app:
        assert app.execute(
            "select platform_control.triage_conversation_feedback_v64(%s,%s,'triaged')",
            (actor_id, unhelpful_id),
        ).fetchone() == (unhelpful_id,)
        row = app.execute(
            "select triage_status,triaged_by_internal_user_id,triaged_at from "
            "platform_control.conversation_feedback where feedback_id=%s",
            (unhelpful_id,),
        ).fetchone()
        assert row[0:2] == ("triaged", actor_id)
        assert row[2] is not None
        with pytest.raises(psycopg.errors.CheckViolation):
            app.execute(
                "select platform_control.triage_conversation_feedback_v64(%s,%s,'pending_triage')",
                (actor_id, unhelpful_id),
            )


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
