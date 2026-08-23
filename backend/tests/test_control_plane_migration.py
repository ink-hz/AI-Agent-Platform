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
IDENTITY_KEY_POLICY_HARDENING_MIGRATION = (
    MIGRATIONS / "004_reject_null_identity_key_versions.sql"
)
AUDITED_ROLE_ADMINISTRATION_MIGRATION = (
    MIGRATIONS / "005_audited_role_administration.sql"
)
AUDITED_MUTATION_BOUNDARY_MIGRATION = (
    MIGRATIONS / "006_audited_mutation_boundary.sql"
)
RECONCILABLE_AUDIT_BOUNDARY_MIGRATION = (
    MIGRATIONS / "007_reconcilable_audit_boundary.sql"
)
TERMINAL_AUDIT_STATE_MIGRATION = MIGRATIONS / "008_terminal_audit_state.sql"
AUDIT_REQUEST_SERIALIZATION_MIGRATION = (
    MIGRATIONS / "009_audit_request_serialization.sql"
)
VERIFIED_IDENTITY_REFRESH_MIGRATION = (
    MIGRATIONS / "010_verified_identity_refresh.sql"
)
VERIFIED_IDENTITY_BOUNDARY_MIGRATION = (
    MIGRATIONS / "011_verified_identity_boundary.sql"
)
VERIFIED_IDENTITY_PAIR_BOUNDARY_MIGRATION = (
    MIGRATIONS / "012_verified_identity_pair_boundary.sql"
)
DIRECTORY_PROMOTION_BOUNDARY_MIGRATION = (
    MIGRATIONS / "013_directory_promotion_boundary.sql"
)
EXACT_IDENTITY_MAPPING_BOUNDARY_MIGRATION = (
    MIGRATIONS / "014_exact_identity_mapping_boundary.sql"
)
PLATFORM_ADMIN_MUTATION_MIGRATION = (
    MIGRATIONS / "025_platform_admin_mutations.sql"
)
INACTIVE_ADMIN_CLEANUP_MIGRATION = (
    MIGRATIONS / "026_inactive_platform_admin_cleanup.sql"
)
ACCOUNT_DEPARTMENT_PROJECTION_MIGRATION = (
    MIGRATIONS / "027_account_department_projection.sql"
)
DIRECTORY_MEMBER_GENDER_MIGRATION = (
    MIGRATIONS / "028_directory_member_gender.sql"
)
ACCOUNT_GENDER_PROJECTION_MIGRATION = (
    MIGRATIONS / "029_account_gender_projection.sql"
)
RELEASE_1_PLAN = (
    Path(__file__).parents[2]
    / "docs/superpowers/plans/2026-08-13-dingtalk-identity-release-1.md"
)
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
    "management_mutations",
    "worker_heartbeats",
    "directory_event_subject_state",
}

