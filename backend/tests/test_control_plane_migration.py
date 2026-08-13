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
HARDENING_MIGRATION = MIGRATIONS / "002_isolate_environment_roles.sql"
IDENTITY_KEY_POLICY_MIGRATION = MIGRATIONS / "003_identity_key_policy.sql"
PRODUCTION_ROLES = (
    "platform_control_migrator",
    "platform_control_app",
    "platform_directory_worker",
    "platform_stream_ingest",
    "platform_audit_append",
    "platform_control_maintenance",
)
PREVIEW_ROLES = tuple(f"{role}_preview" for role in PRODUCTION_ROLES)
ROLES = PRODUCTION_ROLES + PREVIEW_ROLES
OWNER_ROLES = (
    "platform_control_owner",
    "platform_control_owner_preview",
)
ROLE_PASSWORDS = {
    role: f"{index:064x}"
    for index, role in enumerate(ROLES, start=1)
}
ENVIRONMENTS = {
    "production": {
        "database": "agent_platform_control",
        "owner": OWNER_ROLES[0],
        "roles": PRODUCTION_ROLES,
    },
    "preview": {
        "database": "agent_platform_control_preview",
        "owner": OWNER_ROLES[1],
        "roles": PREVIEW_ROLES,
    },
}
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
    "provider_identity_key_policies",
}


def test_first_control_migration_exists() -> None:
    assert MIGRATION.is_file(), f"missing migration: {MIGRATION}"
    assert HARDENING_MIGRATION.is_file(), (
        f"missing security migration: {HARDENING_MIGRATION}"
    )
    assert IDENTITY_KEY_POLICY_MIGRATION.is_file(), (
        f"missing identity key policy migration: {IDENTITY_KEY_POLICY_MIGRATION}"
    )


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
    try:
        with psycopg.connect(admin_url, autocommit=True) as connection:
            with connection.cursor() as cursor:
                for owner_role in OWNER_ROLES:
                    cursor.execute(
                        psycopg.sql.SQL(
                            "create role {} nologin nosuperuser nocreatedb "
                            "nocreaterole noreplication nobypassrls noinherit"
                        ).format(psycopg.sql.Identifier(owner_role))
                    )
                for role in ROLES:
                    inheritance = (
                        "noinherit" if "migrator" in role else "inherit"
                    )
                    cursor.execute(
                        psycopg.sql.SQL(
                            "create role {} login password {} nosuperuser "
                            "nocreatedb nocreaterole noreplication nobypassrls "
                            + inheritance
                        ).format(
                            psycopg.sql.Identifier(role),
                            psycopg.sql.Literal(ROLE_PASSWORDS[role]),
                        )
                    )
                for environment in ENVIRONMENTS.values():
                    cursor.execute(
                        psycopg.sql.SQL(
                            "create database {} owner {} template template0"
                        ).format(
                            psycopg.sql.Identifier(environment["database"]),
                            psycopg.sql.Identifier(environment["owner"]),
                        )
                    )
                    cursor.execute(
                        psycopg.sql.SQL(
                            "revoke connect on database {} from public"
                        ).format(
                            psycopg.sql.Identifier(environment["database"])
                        )
                    )
                    cursor.execute(
                        psycopg.sql.SQL(
                            "grant connect on database {} to {}"
                        ).format(
                            psycopg.sql.Identifier(environment["database"]),
                            psycopg.sql.SQL(", ").join(
                                psycopg.sql.Identifier(role)
                                for role in environment["roles"]
                            ),
                        )
                    )
                    cursor.execute(
                        psycopg.sql.SQL("grant {} to {}").format(
                            psycopg.sql.Identifier(environment["owner"]),
                            psycopg.sql.Identifier(environment["roles"][0]),
                        )
                    )
        from app.control_plane.migrate import migrate_control_database

        result = {"cluster_admin": admin_url, "port": port, "environments": {}}
        try:
            for name, environment in ENVIRONMENTS.items():
                database_name = environment["database"]
                role_urls = {
                    role: (
                        f"postgresql://{role}:{ROLE_PASSWORDS[role]}@"
                        f"127.0.0.1:{port}/{database_name}"
                    )
                    for role in environment["roles"]
                }
                migrate_control_database(
                    role_urls[environment["roles"][0]],
                    MIGRATIONS,
                    owner_role=environment["owner"],
                )
                migrate_control_database(
                    role_urls[environment["roles"][0]],
                    MIGRATIONS,
                    owner_role=environment["owner"],
                )
                result["environments"][name] = {
                    **environment,
                    "admin": (
                        f"postgresql://control_test_admin@127.0.0.1:{port}/"
                        f"{database_name}"
                    ),
                    "urls": role_urls,
                }
        finally:
            with psycopg.connect(admin_url, autocommit=True) as connection:
                with connection.cursor() as cursor:
                    for environment in ENVIRONMENTS.values():
                        cursor.execute(
                            psycopg.sql.SQL("revoke {} from {}").format(
                                psycopg.sql.Identifier(environment["owner"]),
                                psycopg.sql.Identifier(environment["roles"][0]),
                            )
                        )
        yield result
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

    for environment in control_database["environments"].values():
        with psycopg.connect(environment["admin"]) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "select version, length(sha256) "
                    "from platform_control.schema_migrations order by version"
                )
                assert cursor.fetchall() == [(1, 64), (2, 64), (3, 64)]

    changed = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS, changed)
    (changed / HARDENING_MIGRATION.name).write_text(
        HARDENING_MIGRATION.read_text(encoding="utf-8") + "\nselect 1;\n",
        encoding="utf-8",
    )
    environment = control_database["environments"]["production"]
    with psycopg.connect(
        control_database["cluster_admin"], autocommit=True
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                psycopg.sql.SQL("grant {} to {}").format(
                    psycopg.sql.Identifier(environment["owner"]),
                    psycopg.sql.Identifier(environment["roles"][0]),
                )
            )
    try:
        with pytest.raises(MigrationChecksumMismatch):
            migrate_control_database(
                environment["urls"][environment["roles"][0]],
                changed,
                owner_role=environment["owner"],
            )
    finally:
        with psycopg.connect(
            control_database["cluster_admin"], autocommit=True
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    psycopg.sql.SQL("revoke {} from {}").format(
                        psycopg.sql.Identifier(environment["owner"]),
                        psycopg.sql.Identifier(environment["roles"][0]),
                    )
                )


