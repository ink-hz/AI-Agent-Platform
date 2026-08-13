from __future__ import annotations

from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import uuid

import psycopg
import pytest


BACKEND = Path(__file__).parents[1]
MIGRATIONS = BACKEND / "control_migrations"
MIGRATION = MIGRATIONS / "001_identity_security.sql"
ROLES = (
    "platform_control_migrator",
    "platform_control_app",
    "platform_directory_worker",
    "platform_stream_ingest",
    "platform_audit_append",
    "platform_control_maintenance",
)
TABLES = {
    "schema_migrations",
    "internal_users",
    "provider_identities",
    "directory_generations",
    "directory_state",
    "directory_members",
    "directory_departments",
    "department_closure",
    "member_departments",
    "login_attempts",
    "web_sessions",
    "observation_grants",
    "stream_inbox",
    "sync_runs",
    "auth_rate_buckets",
    "audit_events",
}


def test_first_control_migration_exists() -> None:
    assert MIGRATION.is_file(), f"missing migration: {MIGRATION}"


def _available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@pytest.fixture(scope="module")
def control_database():
    if not all(shutil.which(command) for command in ("initdb", "pg_ctl")):
        pytest.fail("disposable PostgreSQL requires initdb and pg_ctl")

    root = Path(tempfile.mkdtemp(prefix="control-pg-", dir="/tmp"))
    data = root / "data"
    socket_dir = root / "socket"
    socket_dir.mkdir()
    port = _available_port()
    subprocess.run(
        [
            "initdb",
            "-D",
            str(data),
            "--auth=trust",
            "--encoding=UTF8",
            "--no-locale",
            "--username=control_test_admin",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            "pg_ctl",
            "-D",
            str(data),
            "-l",
            str(root / "postgres.log"),
            "-o",
            f"-F -h 127.0.0.1 -p {port} -k {socket_dir}",
            "start",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    admin_url = (
        f"postgresql://control_test_admin@127.0.0.1:{port}/postgres"
    )
    database_name = f"control_test_{uuid.uuid4().hex}"
    try:
        with psycopg.connect(admin_url, autocommit=True) as connection:
            with connection.cursor() as cursor:
                for role in ROLES:
                    cursor.execute(
                        psycopg.sql.SQL("create role {} login").format(
                            psycopg.sql.Identifier(role)
                        )
                    )
                cursor.execute(
                    psycopg.sql.SQL(
                        "create database {} owner platform_control_migrator "
                        "template template0"
                    ).format(psycopg.sql.Identifier(database_name))
                )
        migrator_url = (
            f"postgresql://platform_control_migrator@127.0.0.1:{port}/"
            f"{database_name}"
        )
        database_admin_url = (
            f"postgresql://control_test_admin@127.0.0.1:{port}/{database_name}"
        )
        from app.control_plane.migrate import migrate_control_database

        migrate_control_database(migrator_url, MIGRATIONS)
        yield {
            "admin": database_admin_url,
            "migrator": migrator_url,
            "name": database_name,
        }
    finally:
        subprocess.run(
            ["pg_ctl", "-D", str(data), "stop", "-m", "immediate"],
            check=False,
            capture_output=True,
            text=True,
        )
        shutil.rmtree(root, ignore_errors=True)


@pytest.mark.postgres
def test_migration_is_idempotent_and_checksum_guarded(control_database, tmp_path):
    from app.control_plane.migrate import (
        MigrationChecksumMismatch,
        migrate_control_database,
    )

    migrate_control_database(control_database["migrator"], MIGRATIONS)
    migrate_control_database(control_database["migrator"], MIGRATIONS)

    with psycopg.connect(control_database["migrator"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "select version, length(sha256) "
                "from platform_control.schema_migrations"
            )
            assert cursor.fetchall() == [(1, 64)]

    changed = tmp_path / "migrations"
    changed.mkdir()
    (changed / MIGRATION.name).write_text(
        MIGRATION.read_text(encoding="utf-8") + "\nselect 1;\n",
        encoding="utf-8",
    )
    with pytest.raises(MigrationChecksumMismatch):
        migrate_control_database(control_database["migrator"], changed)


@pytest.mark.postgres
def test_migration_creates_complete_constrained_control_model(control_database):
    with psycopg.connect(control_database["migrator"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "select table_name from information_schema.tables "
                "where table_schema = 'platform_control'"
            )
            assert {row[0] for row in cursor.fetchall()} == TABLES

            cursor.execute(
                "select enumlabel from pg_enum "
                "where enumtypid = 'platform_control.user_role'::regtype "
                "order by enumsortorder"
            )
            assert [row[0] for row in cursor.fetchall()] == [
                "member",
                "management_viewer",
                "platform_owner",
            ]

            cursor.execute(
                "select column_name from information_schema.columns "
                "where table_schema = 'platform_control' "
                "and table_name = 'provider_identities'"
            )
            provider_columns = {row[0] for row in cursor.fetchall()}
            assert {
                "subject_kind",
                "lookup_hmac",
                "lookup_key_version",
                "encrypted_provider_id",
                "encryption_key_version",
            } <= provider_columns
            assert not provider_columns & {
                "provider_id",
                "userid",
                "unionid",
                "corp_id",
                "mobile",
                "email",
            }

            cursor.execute(
                "select column_name from information_schema.columns "
                "where table_schema = 'platform_control' "
                "and table_name = 'web_sessions'"
            )
            session_columns = {row[0] for row in cursor.fetchall()}
            assert {"token_hash", "csrf_hash"} <= session_columns
            assert not session_columns & {"token", "csrf_token", "cookie_token"}

            cursor.execute(
                "select indexdef from pg_indexes where schemaname = 'platform_control'"
            )
            indexes = "\n".join(row[0].lower() for row in cursor.fetchall())
            assert "one_platform_owner" in indexes
            assert "where ((role = 'platform_owner'" in indexes
            assert "stream_inbox" in indexes and "event_key" in indexes
            assert "observation_grants" in indexes and "revoked_at is null" in indexes


@pytest.mark.postgres
def test_partial_owner_and_active_grant_uniqueness(control_database):
    first_owner = uuid.uuid4()
    second_owner = uuid.uuid4()
    viewer = uuid.uuid4()
    with psycopg.connect(control_database["migrator"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "insert into platform_control.internal_users "
                "(internal_user_id, role, display_name, status) "
                "values (%s, 'platform_owner', 'owner one', 'active')",
                (first_owner,),
            )
            with pytest.raises(psycopg.errors.UniqueViolation):
                cursor.execute(
                    "insert into platform_control.internal_users "
                    "(internal_user_id, role, display_name, status) "
                    "values (%s, 'platform_owner', 'owner two', 'active')",
                    (second_owner,),
                )
        connection.rollback()
        with connection.cursor() as cursor:
            cursor.execute(
                "insert into platform_control.internal_users "
                "(internal_user_id, role, display_name, status) values "
                "(%s, 'platform_owner', 'inactive owner', 'inactive'), "
                "(%s, 'management_viewer', 'viewer', 'active')",
                (first_owner, viewer),
            )
            cursor.execute(
                "insert into platform_control.observation_grants "
                "(observation_grant_id, agent_id, viewer_internal_user_id, "
                "created_by) values (%s, 'agent-a', %s, %s)",
                (uuid.uuid4(), viewer, first_owner),
            )
            with pytest.raises(psycopg.errors.UniqueViolation):
                cursor.execute(
                    "insert into platform_control.observation_grants "
                    "(observation_grant_id, agent_id, viewer_internal_user_id, "
                    "created_by) values (%s, 'agent-a', %s, %s)",
                    (uuid.uuid4(), viewer, first_owner),
                )


def _assert_denied(database_url: str, role: str, statement: str) -> None:
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                psycopg.sql.SQL("set role {}").format(psycopg.sql.Identifier(role))
            )
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cursor.execute(statement)


@pytest.mark.postgres
def test_runtime_roles_cannot_cross_grant_boundaries(control_database):
    database_url = control_database["admin"]
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "select "
                "has_schema_privilege('public', 'platform_control', 'usage'), "
                "has_table_privilege('platform_control_app', "
                "'platform_control.web_sessions', 'select'), "
                "has_table_privilege('platform_stream_ingest', "
                "'platform_control.stream_inbox', 'insert'), "
                "has_table_privilege('platform_audit_append', "
                "'platform_control.audit_events', 'insert'), "
                "has_table_privilege('platform_control_maintenance', "
                "'platform_control.audit_events', 'delete')"
            )
            assert cursor.fetchone() == (False, True, True, False, False)

    _assert_denied(
        database_url,
        "platform_control_app",
        "insert into platform_control.stream_inbox "
        "(event_key, event_type, encrypted_payload, encryption_key_version) "
        "values ('cross-grant', 'test', '\\x00', 1)",
    )
    _assert_denied(
        database_url,
        "platform_directory_worker",
        "insert into platform_control.login_attempts "
        "(login_attempt_id, attempt_kind, state_hash, expires_at) "
        "values (gen_random_uuid(), 'qr', '\\x00', now())",
    )
    _assert_denied(
        database_url,
        "platform_stream_ingest",
        "select * from platform_control.internal_users",
    )
    _assert_denied(
        database_url,
        "platform_audit_append",
        "select * from platform_control.audit_events",
    )


@pytest.mark.postgres
def test_audit_is_append_only_and_retention_is_fixed_cutoff(control_database):
    database_url = control_database["admin"]
    for role in ROLES[1:]:
        _assert_denied(
            database_url,
            role,
            "update platform_control.audit_events set event_type = 'changed'",
        )
        _assert_denied(
            database_url,
            role,
            "delete from platform_control.audit_events",
        )

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "select prosecdef from pg_proc "
                "where oid = 'platform_control.retain_audit_events(timestamptz)'::regprocedure"
            )
            assert cursor.fetchone() == (True,)
            cursor.execute(
                "select "
                "has_function_privilege('platform_control_maintenance', "
                "'platform_control.retain_audit_events(timestamptz)', 'execute'), "
                "has_function_privilege('platform_control_app', "
                "'platform_control.retain_audit_events(timestamptz)', 'execute'), "
                "has_function_privilege('public', "
                "'platform_control.retain_audit_events(timestamptz)', 'execute')"
            )
            assert cursor.fetchone() == (True, False, False)

            cursor.execute("set role platform_control_maintenance")
            with pytest.raises(psycopg.errors.CheckViolation):
                cursor.execute(
                    "select platform_control.retain_audit_events(now() - interval '364 days')"
                )