IMMUTABLE_MIGRATION_SHA256 = {
    "001_identity_security.sql": "309cf6aebdb37d984823faebb859e58f44d387cda4c2fb4bdf61af06868541cd",
    "002_isolate_environment_roles.sql": "837bb27aa7ee09ff52e424c978c2362cad4e40a25d1c02a4ced0183c61dcbd2f",
    "003_identity_key_policy.sql": "4bef30a941e95f0e7508b5ad07c27fd1cf2673effad52738aac8b1fcf6c217f4",
    "004_reject_null_identity_key_versions.sql": "e12c96fc6e6c7f1e563834f8b9ad1f7a6595cd3525043d59afc5bc204baa7ef6",
    "005_audited_role_administration.sql": "836517d461b349e635a0183d2fbb7c88698b1bacfaba609360947e33599b2177",
    "006_audited_mutation_boundary.sql": "7d1886ee0d162ee7303020369a394227b5f6aa958986633e1e763d721b0911a8",
    "007_reconcilable_audit_boundary.sql": "35794c341050cdac641fe4eea0cb15d155d4ebbdbab55288942a9c53762b11ec",
    "008_terminal_audit_state.sql": "11bfb519e242005049a0bcf1d539eefa43738e03d4392646e8c1bf6158096e9b",
    "009_audit_request_serialization.sql": "f9cbb79af2d820795db53c59e5f140f20a077ba00da6fd716ceef059a72bc220",
    "010_verified_identity_refresh.sql": "a6695b5fcbad6a5b13c639dcabe1eacbe19898a1041c7d67dbf29f69a1865ca1",
    "011_verified_identity_boundary.sql": "8febc87dde9ccd091914c0fc0fdaf8f1fc9bcbbe0f247727bb4c914019ea0225",
    "012_verified_identity_pair_boundary.sql": "63892ec38e49514b34d38c3fc851616981aaee0af172855dccb889a22888343b",
    "013_directory_promotion_boundary.sql": "1d324cfa3cdd8e9c41555acb3c302383094b1e1aaa6713aec87d6a01b5500759",
    "014_exact_identity_mapping_boundary.sql": "4281cf9129035b2e07d7c6ed3029e558b216032ec81065692d6648892ca9bd9a",
    "015_secure_web_sessions.sql": "551a81a9be8d9ae6900ab258052852f3d667d516794407945023693416d0e50d",
    "016_rate_limit_boundary.sql": "5f6f081db2ebafe0341327ea006fa108462a71e13f7a32a6f390aa4c36c20e8e",
    "017_rate_limit_hardening.sql": "fc388ac41947a84f11a5a1bb4bf4bff0f7b087964f0562e05f94f1f8d6cc09ad",
    "018_bound_rate_maintenance.sql": "71093149909a22446d481ec8297fc4a55526320e2a185a5899a69e5902dcfeae",
    "019_atomic_directory_reconciliation.sql": "af554cf70e678706f976abec808292c781258ce3646244b22b4912fe202a934c",
}


def test_first_control_migration_exists() -> None:
    assert MIGRATION.is_file(), f"missing migration: {MIGRATION}"
    assert HARDENING_MIGRATION.is_file(), (
        f"missing security migration: {HARDENING_MIGRATION}"
    )
    assert IDENTITY_KEY_POLICY_MIGRATION.is_file(), (
        f"missing identity key policy migration: {IDENTITY_KEY_POLICY_MIGRATION}"
    )
    assert IDENTITY_KEY_POLICY_HARDENING_MIGRATION.is_file(), (
        "missing identity key policy hardening migration: "
        f"{IDENTITY_KEY_POLICY_HARDENING_MIGRATION}"
    )
    assert AUDITED_ROLE_ADMINISTRATION_MIGRATION.is_file(), (
        "missing audited role administration migration: "
        f"{AUDITED_ROLE_ADMINISTRATION_MIGRATION}"
    )
    assert AUDITED_MUTATION_BOUNDARY_MIGRATION.is_file(), (
        "missing audited mutation boundary migration: "
        f"{AUDITED_MUTATION_BOUNDARY_MIGRATION}"
    )
    assert RECONCILABLE_AUDIT_BOUNDARY_MIGRATION.is_file(), (
        "missing reconcilable audit boundary migration: "
        f"{RECONCILABLE_AUDIT_BOUNDARY_MIGRATION}"
    )
    assert TERMINAL_AUDIT_STATE_MIGRATION.is_file(), (
        f"missing terminal audit state migration: {TERMINAL_AUDIT_STATE_MIGRATION}"
    )
    assert AUDIT_REQUEST_SERIALIZATION_MIGRATION.is_file(), (
        "missing audit request serialization migration: "
        f"{AUDIT_REQUEST_SERIALIZATION_MIGRATION}"
    )
    assert VERIFIED_IDENTITY_REFRESH_MIGRATION.is_file(), (
        "missing verified identity refresh migration: "
        f"{VERIFIED_IDENTITY_REFRESH_MIGRATION}"
    )
    assert VERIFIED_IDENTITY_BOUNDARY_MIGRATION.is_file(), (
        "missing verified identity boundary migration: "
        f"{VERIFIED_IDENTITY_BOUNDARY_MIGRATION}"
    )
    assert VERIFIED_IDENTITY_PAIR_BOUNDARY_MIGRATION.is_file(), (
        "missing verified identity pair boundary migration: "
        f"{VERIFIED_IDENTITY_PAIR_BOUNDARY_MIGRATION}"
    )
    assert DIRECTORY_PROMOTION_BOUNDARY_MIGRATION.is_file(), (
        "missing directory promotion boundary migration: "
        f"{DIRECTORY_PROMOTION_BOUNDARY_MIGRATION}"
    )
    assert EXACT_IDENTITY_MAPPING_BOUNDARY_MIGRATION.is_file(), (
        "missing exact identity mapping boundary migration: "
        f"{EXACT_IDENTITY_MAPPING_BOUNDARY_MIGRATION}"
    )
    assert INACTIVE_ADMIN_CLEANUP_MIGRATION.is_file(), (
        "missing inactive administrator cleanup migration: "
        f"{INACTIVE_ADMIN_CLEANUP_MIGRATION}"
    )
    assert ACCOUNT_DEPARTMENT_PROJECTION_MIGRATION.is_file(), (
        "missing account department projection migration: "
        f"{ACCOUNT_DEPARTMENT_PROJECTION_MIGRATION}"
    )
    assert DIRECTORY_MEMBER_GENDER_MIGRATION.is_file(), (
        "missing directory member gender migration: "
        f"{DIRECTORY_MEMBER_GENDER_MIGRATION}"
    )
    assert ACCOUNT_GENDER_PROJECTION_MIGRATION.is_file(), (
        "missing account gender projection migration: "
        f"{ACCOUNT_GENDER_PROJECTION_MIGRATION}"
    )