def test_migration_rejects_unapproved_owner_identifier() -> None:
    from app.control_plane.migrate import migrate_control_database

    with pytest.raises(ValueError, match="unsupported control owner role"):
        migrate_control_database(
            "postgresql://unused",
            MIGRATIONS,
            owner_role='platform_control_owner"; reset role; --',
        )


@pytest.mark.postgres
def test_migration_creates_complete_constrained_control_model(control_database):
    for environment in control_database["environments"].values():
        with psycopg.connect(environment["admin"]) as connection:
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
                assert not session_columns & {
                    "token", "csrf_token", "cookie_token"
                }

                cursor.execute(
                    "select indexdef from pg_indexes "
                    "where schemaname = 'platform_control'"
                )
                indexes = "\n".join(
                    row[0].lower() for row in cursor.fetchall()
                )
                assert "one_platform_owner" in indexes
                assert "where ((role = 'platform_owner'" in indexes
                assert "stream_inbox" in indexes and "event_key" in indexes
                assert (
                    "observation_grants" in indexes
                    and "revoked_at is null" in indexes
                )


@pytest.mark.postgres
def test_partial_owner_and_active_grant_uniqueness(control_database):
    first_owner = uuid.uuid4()
    second_owner = uuid.uuid4()
    viewer = uuid.uuid4()
    database_url = control_database["environments"]["production"]["admin"]
    with psycopg.connect(database_url) as connection:
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


