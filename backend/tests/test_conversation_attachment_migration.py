from __future__ import annotations

# Pytest fixtures are imported into this module's namespace for discovery.
# ruff: noqa: F401,F811
from pathlib import Path

import psycopg
import pytest
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
            assert feedback_columns["triage_status"] == "'pending_triage'::text"
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
            "consume_task_grant_v64",
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