def test_control_migrations_001_through_019_are_byte_immutable() -> None:
    import hashlib

    assert {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(
            (
                *MIGRATIONS.glob("00[1-9]_*.sql"),
                *MIGRATIONS.glob("01[0-9]_*.sql"),
            )
        )
    } == IMMUTABLE_MIGRATION_SHA256


def test_task6_and_task8_share_exported_directory_identity_lock_contract() -> None:
    migration = DIRECTORY_PROMOTION_BOUNDARY_MIGRATION.read_text(encoding="utf-8")
    plan = RELEASE_1_PLAN.read_text(encoding="utf-8")
    task6 = plan.split("### Task 6:", 1)[1].split("### Task 7:", 1)[0]
    task8 = plan.split("### Task 8:", 1)[1].split("### Task 9:", 1)[0]

    assert "lock_dingtalk_identity_directory" in migration
    assert "promote_verified_directory_generation" in migration
    assert "consume_attempt_and_issue_session" in task6
    assert "lock_dingtalk_identity_directory" in task6
    assert "same database transaction" in task6.lower()
    assert "promote_verified_directory_generation" in task8
    assert "directory_state" in task8
    assert "raw" in task8.lower()


def test_platform_admin_mutations_serialize_with_directory_promotion() -> None:
    migrations = {
        "assign_platform_admin": PLATFORM_ADMIN_MUTATION_MIGRATION.read_text(
            encoding="utf-8"
        ),
        "revoke_platform_admin": INACTIVE_ADMIN_CLEANUP_MIGRATION.read_text(
            encoding="utf-8"
        ),
    }
    for function_name, migration in migrations.items():
        function = migration.split(
            f"function platform_control.{function_name}", 1
        )[1].split("$function$;", 1)[0]
        assert function.index(
            "perform platform_control.lock_dingtalk_identity_directory();"
        ) < function.index("perform pg_advisory_xact_lock(")
        assert function.index("perform pg_advisory_xact_lock(") < function.index(
            "perform platform_control.require_platform_owner(selected_actor_id);"
        )
        assert function.index(
            "perform platform_control.require_platform_owner(selected_actor_id);"
        ) < function.index("replay := platform_control.replay_management_mutation(")


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
                assert cursor.fetchall() == [
                    (version, 64) for version in range(1, 30)
                ]

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