def _assert_denied(database_url: str, statement: str) -> None:
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cursor.execute(statement)


@pytest.mark.postgres
def test_runtime_roles_cannot_cross_grant_boundaries(control_database):
    for environment in control_database["environments"].values():
        roles = environment["roles"]
        with psycopg.connect(environment["admin"]) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "select "
                    "has_schema_privilege('public', 'platform_control', 'usage'), "
                    "has_table_privilege(%s, "
                    "'platform_control.web_sessions', 'select'), "
                    "has_table_privilege(%s, "
                    "'platform_control.stream_inbox', 'insert'), "
                    "has_table_privilege(%s, "
                    "'platform_control.audit_events', 'insert'), "
                    "has_table_privilege(%s, "
                    "'platform_control.audit_events', 'delete')",
                    (roles[1], roles[3], roles[4], roles[5]),
                )
                assert cursor.fetchone() == (False, True, True, False, False)

        _assert_denied(
            environment["urls"][roles[1]],
            "insert into platform_control.stream_inbox "
            "(event_key, event_type, encrypted_payload, encryption_key_version) "
            "values ('cross-grant', 'test', '\\x00', 1)",
        )
        _assert_denied(
            environment["urls"][roles[2]],
            "insert into platform_control.login_attempts "
            "(login_attempt_id, attempt_kind, state_hash, expires_at) "
            "values (gen_random_uuid(), 'qr', '\\x00', now())",
        )
        _assert_denied(
            environment["urls"][roles[3]],
            "select * from platform_control.internal_users",
        )
        _assert_denied(
            environment["urls"][roles[4]],
            "select * from platform_control.audit_events",
        )


@pytest.mark.postgres
def test_identity_key_policy_is_environment_owned_and_maintenance_only_mutable(
    control_database,
) -> None:
    for environment in control_database["environments"].values():
        roles = environment["roles"]
        app_url = environment["urls"][roles[1]]
        maintenance_url = environment["urls"][roles[5]]
        opposite_name = (
            "preview"
            if environment["database"] == "agent_platform_control"
            else "production"
        )
        opposite_roles = control_database["environments"][opposite_name]["roles"]
        with psycopg.connect(environment["admin"]) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "select tableowner from pg_tables where schemaname = "
                    "'platform_control' and tablename = "
                    "'provider_identity_key_policies'"
                )
                assert cursor.fetchone() == (environment["owner"],)
                cursor.execute(
                    "select has_table_privilege(%s, "
                    "'platform_control.provider_identity_key_policies', 'select'), "
                    "has_table_privilege(%s, "
                    "'platform_control.provider_identity_key_policies', 'insert'), "
                    "has_table_privilege(%s, "
                    "'platform_control.provider_identity_key_policies', 'update'), "
                    "has_table_privilege(%s, "
                    "'platform_control.provider_identity_key_policies', 'delete'), "
                    "has_function_privilege(%s, "
                    "'platform_control.set_provider_identity_key_policy(text,integer[])', "
                    "'execute'), has_function_privilege(%s, "
                    "'platform_control.set_provider_identity_key_policy(text,integer[])', "
                    "'execute'), has_function_privilege('public', "
                    "'platform_control.set_provider_identity_key_policy(text,integer[])', "
                    "'execute')",
                    (roles[1], roles[1], roles[1], roles[1], roles[5], roles[1]),
                )
                assert cursor.fetchone() == (
                    True, True, False, False, True, False, False
                )
                cursor.execute(
                    "select role_name, has_table_privilege(role_name, "
                    "'platform_control.provider_identity_key_policies', 'select') "
                    "from unnest(%s::text[]) role_name",
                    (list(opposite_roles),),
                )
                assert cursor.fetchall() == [
                    (role, False) for role in opposite_roles
                ]

        with psycopg.connect(app_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "insert into platform_control.provider_identity_key_policies "
                    "(provider, lookup_transition_versions) values "
                    "('dingtalk', array[1,2]) on conflict do nothing"
                )
                cursor.execute(
                    "select lookup_transition_versions from "
                    "platform_control.provider_identity_key_policies "
                    "where provider = 'dingtalk'"
                )
                assert cursor.fetchone() == ([1, 2],)
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    cursor.execute(
                        "update platform_control.provider_identity_key_policies "
                        "set lookup_transition_versions = array[2,3] "
                        "where provider = 'dingtalk'"
                    )
            connection.rollback()

        with psycopg.connect(maintenance_url) as connection:
            with connection.cursor() as cursor:
                for invalid in ([0], [2, 1], [1, 1], [1, 3], [1, 2, 3, 4]):
                    with pytest.raises(psycopg.errors.CheckViolation):
                        cursor.execute(
                            "select platform_control.set_provider_identity_key_policy("
                            "'dingtalk', %s)",
                            (invalid,),
                        )
                    connection.rollback()
                cursor.execute(
                    "select platform_control.set_provider_identity_key_policy("
                    "'dingtalk', array[2,3])"
                )

        with psycopg.connect(environment["admin"]) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "select lookup_transition_versions, updated_at >= created_at "
                    "from platform_control.provider_identity_key_policies "
                    "where provider = 'dingtalk'"
                )
                assert cursor.fetchone() == ([2, 3], True)

        for denied_role in roles[:5]:
            _assert_denied(
                environment["urls"][denied_role],
                "select platform_control.set_provider_identity_key_policy("
                "'dingtalk', array[1,2])",
            )


