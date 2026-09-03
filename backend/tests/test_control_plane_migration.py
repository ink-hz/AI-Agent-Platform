from __future__ import annotations

import json
import shutil
import socket
import subprocess
import tempfile
import uuid
from pathlib import Path

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
    MIGRATIONS / "034_directory_member_gender.sql"
)
ACCOUNT_GENDER_PROJECTION_MIGRATION = (
    MIGRATIONS / "035_account_gender_projection.sql"
)
AGENT_BRAIN_CONVERSATION_MIGRATION = (
    MIGRATIONS / "036_agent_brain_conversations.sql"
)
CONVERSATION_FEEDBACK_MIGRATION = (
    MIGRATIONS / "037_conversation_feedback.sql"
)
AGENT_BRAIN_SUMMARY_PHASE_MIGRATION = (
    MIGRATIONS / "038_agent_brain_summary_phase.sql"
)
AGENT_BRAIN_DURABLE_LOOP_MIGRATION = (
    MIGRATIONS / "041_agent_brain_durable_loop.sql"
)
AGENT_BRAIN_TASK_WAIT_MIGRATION = (
    MIGRATIONS / "050_agent_brain_task_wait_state.sql"
)
VOC_INACTIVE_STAFF_PROJECTION_MIGRATION = (
    MIGRATIONS / "061_voc_inactive_staff_projection.sql"
)
VOC_INACTIVE_STAFF_MAPPING_BINDING_MIGRATION = (
    MIGRATIONS / "062_voc_inactive_staff_mapping_binding.sql"
)
FAE_WORKBENCH_ACCESS_MIGRATION = (
    MIGRATIONS / "063_fae_workbench_access.sql"
)
DIRECTORY_MEMBER_EMPLOYEE_PROFILE_MIGRATION = (
    MIGRATIONS / "039_directory_member_employee_profile.sql"
)
ACCOUNT_EMPLOYEE_PROFILE_PROJECTION_MIGRATION = (
    MIGRATIONS / "040_account_employee_profile_projection.sql"
)
EXECUTION_RELAY_MIGRATION = MIGRATIONS / "028_execution_relay.sql"
AGENT_BRAIN_MIGRATION = MIGRATIONS / "029_agent_brain_mvp.sql"
AGENT_BRAIN_ORCHESTRATION_MIGRATION = (
    MIGRATIONS / "030_agent_brain_orchestration.sql"
)
EXECUTION_STOP_DELIVERY_MIGRATION = (
    MIGRATIONS / "031_execution_stop_delivery.sql"
)
CONTENT_KEY_CANARIES_MIGRATION = MIGRATIONS / "032_content_key_canaries.sql"
FIRST_PRODUCTION_BOOTSTRAP_MIGRATION = (
    MIGRATIONS / "033_first_production_bootstrap.sql"
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
    "platform_brain_worker",
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
    "execution_workers",
    "execution_worker_keys",
    "execution_jobs",
    "execution_events",
    "execution_worker_nonces",
    "agent_use_grants",
    "missions",
    "mission_messages",
    "mission_tasks",
    "mission_runs",
    "mission_events",
    "conversations",
    "conversation_messages",
    "conversation_turns",
    "conversation_events",
    "conversation_feedback",
    "content_key_canaries",
    "agent_launch_codes",
    "agent_identity_bindings",
    "agent_access_subjects",
    "enterprise_subject_links",
    "partner_organizations",
    "partner_operators",
    "partner_provider_identities",
    "partner_identity_binding_requests",
    "partner_agent_grants",
    "partner_login_attempts",
    "fae_workbench_grants",
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

PUBLISHED_MIGRATION_SHA256 = {
    "053_office_recipient_directory.sql": (
        "b0beb171e033dfdb0edc9fd023a62c69c52d8bc2e9766a747eb303f4ebc9deaa"
    ),
    "054_office_recipient_directory_department_order.sql": (
        "70fbd52845c54312b491f84955ad98f5a32f38e34533d367429290005fc90a1a"
    ),
}


def test_control_migration_versions_are_unique_and_contiguous() -> None:
    versions = [int(path.name.split("_", 1)[0]) for path in MIGRATIONS.glob("*.sql")]

    assert len(versions) == len(set(versions))
    assert sorted(versions) == list(range(1, max(versions) + 1))
    assert max(versions) == 63


def migration_sql(filename: str) -> str:
    return (MIGRATIONS / filename).read_text(encoding="utf-8").lower()


def test_fae_workbench_access_migration_is_function_only_for_app_role() -> None:
    sql = migration_sql("063_fae_workbench_access.sql")
    assert "create table platform_control.fae_workbench_grants" in sql
    assert "create unique index one_active_fae_workbench_grant" in sql
    assert "create function platform_control.grant_fae_workbench_access_v63" in sql
    assert "create function platform_control.replay_fae_workbench_grant_v63" in sql
    assert "create function platform_control.revoke_fae_workbench_access_v63" in sql
    assert "create function platform_control.has_fae_workbench_access_v63" in sql
    assert "create function platform_control.read_fae_workbench_grants_v63" in sql
    assert "create function platform_control.validate_fae_workbench_audit_v63" in sql
    for action in ("grant", "revoke"):
        for result in ("requested", "completed", "failed"):
            assert f"fae_workbench_{action}_{result}" in sql
    assert (
        "create or replace function platform_control.validate_audit_event_v2"
        not in sql
    )
    assert "grant execute on function" in sql
    assert "grant update on platform_control.fae_workbench_grants" not in sql
    assert "grant insert on platform_control.fae_workbench_grants" not in sql
    assert "grant delete on platform_control.fae_workbench_grants" not in sql


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
    assert DIRECTORY_MEMBER_EMPLOYEE_PROFILE_MIGRATION.is_file(), (
        "missing directory employee profile migration: "
        f"{DIRECTORY_MEMBER_EMPLOYEE_PROFILE_MIGRATION}"
    )
    assert ACCOUNT_EMPLOYEE_PROFILE_PROJECTION_MIGRATION.is_file(), (
        "missing account employee profile projection migration: "
        f"{ACCOUNT_EMPLOYEE_PROFILE_PROJECTION_MIGRATION}"
    )
    assert AGENT_BRAIN_CONVERSATION_MIGRATION.is_file(), (
        "missing Agent Brain Conversation migration: "
        f"{AGENT_BRAIN_CONVERSATION_MIGRATION}"
    )
    assert EXECUTION_RELAY_MIGRATION.is_file(), (
        f"missing execution relay migration: {EXECUTION_RELAY_MIGRATION}"
    )
    assert AGENT_BRAIN_MIGRATION.is_file(), (
        f"missing Agent Brain migration: {AGENT_BRAIN_MIGRATION}"
    )
    assert AGENT_BRAIN_ORCHESTRATION_MIGRATION.is_file(), (
        "missing Agent Brain orchestration migration: "
        f"{AGENT_BRAIN_ORCHESTRATION_MIGRATION}"
    )
    assert EXECUTION_STOP_DELIVERY_MIGRATION.is_file(), (
        "missing execution stop-delivery migration: "
        f"{EXECUTION_STOP_DELIVERY_MIGRATION}"
    )
    assert CONTENT_KEY_CANARIES_MIGRATION.is_file(), (
        f"missing content-key canary migration: {CONTENT_KEY_CANARIES_MIGRATION}"
    )
    assert AGENT_BRAIN_DURABLE_LOOP_MIGRATION.is_file(), (
        "missing durable Agent Brain migration: "
        f"{AGENT_BRAIN_DURABLE_LOOP_MIGRATION}"
    )
    assert AGENT_BRAIN_TASK_WAIT_MIGRATION.is_file(), (
        "missing Agent Brain task/wait migration: "
        f"{AGENT_BRAIN_TASK_WAIT_MIGRATION}"
    )
    assert VOC_INACTIVE_STAFF_PROJECTION_MIGRATION.is_file(), (
        "missing VOC inactive staff projection migration: "
        f"{VOC_INACTIVE_STAFF_PROJECTION_MIGRATION}"
    )
    assert VOC_INACTIVE_STAFF_MAPPING_BINDING_MIGRATION.is_file(), (
        "missing VOC inactive staff mapping binding migration: "
        f"{VOC_INACTIVE_STAFF_MAPPING_BINDING_MIGRATION}"
    )
    assert FAE_WORKBENCH_ACCESS_MIGRATION.is_file(), (
        "missing FAE workbench access migration: "
        f"{FAE_WORKBENCH_ACCESS_MIGRATION}"
    )


@pytest.mark.postgres
def test_voc_inactive_staff_projection_is_app_only_and_directory_read_only(
    control_database,
) -> None:
    production = control_database["environments"]["production"]
    function = (
        "platform_control.read_current_inactive_staff_member_v62"
        "(integer,bytea,integer,bytea)"
    )
    with psycopg.connect(production["admin"]) as connection:
        privileges = connection.execute(
            "select has_function_privilege('platform_control_app', %s, 'execute'), "
            "has_function_privilege('platform_directory_worker', %s, 'execute'), "
            "has_table_privilege('platform_control_app', "
            "'platform_control.directory_members', 'select')",
            (function, function),
        ).fetchone()
    assert privileges == (True, False, False)

    with psycopg.connect(production["urls"]["platform_control_app"]) as connection:
        assert connection.execute(
            "select * from "
            "platform_control.read_current_inactive_staff_member_v62("
            "%s,%s,%s,%s)",
            (1, b"c" * 32, 1, b"u" * 32),
        ).fetchall() == []


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


def test_published_control_migrations_are_byte_immutable() -> None:
    import hashlib

    assert {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(MIGRATIONS.glob("*.sql"))
        if path.name in PUBLISHED_MIGRATION_SHA256
    } == PUBLISHED_MIGRATION_SHA256


def test_employee_profile_migration_uses_nullable_encrypted_columns_only() -> None:
    migration = DIRECTORY_MEMBER_EMPLOYEE_PROFILE_MIGRATION.read_text(
        encoding="utf-8"
    ).lower()

    for purpose in ("real_name", "mobile", "primary_department"):
        assert f"{purpose}_ciphertext bytea" in migration
        assert f"{purpose}_nonce bytea" in migration
        assert f"{purpose}_encryption_key_version integer" in migration
        assert f"source_{purpose}_present_count integer" in migration
        assert f"{purpose}_present_count integer" in migration
    assert "source_schema_version between 0 and 3" in migration
    assert "create_directory_staging_generation_v39" in migration
    assert "stage_directory_member_v39" in migration
    assert "directory_generation_checksum_v39" in migration
    assert "validate_directory_generation_v39" in migration
    for purpose in ("real_name", "mobile", "primary_department"):
        assert (
            f"num_nonnulls({purpose}_ciphertext, {purpose}_nonce, "
            f"{purpose}_encryption_key_version) in (0,3)"
        ) in " ".join(migration.split())
        assert (
            f"num_nonnulls(selected_{purpose}_ciphertext, selected_{purpose}_nonce, "
            f"selected_{purpose}_encryption_version) in (0,3)"
        ) in " ".join(migration.split())
    for forbidden in (
        "real_name text",
        "mobile text",
        "primary_department text",
        "real_name_plaintext",
        "mobile_plaintext",
    ):
        assert forbidden not in migration


@pytest.mark.postgres
def test_employee_profile_staging_rejects_every_partial_encryption_tuple(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    worker_url = environment["urls"]["platform_directory_worker"]
    partial_shapes = (
        (True, False, False),
        (False, True, False),
        (False, False, True),
        (True, True, False),
        (True, False, True),
        (False, True, True),
    )
    for purpose_index in range(3):
        for shape in partial_shapes:
            generation_id = uuid.uuid4()
            with psycopg.connect(worker_url, autocommit=True) as connection:
                connection.execute(
                    "select platform_control.create_directory_staging_generation_v39("
                    "%s,%s,'scheduled',1,1,0,1,3,%s,0,0,0)",
                    (generation_id, uuid.uuid4(), "a" * 64),
                )
                triples: list[object | None] = [None] * 9
                offset = purpose_index * 3
                triples[offset : offset + 3] = (
                    b"c" * 16 if shape[0] else None,
                    b"n" * 12 if shape[1] else None,
                    1 if shape[2] else None,
                )
                with pytest.raises(
                    psycopg.errors.CheckViolation,
                    match="directory member profile invalid",
                ):
                    connection.execute(
                        "select platform_control.stage_directory_member_v39("
                        + ",".join(("%s",) * 22)
                        + ")",
                        (
                            generation_id,
                            uuid.uuid4(),
                            b"l" * 32,
                            1,
                            b"p" * 16,
                            1,
                            b"u" * 32,
                            1,
                            b"q" * 16,
                            1,
                            "Profile Member",
                            "active",
                            "female",
                            *triples,
                        ),
                    )


def test_account_employee_profile_projection_is_session_scoped_and_least_privilege() -> None:
    migration = ACCOUNT_EMPLOYEE_PROFILE_PROJECTION_MIGRATION.read_text(
        encoding="utf-8"
    ).lower()
    normalized = " ".join(migration.split())

    assert "read_current_account_employee_profile_v40( selected_session_id uuid )" in normalized
    assert "selected_internal_user_id" not in migration
    assert "selected_userid" not in migration
    assert "selected_staff" not in migration
    assert "from platform_control.web_sessions session" in migration
    assert "session.session_id=selected_session_id" in migration
    assert "security definer" in migration
    assert "set search_path = pg_catalog, platform_control" in migration
    assert "grant execute" in migration
    assert "platform_control_app" in migration
    assert "platform_directory_worker" in migration


def test_origin_account_department_projection_is_byte_immutable() -> None:
    import hashlib

    assert (
        hashlib.sha256(ACCOUNT_DEPARTMENT_PROJECTION_MIGRATION.read_bytes()).hexdigest()
        == "531d5b31b615bec5b17860816ff955a927bdfe4c5010909c9ab9b750a1d11fc3"
    )


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
                migration_versions = sorted(
                    int(path.name.split("_", 1)[0])
                    for path in MIGRATIONS.glob("*.sql")
                )
                assert cursor.fetchall() == [
                    (version, 64) for version in migration_versions
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
        "create_directory_staging_generation_v34": True,
        "create_directory_staging_generation_v39": True,
        "directory_generation_checksum_v34": False,
        "directory_generation_checksum_v39": False,
        "read_employee_profile_readiness_v39": True,
        "stage_directory_member_v34": True,
        "stage_directory_member_v39": True,
        "validate_directory_generation_v34": False,
        "validate_directory_generation_v39": False,
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
def test_current_account_employee_profile_projection_has_exact_app_grant(
    control_database,
) -> None:
    function_name = "read_current_account_employee_profile_v40"
    for environment in control_database["environments"].values():
        matched_app = environment["roles"][1]
        with psycopg.connect(environment["admin"]) as connection:
            rows = connection.execute(
                "select role_name,"
                "has_function_privilege(role_name,proc.oid,'execute'),"
                "has_function_privilege('public',proc.oid,'execute'),"
                "proc.prosecdef,proc.proconfig "
                "from pg_proc proc cross join unnest(%s::text[]) role_name "
                "where proc.pronamespace='platform_control'::regnamespace "
                "and proc.proname=%s order by role_name",
                (list(ROLES), function_name),
            ).fetchall()

        assert len(rows) == len(ROLES)
        for role, can_execute, public, security_definer, config in rows:
            assert can_execute is (role == matched_app)
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
                    "select column_name,is_nullable from information_schema.columns "
                    "where table_schema='platform_control' "
                    "and table_name='directory_members'"
                )
                member_columns = dict(cursor.fetchall())
                encrypted_profile_columns = {
                    f"{purpose}_{suffix}"
                    for purpose in ("real_name", "mobile", "primary_department")
                    for suffix in (
                        "ciphertext",
                        "nonce",
                        "encryption_key_version",
                    )
                }
                assert encrypted_profile_columns <= set(member_columns)
                assert all(
                    member_columns[column] == "YES"
                    for column in encrypted_profile_columns
                )
                assert not set(member_columns) & {
                    "real_name",
                    "mobile",
                    "primary_department",
                }

                cursor.execute(
                    "select column_name from information_schema.columns "
                    "where table_schema='platform_control' "
                    "and table_name='directory_generations'"
                )
                generation_columns = {row[0] for row in cursor.fetchall()}
                assert {
                    f"{prefix}{purpose}_present_count"
                    for prefix in ("", "source_")
                    for purpose in ("real_name", "mobile", "primary_department")
                } <= generation_columns

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


def _seed_fae_workbench_directory(
    database_url: str,
) -> dict[str, object]:
    generation_id = uuid.uuid4()
    unique_member_key = uuid.uuid4()
    inactive_member_key = uuid.uuid4()
    duplicate_member_keys = (uuid.uuid4(), uuid.uuid4())
    mixed_status_duplicate_member_keys = (uuid.uuid4(), uuid.uuid4())
    unique_name = f"FAE Unique {uuid.uuid4()}"
    inactive_name = f"FAE Inactive {uuid.uuid4()}"
    duplicate_name = f"FAE Duplicate {uuid.uuid4()}"
    mixed_status_duplicate_name = f"FAE Mixed Duplicate {uuid.uuid4()}"
    members = (
        (unique_member_key, unique_name, "active", 11),
        (inactive_member_key, inactive_name, "inactive", 12),
        (duplicate_member_keys[0], duplicate_name, "active", 13),
        (duplicate_member_keys[1], duplicate_name, "active", 14),
        (
            mixed_status_duplicate_member_keys[0],
            mixed_status_duplicate_name,
            "active",
            15,
        ),
        (
            mixed_status_duplicate_member_keys[1],
            mixed_status_duplicate_name,
            "inactive",
            16,
        ),
    )
    with psycopg.connect(database_url) as connection:
        connection.execute(
            "delete from platform_control.management_mutations "
            "where action in ('grant_fae_workbench','revoke_fae_workbench')"
        )
        connection.execute(
            "delete from platform_control.fae_workbench_grants"
        )
        connection.execute(
            "delete from platform_control.provider_identities"
        )
        owner = connection.execute(
            "select internal_user_id from platform_control.internal_users "
            "where role = 'platform_owner' limit 1"
        ).fetchone()
        if owner is None:
            owner_id = uuid.uuid4()
            connection.execute(
                "insert into platform_control.internal_users ("
                "internal_user_id,role,display_name,status) values "
                "(%s,'platform_owner','FAE Boundary Owner','active')",
                (owner_id,),
            )
        else:
            owner_id = owner[0]
            connection.execute(
                "update platform_control.internal_users set status='active', "
                "locally_invalidated_at=null where internal_user_id=%s",
                (owner_id,),
            )
        connection.execute(
            "insert into platform_control.provider_identity_key_policies ("
            "provider,lookup_transition_versions) values "
            "('dingtalk',array[1]) on conflict (provider) do update set "
            "lookup_transition_versions=excluded.lookup_transition_versions",
        )
        connection.execute(
            "insert into platform_control.directory_generations ("
            "generation_id,status,member_count,source_member_count,"
            "source_schema_version,completed_at) values "
            "(%s,'complete',%s,%s,3,clock_timestamp())",
            (generation_id, len(members), len(members)),
        )
        for member_key, display_name, status, marker in members:
            connection.execute(
                "insert into platform_control.directory_members ("
                "generation_id,member_key,subject_kind,lookup_hmac,"
                "lookup_key_version,encrypted_provider_id,"
                "encryption_key_version,union_lookup_hmac,"
                "union_lookup_key_version,union_encrypted_provider_id,"
                "union_encryption_key_version,display_name,status) values "
                "(%s,%s,'employee',%s,1,%s,1,%s,1,%s,1,%s,%s)",
                (
                    generation_id,
                    member_key,
                    bytes([marker]) * 32,
                    bytes([marker + 32]) * 32,
                    bytes([marker + 64]) * 32,
                    bytes([marker + 96]) * 32,
                    display_name,
                    status,
                ),
            )
        connection.execute(
            "update platform_control.directory_state set "
            "active_generation_id=%s,last_complete_at=clock_timestamp(),"
            "updated_at=clock_timestamp() where singleton",
            (generation_id,),
        )
    return {
        "owner_id": owner_id,
        "generation_id": generation_id,
        "unique_member_key": unique_member_key,
        "unique_name": unique_name,
        "inactive_member_key": inactive_member_key,
        "inactive_name": inactive_name,
        "duplicate_member_key": duplicate_member_keys[0],
        "duplicate_name": duplicate_name,
        "mixed_status_duplicate_member_key": mixed_status_duplicate_member_keys[0],
        "mixed_status_duplicate_name": mixed_status_duplicate_name,
    }


def _append_fae_workbench_request(
    audit_url: str,
    *,
    event_id: uuid.UUID,
    operation_id: uuid.UUID,
    actor_id: uuid.UUID,
    target_id: uuid.UUID,
    action: str,
    metadata: dict[str, object],
) -> None:
    reason = (
        "fae_workbench_access_approved"
        if action == "grant"
        else "fae_workbench_access_revoked"
    )
    target_type = "directory_member" if action == "grant" else "internal_user"
    with psycopg.connect(audit_url) as connection:
        connection.execute(
            "select platform_control.append_audit_event("
            "%s,%s,%s,%s,%s,%s,'requested',%s,%s::jsonb)",
            (
                event_id,
                actor_id,
                f"fae_workbench_{action}_requested",
                target_type,
                str(target_id),
                operation_id,
                reason,
                json.dumps(
                    {
                        "operation_id": str(operation_id),
                        "result": "requested",
                        **metadata,
                    }
                ),
            ),
        )


def _grant_seeded_fae_member(
    environment: dict[str, object],
    seeded: dict[str, object],
) -> dict[str, object]:
    operation_id = uuid.uuid4()
    requested_audit_id = uuid.uuid4()
    new_user_id = uuid.uuid4()
    parameters = (
        operation_id,
        seeded["owner_id"],
        seeded["unique_name"],
        seeded["generation_id"],
        seeded["unique_member_key"],
        new_user_id,
        uuid.uuid4(),
        uuid.uuid4(),
        requested_audit_id,
    )
    _append_fae_workbench_request(
        environment["urls"]["platform_audit_append"],
        event_id=requested_audit_id,
        operation_id=operation_id,
        actor_id=seeded["owner_id"],
        target_id=seeded["unique_member_key"],
        action="grant",
        metadata={
            "expected_generation_id": str(seeded["generation_id"]),
            "expected_member_key": str(seeded["unique_member_key"]),
        },
    )
    with psycopg.connect(
        environment["urls"]["platform_control_app"]
    ) as connection:
        result = connection.execute(
            "select platform_control.grant_fae_workbench_access_v63("
            + ",".join(("%s",) * 9)
            + ")",
            parameters,
        ).fetchone()[0]
    return {
        "operation_id": operation_id,
        "requested_audit_id": requested_audit_id,
        "new_user_id": new_user_id,
        "parameters": parameters,
        "result": result,
    }


@pytest.mark.postgres
def test_fae_workbench_grant_provisions_identity_and_normal_login_reuses_it(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    seeded = _seed_fae_workbench_directory(environment["admin"])
    app_url = environment["urls"]["platform_control_app"]
    granted = _grant_seeded_fae_member(environment, seeded)
    parameters = granted["parameters"]
    new_user_id = granted["new_user_id"]
    with psycopg.connect(app_url) as connection:
        result = granted["result"]
        replay = connection.execute(
            "select platform_control.grant_fae_workbench_access_v63("
            + ",".join(("%s",) * 9)
            + ")",
            parameters,
        ).fetchone()[0]
        assert replay == result
        replay_context = connection.execute(
            "select platform_control.replay_fae_workbench_grant_v63(%s,%s,%s)",
            (granted["operation_id"], seeded["owner_id"], seeded["unique_name"]),
        ).fetchone()[0]
        assert replay_context == {
            "generation_id": str(seeded["generation_id"]),
            "member_key": str(seeded["unique_member_key"]),
            "result": result,
        }
        assert result["internal_user_id"] == str(new_user_id)
        assert result["permission"] == "manager"
        assert connection.execute(
            "select platform_control.has_fae_workbench_access_v63(%s)",
            (new_user_id,),
        ).fetchone() == (True,)
        assert connection.execute(
            "select internal_user_id from "
            "platform_control.read_fae_workbench_grants_v63()"
        ).fetchall() == [(new_user_id,)]

        resolved_user_id = connection.execute(
            "select platform_control.resolve_verified_dingtalk_member("
            + ",".join(("%s",) * 12)
            + ")",
            (
                uuid.uuid4(),
                seeded["unique_name"],
                uuid.uuid4(),
                bytes([11]) * 32,
                1,
                bytes([43]) * 32,
                1,
                uuid.uuid4(),
                bytes([75]) * 32,
                1,
                bytes([107]) * 32,
                1,
            ),
        ).fetchone()[0]
        assert resolved_user_id == new_user_id

    with psycopg.connect(environment["admin"]) as connection:
        assert connection.execute(
            "select role::text,status,last_confirmed_generation_id from "
            "platform_control.internal_users where internal_user_id=%s",
            (new_user_id,),
        ).fetchone() == ("member", "active", seeded["generation_id"])
        assert connection.execute(
            "select count(*) from platform_control.web_sessions "
            "where internal_user_id=%s",
            (new_user_id,),
        ).fetchone() == (0,)


@pytest.mark.postgres
def test_fae_workbench_grant_rejects_stable_directory_and_duplicate_codes(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    seeded_state = _seed_fae_workbench_directory(environment["admin"])
    app_url = environment["urls"]["platform_control_app"]
    audit_url = environment["urls"]["platform_audit_append"]
    seeded = seeded_state["generation_id"]
    owner_id = seeded_state["owner_id"]
    cases = (
        (
            f"FAE Missing {uuid.uuid4()}",
            uuid.uuid4(),
            seeded,
            "directory_member_not_found",
        ),
        (
            seeded_state["duplicate_name"],
            seeded_state["duplicate_member_key"],
            seeded,
            "directory_name_not_unique",
        ),
        (
            seeded_state["mixed_status_duplicate_name"],
            seeded_state["mixed_status_duplicate_member_key"],
            seeded,
            "directory_name_not_unique",
        ),
        (
            seeded_state["inactive_name"],
            seeded_state["inactive_member_key"],
            seeded,
            "directory_member_inactive",
        ),
        (
            seeded_state["unique_name"],
            seeded_state["unique_member_key"],
            uuid.uuid4(),
            "directory_generation_changed",
        ),
    )
    for display_name, member_key, expected_generation_id, error_code in cases:
        operation_id = uuid.uuid4()
        audit_event_id = uuid.uuid4()
        new_user_id = uuid.uuid4()
        _append_fae_workbench_request(
            audit_url,
            event_id=audit_event_id,
            operation_id=operation_id,
            actor_id=owner_id,
            target_id=member_key,
            action="grant",
            metadata={
                "expected_generation_id": str(expected_generation_id),
                "expected_member_key": str(member_key),
            },
        )
        with psycopg.connect(app_url) as connection:
            with pytest.raises(psycopg.errors.CheckViolation, match=error_code):
                connection.execute(
                    "select platform_control.grant_fae_workbench_access_v63("
                    + ",".join(("%s",) * 9)
                    + ")",
                    (
                        operation_id,
                        owner_id,
                        display_name,
                        expected_generation_id,
                        member_key,
                        new_user_id,
                        uuid.uuid4(),
                        uuid.uuid4(),
                        audit_event_id,
                    ),
                )


@pytest.mark.postgres
def test_fae_workbench_grant_rejects_identity_collision_and_missing_audit(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    seeded = _seed_fae_workbench_directory(environment["admin"])
    app_url = environment["urls"]["platform_control_app"]
    audit_url = environment["urls"]["platform_audit_append"]
    collision_name = f"FAE Collision {uuid.uuid4()}"
    collision_member_key = uuid.uuid4()
    collision_user_id = uuid.uuid4()
    generation_id = seeded["generation_id"]
    owner_id = seeded["owner_id"]
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.directory_members ("
            "generation_id,member_key,subject_kind,lookup_hmac,"
            "lookup_key_version,encrypted_provider_id,encryption_key_version,"
            "union_lookup_hmac,union_lookup_key_version,"
            "union_encrypted_provider_id,union_encryption_key_version,"
            "display_name,status) values "
            "(%s,%s,'employee',%s,1,%s,1,%s,1,%s,1,%s,'active')",
            (
                generation_id,
                collision_member_key,
                bytes([20]) * 32,
                bytes([52]) * 32,
                bytes([84]) * 32,
                bytes([116]) * 32,
                collision_name,
            ),
        )
        connection.execute(
            "insert into platform_control.internal_users ("
            "internal_user_id,role,display_name,status) values "
            "(%s,'member','Collision Holder','active')",
            (collision_user_id,),
        )
        connection.execute(
            "insert into platform_control.provider_identities ("
            "provider_identity_id,internal_user_id,subject_kind,lookup_hmac,"
            "lookup_key_version,encrypted_provider_id,encryption_key_version) "
            "values (%s,%s,'employee',%s,1,%s,1)",
            (
                uuid.uuid4(),
                collision_user_id,
                bytes([20]) * 32,
                bytes([52]) * 32,
            ),
        )

    operation_id = uuid.uuid4()
    audit_event_id = uuid.uuid4()
    new_user_id = uuid.uuid4()
    _append_fae_workbench_request(
        audit_url,
        event_id=audit_event_id,
        operation_id=operation_id,
        actor_id=owner_id,
        target_id=collision_member_key,
        action="grant",
        metadata={
            "expected_generation_id": str(generation_id),
            "expected_member_key": str(collision_member_key),
        },
    )
    parameters = (
        operation_id,
        owner_id,
        collision_name,
        generation_id,
        collision_member_key,
        new_user_id,
        uuid.uuid4(),
        uuid.uuid4(),
        audit_event_id,
    )
    with psycopg.connect(app_url) as connection, pytest.raises(
        psycopg.errors.CheckViolation, match="verified_identity_collision"
    ):
        connection.execute(
            "select platform_control.grant_fae_workbench_access_v63("
            + ",".join(("%s",) * 9)
            + ")",
            parameters,
        )

    missing_audit_parameters = (
        uuid.uuid4(),
        owner_id,
        collision_name,
        generation_id,
        collision_member_key,
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
    )
    with psycopg.connect(app_url) as connection, pytest.raises(
        psycopg.errors.CheckViolation, match="matching_audit_intent_required"
    ):
        connection.execute(
            "select platform_control.grant_fae_workbench_access_v63("
            + ",".join(("%s",) * 9)
            + ")",
            missing_audit_parameters,
        )


@pytest.mark.postgres
def test_fae_workbench_grant_only_reuses_member_role_identities(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    seeded = _seed_fae_workbench_directory(environment["admin"])
    owner_id = seeded["owner_id"]
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_control.directory_members set internal_user_id=%s "
            "where generation_id=%s and member_key=%s",
            (owner_id, seeded["generation_id"], seeded["unique_member_key"]),
        )
        connection.execute(
            "insert into platform_control.provider_identities ("
            "provider_identity_id,internal_user_id,subject_kind,lookup_hmac,"
            "lookup_key_version,encrypted_provider_id,encryption_key_version) "
            "values (%s,%s,'employee',%s,1,%s,1),"
            "(%s,%s,'employee_union',%s,1,%s,1)",
            (
                uuid.uuid4(),
                owner_id,
                bytes([11]) * 32,
                bytes([43]) * 32,
                uuid.uuid4(),
                owner_id,
                bytes([75]) * 32,
                bytes([107]) * 32,
            ),
        )

    operation_id = uuid.uuid4()
    audit_event_id = uuid.uuid4()
    _append_fae_workbench_request(
        environment["urls"]["platform_audit_append"],
        event_id=audit_event_id,
        operation_id=operation_id,
        actor_id=owner_id,
        target_id=seeded["unique_member_key"],
        action="grant",
        metadata={
            "expected_generation_id": str(seeded["generation_id"]),
            "expected_member_key": str(seeded["unique_member_key"]),
        },
    )
    with psycopg.connect(
        environment["urls"]["platform_control_app"]
    ) as connection, pytest.raises(
        psycopg.errors.CheckViolation, match="verified_identity_collision"
    ):
        connection.execute(
            "select platform_control.grant_fae_workbench_access_v63("
            + ",".join(("%s",) * 9)
            + ")",
            (
                operation_id,
                owner_id,
                seeded["unique_name"],
                seeded["generation_id"],
                seeded["unique_member_key"],
                uuid.uuid4(),
                uuid.uuid4(),
                uuid.uuid4(),
                audit_event_id,
            ),
        )


@pytest.mark.postgres
def test_fae_workbench_grant_and_revoke_are_operation_idempotent_only(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    app_url = environment["urls"]["platform_control_app"]
    audit_url = environment["urls"]["platform_audit_append"]
    seeded = _seed_fae_workbench_directory(environment["admin"])
    granted = _grant_seeded_fae_member(environment, seeded)
    grant_row = (
        uuid.UUID(granted["result"]["grant_id"]),
        granted["new_user_id"],
        granted["result"]["row_version"],
    )
    owner_id = seeded["owner_id"]
    member = (
        seeded["generation_id"],
        seeded["unique_member_key"],
        seeded["unique_name"],
    )

    duplicate_operation_id = uuid.uuid4()
    duplicate_audit_id = uuid.uuid4()
    duplicate_new_user_id = uuid.uuid4()
    _append_fae_workbench_request(
        audit_url,
        event_id=duplicate_audit_id,
        operation_id=duplicate_operation_id,
        actor_id=owner_id,
        target_id=member[1],
        action="grant",
        metadata={
            "expected_generation_id": str(member[0]),
            "expected_member_key": str(member[1]),
        },
    )
    with psycopg.connect(app_url) as connection:
        with pytest.raises(
            psycopg.errors.CheckViolation, match="fae_workbench_already_granted"
        ):
            connection.execute(
                "select platform_control.grant_fae_workbench_access_v63("
                + ",".join(("%s",) * 9)
                + ")",
                (
                    duplicate_operation_id,
                    owner_id,
                    member[2],
                    member[0],
                    member[1],
                    duplicate_new_user_id,
                    uuid.uuid4(),
                    uuid.uuid4(),
                    duplicate_audit_id,
                ),
            )

    revoke_operation_id = uuid.uuid4()
    revoke_audit_id = uuid.uuid4()
    _append_fae_workbench_request(
        audit_url,
        event_id=revoke_audit_id,
        operation_id=revoke_operation_id,
        actor_id=owner_id,
        target_id=grant_row[1],
        action="revoke",
        metadata={"expected_row_version": grant_row[2]},
    )
    revoke_parameters = (
        revoke_operation_id,
        owner_id,
        grant_row[1],
        grant_row[2],
        revoke_audit_id,
    )
    with psycopg.connect(app_url) as connection:
        revoked = connection.execute(
            "select platform_control.revoke_fae_workbench_access_v63("
            "%s,%s,%s,%s,%s)",
            revoke_parameters,
        ).fetchone()[0]
        replay = connection.execute(
            "select platform_control.revoke_fae_workbench_access_v63("
            "%s,%s,%s,%s,%s)",
            revoke_parameters,
        ).fetchone()[0]
        assert replay == revoked
        assert revoked["row_version"] == grant_row[2] + 1
        assert connection.execute(
            "select platform_control.has_fae_workbench_access_v63(%s)",
            (grant_row[1],),
        ).fetchone() == (False,)

    new_revoke_operation_id = uuid.uuid4()
    new_revoke_audit_id = uuid.uuid4()
    _append_fae_workbench_request(
        audit_url,
        event_id=new_revoke_audit_id,
        operation_id=new_revoke_operation_id,
        actor_id=owner_id,
        target_id=grant_row[1],
        action="revoke",
        metadata={"expected_row_version": revoked["row_version"]},
    )
    with psycopg.connect(app_url) as connection:
        with pytest.raises(
            psycopg.errors.CheckViolation, match="fae_workbench_not_granted"
        ):
            connection.execute(
                "select platform_control.revoke_fae_workbench_access_v63("
                "%s,%s,%s,%s,%s)",
                (
                    new_revoke_operation_id,
                    owner_id,
                    grant_row[1],
                    revoked["row_version"],
                    new_revoke_audit_id,
                ),
            )


@pytest.mark.postgres
def test_fae_workbench_table_has_no_runtime_direct_mutation_rights(
    control_database,
) -> None:
    for environment in control_database["environments"].values():
        app_role = environment["roles"][1]
        with psycopg.connect(environment["admin"]) as connection:
            for runtime_role in environment["roles"]:
                assert connection.execute(
                    "select has_table_privilege(%s,"
                    "'platform_control.fae_workbench_grants','insert'),"
                    "has_table_privilege(%s,"
                    "'platform_control.fae_workbench_grants','update'),"
                    "has_table_privilege(%s,"
                    "'platform_control.fae_workbench_grants','delete'),"
                    "has_table_privilege(%s,"
                    "'platform_control.fae_workbench_grants','select')",
                    (runtime_role,) * 4,
                ).fetchone() == (False, False, False, False)
                expected_execute = runtime_role == app_role
                assert connection.execute(
                    "select has_function_privilege(%s,"
                    "'platform_control.grant_fae_workbench_access_v63("
                    "uuid,uuid,text,uuid,uuid,uuid,uuid,uuid,uuid)','execute'),"
                    "has_function_privilege(%s,"
                    "'platform_control.replay_fae_workbench_grant_v63("
                    "uuid,uuid,text)','execute'),"
                    "has_function_privilege(%s,"
                    "'platform_control.revoke_fae_workbench_access_v63("
                    "uuid,uuid,uuid,bigint,uuid)','execute'),"
                    "has_function_privilege(%s,"
                    "'platform_control.has_fae_workbench_access_v63(uuid)',"
                    "'execute'),has_function_privilege(%s,"
                    "'platform_control.read_fae_workbench_grants_v63()',"
                    "'execute')",
                    (runtime_role,) * 5,
                ).fetchone() == (expected_execute,) * 5
        app_url = environment["urls"][app_role]
        _assert_denied(
            app_url,
            "insert into platform_control.fae_workbench_grants ("
            "grant_id,internal_user_id,permission,created_by_internal_user_id,"
            "created_audit_event_id) values (gen_random_uuid(),gen_random_uuid(),"
            "'manager',gen_random_uuid(),gen_random_uuid())",
        )
        _assert_denied(
            app_url,
            "update platform_control.fae_workbench_grants set row_version=1",
        )
        _assert_denied(
            app_url,
            "delete from platform_control.fae_workbench_grants",
        )