@pytest.mark.postgres
def test_directory_gender_functions_have_exact_environment_grants(
    control_database,
) -> None:
    protected_functions = {
        "create_directory_staging_generation_v28": True,
        "directory_generation_checksum_v28": False,
        "stage_directory_member_v28": True,
        "validate_directory_generation_v28": False,
    }
    for environment in control_database["environments"].values():
        matched_worker = environment["roles"][2]
        with psycopg.connect(environment["admin"]) as connection:
            rows = connection.execute(
                "select proc.proname,role_name,"
                "has_function_privilege(role_name,proc.oid,'execute'),"
                "has_function_privilege('public',proc.oid,'execute'),"
                "proc.prosecdef,proc.proconfig "
                "from pg_proc proc cross join unnest(%s::text[]) role_name "
                "where proc.pronamespace='platform_control'::regnamespace "
                "and proc.proname=any(%s) order by proc.proname,role_name",
                (list(ROLES), list(protected_functions)),
            ).fetchall()

        assert len(rows) == len(protected_functions) * len(ROLES)
        for name, role, can_execute, public, security_definer, config in rows:
            assert can_execute is (
                protected_functions[name] and role == matched_worker
            )
            assert public is False
            assert security_definer is True
            assert config == ["search_path=pg_catalog, platform_control"]