@pytest.mark.postgres
def test_environment_roles_are_hardened_isolated_and_unprivileged_after_migration(
    control_database,
):
    with psycopg.connect(control_database["cluster_admin"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "select rolname, rolcanlogin, rolsuper, rolcreatedb, "
                "rolcreaterole, rolreplication, rolbypassrls, rolinherit "
                "from pg_roles where rolname = any(%s) order by rolname",
                (list(ROLES + OWNER_ROLES),),
            )
            attributes = {row[0]: row[1:] for row in cursor.fetchall()}
            assert set(attributes) == set(ROLES + OWNER_ROLES)
            for role in ROLES:
                assert attributes[role] == (
                    True,
                    False,
                    False,
                    False,
                    False,
                    False,
                    "migrator" not in role,
                )
            for owner_role in OWNER_ROLES:
                assert attributes[owner_role] == (
                    False, False, False, False, False, False, False
                )

            cursor.execute(
                "select owner.rolname, member.rolname "
                "from pg_auth_members membership "
                "join pg_roles owner on owner.oid = membership.roleid "
                "join pg_roles member on member.oid = membership.member "
                "where member.rolname = any(%s)",
                (list(ROLES),),
            )
            assert cursor.fetchall() == []

            for environment in control_database["environments"].values():
                cursor.execute(
                    "select datdba::regrole::text from pg_database "
                    "where datname = %s",
                    (environment["database"],),
                )
                assert cursor.fetchone() == (environment["owner"],)

    for environment in control_database["environments"].values():
        opposite_name = (
            "preview" if environment["database"] == "agent_platform_control"
            else "production"
        )
        opposite = control_database["environments"][opposite_name]
        with psycopg.connect(environment["admin"]) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "select nspowner::regrole::text from pg_namespace "
                    "where nspname = 'platform_control'"
                )
                assert cursor.fetchone() == (environment["owner"],)
                cursor.execute(
                    "select distinct tableowner from pg_tables "
                    "where schemaname = 'platform_control'"
                )
                assert cursor.fetchall() == [(environment["owner"],)]
                cursor.execute(
                    "select distinct proowner::regrole::text from pg_proc "
                    "where pronamespace = 'platform_control'::regnamespace"
                )
                assert cursor.fetchall() == [(environment["owner"],)]
                cursor.execute(
                    "select role_name, "
                    "has_schema_privilege(role_name, 'platform_control', 'usage'), "
                    "has_table_privilege(role_name, "
                    "'platform_control.internal_users', 'select') "
                    "from unnest(%s::text[]) role_name",
                    (list(opposite["roles"]),),
                )
                assert cursor.fetchall() == [
                    (role, False, False) for role in opposite["roles"]
                ]

        for role in environment["roles"]:
            cross_database_url = (
                f"postgresql://{role}:{ROLE_PASSWORDS[role]}@127.0.0.1:"
                f"{control_database['port']}/{opposite['database']}"
            )
            with pytest.raises(
                psycopg.OperationalError,
                match="permission denied for database",
            ):
                psycopg.connect(cross_database_url)