@pytest.mark.postgres
def test_migration_commits_each_numbered_file_before_the_next(
    control_database, monkeypatch, tmp_path
):
    from app.control_plane import dsn as control_dsn
    from app.control_plane import migrate as migration_runner
    from app.control_plane.migrate import (
        MigrationChecksumMismatch,
        migrate_control_database,
    )

    database_name = f"migration_transactions_{uuid.uuid4().hex}"
    owner_role = OWNER_ROLES[0]
    migrator_role = PRODUCTION_ROLES[0]
    admin_url = control_database["cluster_admin"]
    database_admin_url = (
        f"postgresql://control_test_admin@127.0.0.1:"
        f"{control_database['port']}/{database_name}"
    )
    migrator_url = (
        f"postgresql://{migrator_role}:{ROLE_PASSWORDS[migrator_role]}@"
        f"127.0.0.1:{control_database['port']}/{database_name}"
    )
    migrations = tmp_path / "consecutive-enum-migrations"
    migrations.mkdir()
    enum_migration = migrations / "900_add_test_role.sql"
    enum_migration.write_text(
        "alter type platform_control.transaction_test_role "
        "add value 'platform_admin';\n",
        encoding="utf-8",
    )
    (migrations / "901_use_test_role.sql").write_text(
        "insert into platform_control.transaction_test_users (role) "
        "values ('platform_admin');\n",
        encoding="utf-8",
    )
    monkeypatch.setitem(
        control_dsn._DATABASE_ENVIRONMENTS, database_name, "production"
    )

    try:
        with psycopg.connect(admin_url, autocommit=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    psycopg.sql.SQL(
                        "create database {} owner {} template template0"
                    ).format(
                        psycopg.sql.Identifier(database_name),
                        psycopg.sql.Identifier(owner_role),
                    )
                )
                cursor.execute(
                    psycopg.sql.SQL(
                        "revoke connect on database {} from public"
                    ).format(psycopg.sql.Identifier(database_name))
                )
                cursor.execute(
                    psycopg.sql.SQL(
                        "grant connect on database {} to {}"
                    ).format(
                        psycopg.sql.Identifier(database_name),
                        psycopg.sql.Identifier(migrator_role),
                    )
                )
                cursor.execute(
                    psycopg.sql.SQL("grant {} to {}").format(
                        psycopg.sql.Identifier(owner_role),
                        psycopg.sql.Identifier(migrator_role),
                    )
                )

        with psycopg.connect(database_admin_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    psycopg.sql.SQL("set local role {}").format(
                        psycopg.sql.Identifier(owner_role)
                    )
                )
                cursor.execute("create schema platform_control")
                cursor.execute(
                    "create type platform_control.transaction_test_role "
                    "as enum ('member')"
                )
                cursor.execute(
                    "create table platform_control.transaction_test_users ("
                    "role platform_control.transaction_test_role not null)"
                )

        migrate_control_database(
            migrator_url,
            migrations,
            owner_role=owner_role,
        )

        with psycopg.connect(database_admin_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "select version from platform_control.schema_migrations "
                    "order by version"
                )
                assert cursor.fetchall() == [(900,), (901,)]
                cursor.execute(
                    "select role::text from "
                    "platform_control.transaction_test_users"
                )
                assert cursor.fetchall() == [("platform_admin",)]

        (migrations / "902_interrupt_after_apply.sql").write_text(
            "insert into platform_control.transaction_test_users (role) "
            "values ('member');\n",
            encoding="utf-8",
        )
        original_verify_or_apply = migration_runner.verify_or_apply

        def interrupt_after_apply(cursor, version, sha256, sql):
            original_verify_or_apply(cursor, version, sha256, sql)
            if version == 902:
                raise KeyboardInterrupt

        with monkeypatch.context() as interrupt:
            interrupt.setattr(
                migration_runner, "verify_or_apply", interrupt_after_apply
            )
            with pytest.raises(KeyboardInterrupt):
                migrate_control_database(
                    migrator_url,
                    migrations,
                    owner_role=owner_role,
                )

        with psycopg.connect(database_admin_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "select version from platform_control.schema_migrations "
                    "order by version"
                )
                assert cursor.fetchall() == [(900,), (901,)]
                cursor.execute(
                    "select role::text from "
                    "platform_control.transaction_test_users"
                )
                assert cursor.fetchall() == [("platform_admin",)]

        enum_migration.write_text(
            enum_migration.read_text(encoding="utf-8") + "select 1;\n",
            encoding="utf-8",
        )
        with pytest.raises(MigrationChecksumMismatch):
            migrate_control_database(
                migrator_url,
                migrations,
                owner_role=owner_role,
            )
    finally:
        with psycopg.connect(admin_url, autocommit=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "select pg_terminate_backend(pid) from pg_stat_activity "
                    "where datname = %s and pid <> pg_backend_pid()",
                    (database_name,),
                )
                cursor.execute(
                    psycopg.sql.SQL("drop database if exists {}").format(
                        psycopg.sql.Identifier(database_name)
                    )
                )
                cursor.execute(
                    psycopg.sql.SQL("revoke {} from {}").format(
                        psycopg.sql.Identifier(owner_role),
                        psycopg.sql.Identifier(migrator_role),
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
                    "platform_admin",
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
                assert "where (role = 'platform_owner'" in indexes
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
                "(internal_user_id,role,display_name,status) values "
                "(%s,'platform_admin','Admin One','active'),"
                "(%s,'platform_admin','Admin Two','active')",
                (uuid.uuid4(), uuid.uuid4()),
            )
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
def test_app_cannot_bypass_audited_authorization_functions(control_database):
    environment = control_database["environments"]["production"]
    app_url = environment["urls"]["platform_control_app"]
    owner_id = uuid.uuid4()
    target_id = uuid.uuid4()
    grant_id = uuid.uuid4()
    audit_id = uuid.uuid4()
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id, display_name, status, role) values "
            "(%s, 'Boundary Owner', 'active', 'platform_owner'), "
            "(%s, 'Boundary Target', 'active', 'management_viewer')",
            (owner_id, target_id),
        )
        connection.execute(
            "insert into platform_control.audit_events "
            "(audit_event_id, actor_internal_user_id, event_type, target_type, "
            "target_internal_id, request_id, result, reason_code) values "
            "(%s, %s, 'viewer_role_assignment_requested', 'internal_user', "
            "%s, %s, 'requested', 'access_approved')",
            (audit_id, owner_id, str(target_id), uuid.uuid4()),
        )
        connection.execute(
            "insert into platform_control.observation_grants "
            "(observation_grant_id, agent_id, viewer_internal_user_id, created_by) "
            "values (%s, 'boundary-agent', %s, %s)",
            (grant_id, target_id, owner_id),
        )

    statements = (
        "insert into platform_control.internal_users "
        "(internal_user_id, display_name, status) values "
        "(gen_random_uuid(), 'Bypass', 'active')",
        f"update platform_control.internal_users set role = 'platform_owner' "
        f"where internal_user_id = '{target_id}'",
        f"update platform_control.internal_users set role = 'member' "
        f"where internal_user_id = '{owner_id}'",
        f"update platform_control.internal_users set role_audit_event_id = "
        f"'{audit_id}' where internal_user_id = '{target_id}'",
        "insert into platform_control.observation_grants "
        "(observation_grant_id, agent_id, viewer_internal_user_id, created_by) "
        f"values (gen_random_uuid(), 'bypass-agent', '{target_id}', '{owner_id}')",
        f"update platform_control.observation_grants set revoked_at = now() "
        f"where observation_grant_id = '{grant_id}'",
        f"delete from platform_control.observation_grants "
        f"where observation_grant_id = '{grant_id}'",
    )
    for statement in statements:
        _assert_denied(app_url, statement)
    _assert_denied(
        environment["urls"]["platform_directory_worker"],
        f"update platform_control.internal_users set status = 'inactive' "
        f"where internal_user_id = '{target_id}'",
    )


@pytest.mark.postgres
def test_app_has_only_audited_management_mutation_functions(control_database):
    environment = control_database["environments"]["production"]
    roles = environment["roles"]
    with psycopg.connect(environment["admin"]) as connection:
        rows = connection.execute(
            "select proname, has_function_privilege(%s, proc.oid, 'execute'), "
            "has_function_privilege(%s, proc.oid, 'execute'), "
            "has_function_privilege('public', proc.oid, 'execute') "
            "from pg_proc proc where proc.pronamespace = "
            "'platform_control'::regnamespace and proname = any(%s) "
            "order by proname",
            (
                roles[1],
                roles[0],
                [
                    "assign_management_viewer",
                    "assign_platform_admin",
                    "revoke_management_viewer",
                    "revoke_platform_admin",
                    "grant_observation_scope",
                    "revoke_observation_scope",
                    "change_platform_owner_v2",
                ],
            ),
        ).fetchall()
    assert rows == [
        ("assign_management_viewer", True, False, False),
        ("assign_platform_admin", True, False, False),
        ("change_platform_owner_v2", False, True, False),
        ("grant_observation_scope", True, False, False),
        ("revoke_management_viewer", True, False, False),
        ("revoke_observation_scope", True, False, False),
        ("revoke_platform_admin", True, False, False),
    ]


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
                    "has_function_privilege(%s, "
                    "'platform_control.insert_stream_event_v21(text,text,bytea,integer)', "
                    "'execute'), "
                    "has_table_privilege(%s, "
                    "'platform_control.audit_events', 'insert'), "
                    "has_table_privilege(%s, "
                    "'platform_control.audit_events', 'delete')",
                    (roles[1], roles[3], roles[3], roles[4], roles[5]),
                )
                assert cursor.fetchone() == (
                    False, True, False, True, False, False
                )

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
def test_app_role_insert_rejects_invalid_identity_key_version_arrays(
    control_database,
) -> None:
    invalid_windows = (
        [None],
        [1, None],
        [1, 2, None],
        [0],
        [-1],
        [1, 0],
        [1, -1],
        [1, 2, 0],
        [1, 2, -1],
    )
    valid_windows = ([1], [1, 2], [1, 2, 3])

    for environment in control_database["environments"].values():
        app_url = environment["urls"][environment["roles"][1]]
        for versions in (*invalid_windows, *valid_windows):
            with psycopg.connect(environment["admin"]) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "delete from "
                        "platform_control.provider_identity_key_policies"
                    )

            with psycopg.connect(app_url) as connection:
                with connection.cursor() as cursor:
                    if versions in invalid_windows:
                        with pytest.raises(psycopg.errors.CheckViolation):
                            cursor.execute(
                                "insert into "
                                "platform_control.provider_identity_key_policies "
                                "(provider, lookup_transition_versions) "
                                "values ('dingtalk', %s)",
                                (versions,),
                            )
                    else:
                        cursor.execute(
                            "insert into "
                            "platform_control.provider_identity_key_policies "
                            "(provider, lookup_transition_versions) "
                            "values ('dingtalk', %s)",
                            (versions,),
                        )
                        cursor.execute(
                            "select lookup_transition_versions from "
                            "platform_control.provider_identity_key_policies "
                            "where provider = 'dingtalk'"
                        )
                        assert cursor.fetchone() == (versions,)
                connection.rollback()