@pytest.mark.postgres
def test_audit_is_append_only_and_retention_is_fixed_cutoff(control_database):
    for environment in control_database["environments"].values():
        roles = environment["roles"]
        for role in roles:
            _assert_denied(
                environment["urls"][role],
                "update platform_control.audit_events "
                "set event_type = 'changed'",
            )
            _assert_denied(
                environment["urls"][role],
                "delete from platform_control.audit_events",
            )

        migrator_url = environment["urls"][roles[0]]
        _assert_denied(
            migrator_url,
            psycopg.sql.SQL("set role {}").format(
                psycopg.sql.Identifier(environment["owner"])
            ).as_string(),
        )
        _assert_denied(
            migrator_url,
            "select platform_control.retain_audit_events("
            "now() - interval '366 days')",
        )
        _assert_denied(
            migrator_url,
            "select platform_control.append_audit_event("
            "gen_random_uuid(), null, 'test', 'test', 'test', "
            "gen_random_uuid(), 'ok', 'test', '{}'::jsonb)",
        )

        old_event = uuid.uuid4()
        recent_event = uuid.uuid4()
        with psycopg.connect(environment["admin"]) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "select prosecdef, proowner::regrole::text from pg_proc "
                    "where oid = 'platform_control.retain_audit_events("
                    "timestamptz)'::regprocedure"
                )
                assert cursor.fetchone() == (True, environment["owner"])
                cursor.execute(
                    "select "
                    "has_function_privilege(%s, "
                    "'platform_control.retain_audit_events(timestamptz)', "
                    "'execute'), "
                    "has_function_privilege(%s, "
                    "'platform_control.retain_audit_events(timestamptz)', "
                    "'execute'), "
                    "has_function_privilege('public', "
                    "'platform_control.retain_audit_events(timestamptz)', "
                    "'execute')",
                    (roles[5], roles[1]),
                )
                assert cursor.fetchone() == (True, False, False)
                cursor.execute(
                    "insert into platform_control.audit_events ("
                    "audit_event_id, event_type, target_type, target_internal_id, "
                    "request_id, result, reason_code, occurred_at) values "
                    "(%s, 'old', 'test', 'old', %s, 'ok', 'test', "
                    "now() - interval '366 days'), "
                    "(%s, 'recent', 'test', 'recent', %s, 'ok', 'test', now())",
                    (old_event, uuid.uuid4(), recent_event, uuid.uuid4()),
                )

        maintenance_url = environment["urls"][roles[5]]
        with psycopg.connect(maintenance_url) as connection:
            with connection.cursor() as cursor:
                with pytest.raises(psycopg.errors.CheckViolation):
                    cursor.execute(
                        "select platform_control.retain_audit_events("
                        "now() - interval '364 days')"
                    )
            connection.rollback()
            with connection.cursor() as cursor:
                cursor.execute(
                    "select platform_control.retain_audit_events("
                    "now() - interval '365 days')"
                )
                assert cursor.fetchone() == (1,)

        with psycopg.connect(environment["admin"]) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "select audit_event_id from platform_control.audit_events "
                    "where audit_event_id = any(%s)",
                    ([old_event, recent_event],),
                )
                assert cursor.fetchall() == [(recent_event,)]