@pytest.mark.postgres
def test_identity_key_policy_hardening_fails_closed_on_poisoned_row_then_applies(
    control_database,
) -> None:
    from app.control_plane.migrate import migrate_control_database

    environment = control_database["environments"]["production"]
    migrator_role = environment["roles"][0]
    migrator_url = environment["urls"][migrator_role]
    app_url = environment["urls"][environment["roles"][1]]

    with psycopg.connect(environment["admin"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "alter table platform_control.provider_identity_key_policies "
                "drop constraint if exists "
                "provider_identity_key_policies_versions_nonnull_positive"
            )
            cursor.execute(
                "delete from platform_control.schema_migrations where version = 4"
            )
            cursor.execute(
                "delete from platform_control.provider_identity_key_policies"
            )

    with psycopg.connect(app_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "insert into platform_control.provider_identity_key_policies "
                "(provider, lookup_transition_versions) "
                "values ('dingtalk', array[null]::integer[])"
            )

    with psycopg.connect(
        control_database["cluster_admin"], autocommit=True
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                psycopg.sql.SQL("grant {} to {}").format(
                    psycopg.sql.Identifier(environment["owner"]),
                    psycopg.sql.Identifier(migrator_role),
                )
            )
    try:
        with pytest.raises(
            psycopg.errors.CheckViolation,
            match="provider identity key policy data invalid",
        ):
            migrate_control_database(
                migrator_url,
                MIGRATIONS,
                owner_role=environment["owner"],
            )

        with psycopg.connect(environment["admin"]) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "select lookup_transition_versions from "
                    "platform_control.provider_identity_key_policies"
                )
                assert cursor.fetchone() == ([None],)
                cursor.execute(
                    "select count(*) from platform_control.schema_migrations "
                    "where version = 4"
                )
                assert cursor.fetchone() == (0,)
                cursor.execute(
                    "delete from "
                    "platform_control.provider_identity_key_policies"
                )

        migrate_control_database(
            migrator_url,
            MIGRATIONS,
            owner_role=environment["owner"],
        )
        migrate_control_database(
            migrator_url,
            MIGRATIONS,
            owner_role=environment["owner"],
        )

        with psycopg.connect(environment["admin"]) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "select count(*) from platform_control.schema_migrations "
                    "where version = 4"
                )
                assert cursor.fetchone() == (1,)
                cursor.execute(
                    "select convalidated from pg_constraint where conname = "
                    "'provider_identity_key_policies_versions_nonnull_positive'"
                )
                assert cursor.fetchone() == (True,)
    finally:
        with psycopg.connect(environment["admin"]) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "delete from "
                    "platform_control.provider_identity_key_policies "
                    "where array_position(lookup_transition_versions, null) "
                    "is not null"
                )
        with psycopg.connect(
            control_database["cluster_admin"], autocommit=True
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    psycopg.sql.SQL("revoke {} from {}").format(
                        psycopg.sql.Identifier(environment["owner"]),
                        psycopg.sql.Identifier(migrator_role),
                    )
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
