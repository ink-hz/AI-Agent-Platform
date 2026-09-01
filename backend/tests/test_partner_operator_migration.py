from __future__ import annotations

import hashlib
import shutil
import socket
import subprocess
import uuid

# Pytest fixture is imported into this module's namespace for discovery.
# ruff: noqa: F401,F811
from datetime import datetime, timezone

import psycopg
import pytest
from test_control_plane_migration import (
    MIGRATIONS,
    OWNER_ROLES,
    PRODUCTION_ROLES,
    ROLE_PASSWORDS,
    ROLES,
    control_database,
)

from app.control_plane.crypto import IdentityKeyring
from app.control_plane.partner_identity_crypto import PartnerProviderIdentityCodec
from app.control_plane.partner_models import (
    PartnerIdentityError,
    VerifiedProviderSubject,
)
from app.control_plane.partner_repository import PartnerRepository
from app.control_plane.partner_service import PartnerService
from app.execution_relay.content_crypto import ContentCodec

MIGRATION = MIGRATIONS / "056_partner_operator_identity.sql"
MANAGEMENT_REJECTION_MIGRATION = MIGRATIONS / "057_partner_management_rejection.sql"
PARTNER_AUTHENTICATION_MIGRATION = MIGRATIONS / "058_partner_authentication.sql"
GENERIC_AGENT_LAUNCH_MIGRATION = MIGRATIONS / "059_generic_agent_launch_bindings.sql"
TASK2_V54_SHA256 = "d1d89d5ca37d6c65c58e0362766173805d0262f9c9a5e02d790bf6ef03a421fc"
PUBLISHED_OFFICE_MIGRATION_SHA256 = {
    53: "b0beb171e033dfdb0edc9fd023a62c69c52d8bc2e9766a747eb303f4ebc9deaa",
    54: "70fbd52845c54312b491f84955ad98f5a32f38e34533d367429290005fc90a1a",
}
PARTNER_TABLES = {
    "partner_organizations",
    "partner_operators",
    "partner_provider_identities",
    "partner_identity_binding_requests",
    "partner_agent_grants",
    "partner_login_attempts",
}
OWNER_FUNCTION_MIGRATIONS = {
    "create_partner_organization_v54": MIGRATION,
    "create_partner_operator_v54": MIGRATION,
    "set_partner_organization_status_v54": MIGRATION,
    "set_partner_operator_status_v54": MIGRATION,
    "grant_partner_fae_v54": MIGRATION,
    "revoke_partner_fae_v54": MIGRATION,
    "link_partner_binding_request_v54": MIGRATION,
    "reject_partner_binding_request_v54": MANAGEMENT_REJECTION_MIGRATION,
}


def test_v54_bytes_remain_task2_immutable() -> None:
    assert hashlib.sha256(MIGRATION.read_bytes()).hexdigest() == TASK2_V54_SHA256


@pytest.mark.postgres
def test_published_office_migrations_upgrade_additively_to_partner_launch(
    tmp_path,
) -> None:
    from app.control_plane.migrate import migrate_control_database

    database_name = "agent_platform_control"
    owner_role = "platform_control_owner"
    migrator_role = "platform_control_migrator"
    data = tmp_path / "data"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = int(listener.getsockname()[1])
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
            str(tmp_path / "postgres.log"),
            "-o",
            f"-F -h 127.0.0.1 -p {port} -k /tmp",
            "start",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    admin_url = f"postgresql://control_test_admin@127.0.0.1:{port}/postgres"
    database_admin_url = (
        f"postgresql://control_test_admin@127.0.0.1:{port}/{database_name}"
    )
    migrator_url = (
        f"postgresql://{migrator_role}:{ROLE_PASSWORDS[migrator_role]}@"
        f"127.0.0.1:{port}/{database_name}"
    )
    through_v54 = tmp_path / "through-v54"
    through_v54.mkdir()
    for migration in MIGRATIONS.glob("*.sql"):
        if int(migration.name.split("_", 1)[0]) <= 54:
            shutil.copy2(migration, through_v54 / migration.name)

    try:
        with psycopg.connect(admin_url, autocommit=True) as connection:
            for role in OWNER_ROLES:
                connection.execute(
                    psycopg.sql.SQL(
                        "create role {} nologin nosuperuser nocreatedb "
                        "nocreaterole noreplication nobypassrls noinherit"
                    ).format(psycopg.sql.Identifier(role))
                )
            for role in ROLES:
                inheritance = "noinherit" if "migrator" in role else "inherit"
                connection.execute(
                    psycopg.sql.SQL(
                        "create role {} login password {} nosuperuser "
                        "nocreatedb nocreaterole noreplication nobypassrls "
                        + inheritance
                    ).format(
                        psycopg.sql.Identifier(role),
                        psycopg.sql.Literal(ROLE_PASSWORDS[role]),
                    )
                )
            connection.execute(
                psycopg.sql.SQL(
                    "create database {} owner {} template template0"
                ).format(
                    psycopg.sql.Identifier(database_name),
                    psycopg.sql.Identifier(owner_role),
                )
            )
            connection.execute(
                psycopg.sql.SQL("revoke connect on database {} from public").format(
                    psycopg.sql.Identifier(database_name)
                )
            )
            connection.execute(
                psycopg.sql.SQL("grant connect on database {} to {}").format(
                    psycopg.sql.Identifier(database_name),
                    psycopg.sql.SQL(", ").join(
                        psycopg.sql.Identifier(role) for role in PRODUCTION_ROLES
                    ),
                )
            )
            connection.execute(
                psycopg.sql.SQL("grant {} to {}").format(
                    psycopg.sql.Identifier(owner_role),
                    psycopg.sql.Identifier(migrator_role),
                )
            )

        migrate_control_database(migrator_url, through_v54, owner_role=owner_role)
        historical_user_id = uuid.uuid4()
        historical_session_id = uuid.uuid4()
        pending_binding_id = uuid.uuid4()
        consumed_binding_id = uuid.uuid4()
        with psycopg.connect(database_admin_url) as connection:
            connection.execute(
                "insert into platform_control.internal_users "
                "(internal_user_id,display_name,status) values "
                "(%s,'Historical Launch User','active')",
                (historical_user_id,),
            )
            connection.execute(
                "insert into platform_control.web_sessions "
                "(session_id,internal_user_id,token_hash,token_hash_key_version,"
                "csrf_hash,csrf_hash_key_version,idle_expires_at,absolute_expires_at) "
                "values (%s,%s,%s,1,%s,1,now()+interval '1 hour',"
                "now()+interval '2 hours')",
                (
                    historical_session_id,
                    historical_user_id,
                    b"t" * 32,
                    b"c" * 32,
                ),
            )
            connection.execute(
                "insert into platform_control.agent_launch_codes "
                "(launch_code_id,code_hash,code_key_version,source_session_id,"
                "internal_user_id,agent_id,identity_binding_id,expires_at,"
                "consumed_at) values "
                "(%s,%s,1,%s,%s,'ai-fae-agent',%s,now()+interval '1 hour',null),"
                "(%s,%s,1,%s,%s,'ai-fae-agent',%s,now()+interval '1 hour',now())",
                (
                    uuid.uuid4(),
                    b"p" * 32,
                    historical_session_id,
                    historical_user_id,
                    pending_binding_id,
                    uuid.uuid4(),
                    b"x" * 32,
                    historical_session_id,
                    historical_user_id,
                    consumed_binding_id,
                ),
            )
            connection.execute(
                "insert into platform_control.agent_identity_bindings "
                "(identity_binding_id,source_session_id,internal_user_id,agent_id) "
                "values (%s,%s,%s,'ai-fae-agent')",
                (
                    consumed_binding_id,
                    historical_session_id,
                    historical_user_id,
                ),
            )
            before = connection.execute(
                "select version,sha256 from platform_control.schema_migrations "
                "where version in (53,54) order by version"
            ).fetchall()
            assert before == list(PUBLISHED_OFFICE_MIGRATION_SHA256.items())
            assert connection.execute(
                "select has_function_privilege('platform_control_app',"
                "'platform_control.issue_agent_launch_v52(uuid,bytea,integer,"
                "uuid,uuid,text,uuid,integer)','execute')"
            ).fetchone() == (True,)

        migrate_control_database(migrator_url, MIGRATIONS, owner_role=owner_role)

        with psycopg.connect(database_admin_url) as connection:
            rows = connection.execute(
                "select version,sha256 from platform_control.schema_migrations "
                "where version in (55,56,57,58,59) order by version"
            ).fetchall()
            assert rows == [
                (
                    55,
                    hashlib.sha256(
                        (MIGRATIONS / "055_agent_access_subjects.sql").read_bytes()
                    ).hexdigest(),
                ),
                (56, TASK2_V54_SHA256),
                (
                    57,
                    hashlib.sha256(
                        MANAGEMENT_REJECTION_MIGRATION.read_bytes()
                    ).hexdigest(),
                ),
                (
                    58,
                    hashlib.sha256(
                        PARTNER_AUTHENTICATION_MIGRATION.read_bytes()
                    ).hexdigest(),
                ),
                (
                    59,
                    hashlib.sha256(
                        GENERIC_AGENT_LAUNCH_MIGRATION.read_bytes()
                    ).hexdigest(),
                ),
            ]
            assert connection.execute(
                "select to_regprocedure("
                "'platform_control.reject_partner_binding_request_v54("
                "uuid,uuid,text,uuid,uuid)') is not null"
            ).fetchone() == (True,)
            assert connection.execute(
                "select subject_id,subject_type::text,internal_user_id "
                "from platform_control.agent_launch_codes "
                "where internal_user_id=%s order by consumed_at nulls first",
                (historical_user_id,),
            ).fetchall() == [
                (historical_user_id, "enterprise_member", historical_user_id),
                (historical_user_id, "enterprise_member", historical_user_id),
            ]
            assert connection.execute(
                "select subject_id,subject_type::text,internal_user_id "
                "from platform_control.agent_identity_bindings "
                "where identity_binding_id=%s",
                (consumed_binding_id,),
            ).fetchone() == (
                historical_user_id,
                "enterprise_member",
                historical_user_id,
            )
            assert connection.execute(
                "select has_function_privilege('platform_control_app',"
                "'platform_control.issue_agent_launch_v52(uuid,bytea,integer,"
                "uuid,uuid,text,uuid,integer)','execute'),"
                "has_function_privilege('platform_control_app',"
                "'platform_control.issue_agent_launch_v57(uuid,bytea,integer,"
                "uuid,platform_control.agent_subject_type,uuid,uuid,text,uuid,"
                "integer)','execute')"
            ).fetchone() == (False, True)
            assert connection.execute(
                "select to_regprocedure("
                "'platform_control.consume_partner_login_attempt_v56("
                "text,bytea,integer)') is not null"
            ).fetchone() == (True,)
    finally:
        subprocess.run(
            ["pg_ctl", "-D", str(data), "stop", "-m", "immediate"],
            check=False,
            capture_output=True,
            text=True,
        )


def test_partner_schema_has_exact_tables_and_protected_identity_columns() -> None:
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    normalized = " ".join(sql.split())

    assert {
        name
        for name in PARTNER_TABLES
        if f"create table platform_control.{name}" in normalized
    } == PARTNER_TABLES
    assert "provider_subject_lookup_hmac bytea" in normalized
    assert "lookup_key_version integer" in normalized
    assert "provider_subject_ciphertext bytea" in normalized
    assert "encryption_key_version integer" in normalized
    assert (
        "unique ( provider_kind, provider_subject_lookup_hmac, lookup_key_version )"
        in normalized
    )
    assert "provider_subject text" not in normalized
    assert "on delete restrict" in normalized
    assert "check(agent_id='ai-fae-agent')" in normalized.replace(" ", "")


def test_pending_binding_and_owner_function_boundaries_are_explicit() -> None:
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    normalized = " ".join(sql.split())

    assert "statusin('pending','linked','rejected','expired')" in normalized.replace(
        " ", ""
    )
    assert "interval '24 hours'" in normalized
    assert "wherestatus='pending'" in normalized.replace(" ", "")
    assert "pending binding transition invalid" in normalized
    for function_name, migration in OWNER_FUNCTION_MIGRATIONS.items():
        migration_sql = " ".join(migration.read_text(encoding="utf-8").lower().split())
        body = migration_sql.split(
            f"create function platform_control.{function_name}", 1
        )[1].split("$function$;", 1)[0]
        assert "security definer" in body
        assert "session_user" in body or "require_partner_owner_v54" in body
        assert "for update" in body or "require_partner_owner_v54" in body
        assert "append_partner_audit_v54" in body
    assert (
        "create function platform_control.record_partner_binding_request_v54"
        in normalized
    )
    assert (
        "insert into platform_control.agent_access_subjects"
        not in normalized.split(
            "create function platform_control.record_partner_binding_request_v54", 1
        )[1].split("$function$;", 1)[0]
    )


@pytest.mark.postgres
def test_v54_has_exact_tables_constraints_and_no_runtime_table_writes(
    control_database,
) -> None:
    privileges = ("INSERT", "UPDATE", "DELETE")
    grantees = ("public", *ROLES)

    for environment in control_database["environments"].values():
        with psycopg.connect(environment["admin"]) as connection:
            rows = connection.execute(
                "select table_name from information_schema.tables "
                "where table_schema='platform_control' "
                "and table_name like 'partner_%' order by table_name"
            ).fetchall()
            assert {name for (name,) in rows} == PARTNER_TABLES

            privilege_rows = connection.execute(
                "select role_name,table_name,privilege_name,"
                "has_table_privilege(role_name,'platform_control.' || table_name,"
                "privilege_name) "
                "from unnest(%s::text[]) role_rows(role_name) "
                "cross join unnest(%s::text[]) table_rows(table_name) "
                "cross join unnest(%s::text[]) privilege_rows(privilege_name)",
                (list(grantees), sorted(PARTNER_TABLES), list(privileges)),
            ).fetchall()
            assert all(not allowed for *_labels, allowed in privilege_rows)

            with pytest.raises(psycopg.errors.CheckViolation):
                connection.execute(
                    "insert into platform_control.partner_agent_grants "
                    "(grant_id,subject_id,agent_id,created_by_internal_user_id) "
                    "values (%s,%s,'another-agent',%s)",
                    (uuid.uuid4(), uuid.uuid4(), uuid.uuid4()),
                )


@pytest.mark.postgres
def test_v54_partner_operator_rejects_enterprise_subjects(control_database) -> None:
    for environment in control_database["environments"].values():
        with psycopg.connect(environment["admin"], autocommit=True) as connection:
            internal_user_id = uuid.uuid4()
            organization_id = uuid.uuid4()
            connection.execute(
                "insert into platform_control.internal_users "
                "(internal_user_id,display_name,status) values (%s,'User','active')",
                (internal_user_id,),
            )
            connection.execute(
                "insert into platform_control.agent_access_subjects "
                "(subject_id,subject_type,status) "
                "values (%s,'enterprise_member','active')",
                (internal_user_id,),
            )
            connection.execute(
                "insert into platform_control.enterprise_subject_links "
                "(subject_id,internal_user_id) values (%s,%s)",
                (internal_user_id, internal_user_id),
            )
            connection.execute(
                "insert into platform_control.partner_organizations "
                "(partner_organization_id,status,name_ciphertext,name_key_version) "
                "values (%s,'active',%s,1)",
                (organization_id, b"o" * 32),
            )
            with pytest.raises(
                psycopg.errors.CheckViolation,
                match="Partner operator subject type required",
            ):
                connection.execute(
                    "insert into platform_control.partner_operators "
                    "(partner_operator_id,subject_id,partner_organization_id,status) "
                    "values (%s,%s,%s,'active')",
                    (uuid.uuid4(), internal_user_id, organization_id),
                )

            partner_subject_id = uuid.uuid4()
            connection.execute(
                "insert into platform_control.agent_access_subjects "
                "(subject_id,subject_type,status,display_name_ciphertext,"
                "display_name_key_version) "
                "values (%s,'partner_operator','active',%s,1)",
                (partner_subject_id, b"d" * 32),
            )
            connection.execute(
                "insert into platform_control.partner_operators "
                "(partner_operator_id,subject_id,partner_organization_id,status) "
                "values (%s,%s,%s,'active')",
                (uuid.uuid4(), partner_subject_id, organization_id),
            )
            with pytest.raises(
                psycopg.errors.CheckViolation,
                match="Partner operator subject type required",
            ):
                connection.execute(
                    "update platform_control.agent_access_subjects set "
                    "subject_type='enterprise_member',display_name_ciphertext=null,"
                    "display_name_key_version=null where subject_id=%s",
                    (partner_subject_id,),
                )


def _seed_owner(connection) -> uuid.UUID:
    existing = connection.execute(
        "select internal_user_id from platform_control.internal_users "
        "where role='platform_owner' and status='active'"
    ).fetchone()
    if existing is not None:
        return existing[0]
    owner_id = uuid.uuid4()
    connection.execute(
        "insert into platform_control.internal_users "
        "(internal_user_id,role,display_name,status) "
        "values (%s,'platform_owner','Owner','active')",
        (owner_id,),
    )
    return owner_id


@pytest.mark.postgres
def test_v54_owner_mutation_appends_audit_atomically_and_checks_session_user(
    control_database,
) -> None:
    for environment in control_database["environments"].values():
        app_url = environment["urls"][environment["roles"][1]]
        organization_id = uuid.uuid4()
        failed_organization_id = uuid.uuid4()
        request_id = uuid.uuid4()
        with psycopg.connect(environment["admin"], autocommit=True) as admin:
            owner_id = _seed_owner(admin)

        with psycopg.connect(app_url) as app:
            created = app.execute(
                "select platform_control.create_partner_organization_v54("
                "%s,%s,%s,%s,%s,%s,%s) as partner_organization_id",
                (
                    organization_id,
                    owner_id,
                    b"o" * 32,
                    1,
                    "pilot",
                    request_id,
                    uuid.uuid4(),
                ),
            ).fetchone()
            assert created == (organization_id,)

        with psycopg.connect(environment["admin"], autocommit=True) as admin:
            audit = admin.execute(
                "select actor_internal_user_id,event_type,target_internal_id,"
                "request_id,result,reason_code,sanitized_before_after "
                "from platform_control.audit_events where request_id=%s",
                (request_id,),
            ).fetchone()
            assert audit[:6] == (
                owner_id,
                "partner_organization_created",
                str(organization_id),
                request_id,
                "completed",
                "pilot",
            )
            assert set(audit[6]) == {
                "operation_id",
                "partner_organization_id",
                "status",
            }

            admin.execute(
                "create function platform_control.fail_partner_audit_test() "
                "returns trigger language plpgsql as $$ begin "
                "raise check_violation using message='forced partner audit failure'; "
                "end $$"
            )
            admin.execute(
                "create trigger fail_partner_audit_test before insert "
                "on platform_control.audit_events for each row execute function "
                "platform_control.fail_partner_audit_test()"
            )
        try:
            with (
                psycopg.connect(app_url) as app,
                pytest.raises(psycopg.Error, match="required_audit_unavailable"),
            ):
                app.execute(
                    "select platform_control.create_partner_organization_v54("
                    "%s,%s,%s,%s,%s,%s,%s)",
                    (
                        failed_organization_id,
                        owner_id,
                        b"o" * 32,
                        1,
                        "must rollback",
                        uuid.uuid4(),
                        uuid.uuid4(),
                    ),
                )
        finally:
            with psycopg.connect(environment["admin"], autocommit=True) as admin:
                admin.execute(
                    "drop trigger fail_partner_audit_test "
                    "on platform_control.audit_events"
                )
                admin.execute(
                    "drop function platform_control.fail_partner_audit_test()"
                )

        with psycopg.connect(environment["admin"], autocommit=True) as admin:
            assert admin.execute(
                "select count(*) from platform_control.partner_organizations "
                "where partner_organization_id=%s",
                (failed_organization_id,),
            ).fetchone() == (0,)
            with pytest.raises(
                psycopg.errors.InsufficientPrivilege,
                match="partner owner mutation caller invalid",
            ):
                admin.execute(
                    "select platform_control.set_partner_organization_status_v54("
                    "%s,%s,'suspended','admin bypass',%s,%s)",
                    (owner_id, organization_id, uuid.uuid4(), uuid.uuid4()),
                )


@pytest.mark.postgres
def test_v54_unknown_identity_only_becomes_linkable_pending_request(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    app_url = environment["urls"][environment["roles"][1]]
    organization_id = uuid.uuid4()
    operator_id = uuid.uuid4()
    subject_id = uuid.uuid4()
    binding_request_id = uuid.uuid4()
    with psycopg.connect(environment["admin"], autocommit=True) as admin:
        owner_id = _seed_owner(admin)
        before = admin.execute(
            "select (select count(*) from platform_control.agent_access_subjects),"
            "(select count(*) from platform_control.partner_operators),"
            "(select count(*) from platform_control.partner_agent_grants),"
            "(select count(*) from platform_control.agent_identity_bindings),"
            "(select count(*) from platform_control.agent_launch_codes)"
        ).fetchone()

    with psycopg.connect(app_url) as app:
        recorded = app.execute(
            "select * from platform_control.record_partner_binding_request_v54("
            "%s,'qianniu',%s,1,%s,%s,%s,1,%s,1,%s)",
            (
                binding_request_id,
                b"h" * 32,
                [1],
                [b"h" * 32],
                b"sealed-provider-subject-with-aead-tag",
                b"sealed-display-name-with-aead-tag",
                datetime_value := "2026-08-29T08:00:00+00:00",
            ),
        ).fetchone()
        repeated = app.execute(
            "select * from platform_control.record_partner_binding_request_v54("
            "%s,'qianniu',%s,1,%s,%s,%s,1,null,null,%s)",
            (
                uuid.uuid4(),
                b"h" * 32,
                [1],
                [b"h" * 32],
                b"different-randomized-ciphertext-with-tag",
                datetime_value,
            ),
        ).fetchone()
        assert recorded[0] == repeated[0] == binding_request_id
        assert recorded[1] == repeated[1] == "pending"

    with psycopg.connect(environment["admin"], autocommit=True) as admin:
        after = admin.execute(
            "select (select count(*) from platform_control.agent_access_subjects),"
            "(select count(*) from platform_control.partner_operators),"
            "(select count(*) from platform_control.partner_agent_grants),"
            "(select count(*) from platform_control.agent_identity_bindings),"
            "(select count(*) from platform_control.agent_launch_codes)"
        ).fetchone()
        assert after == before

    with psycopg.connect(app_url) as app:
        app.execute(
            "select platform_control.create_partner_organization_v54("
            "%s,%s,%s,1,'pilot',%s,%s)",
            (
                organization_id,
                owner_id,
                b"o" * 32,
                uuid.uuid4(),
                uuid.uuid4(),
            ),
        )
        app.execute(
            "select platform_control.create_partner_operator_v54("
            "%s,%s,%s,%s,%s,1,'roster','active',%s,%s)",
            (
                operator_id,
                subject_id,
                organization_id,
                owner_id,
                b"d" * 32,
                uuid.uuid4(),
                uuid.uuid4(),
            ),
        )
        linked = app.execute(
            "select * from platform_control.link_partner_binding_request_v54("
            "%s,%s,%s,%s,'roster verified',%s,%s)",
            (
                uuid.uuid4(),
                owner_id,
                binding_request_id,
                operator_id,
                uuid.uuid4(),
                uuid.uuid4(),
            ),
        ).fetchone()
        assert linked[:2] == (subject_id, operator_id)

    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select status,linked_partner_operator_id,resolved_at is not null "
            "from platform_control.partner_identity_binding_requests "
            "where binding_request_id=%s",
            (binding_request_id,),
        ).fetchone() == ("linked", operator_id, True)
        assert admin.execute(
            "select count(*) from platform_control.partner_provider_identities "
            "where partner_operator_id=%s and revoked_at is null",
            (operator_id,),
        ).fetchone() == (1,)
        assert admin.execute(
            "select count(*) from platform_control.partner_agent_grants "
            "where subject_id=%s and revoked_at is null",
            (subject_id,),
        ).fetchone() == (0,)


@pytest.mark.postgres
def test_v55_owner_rejection_is_atomic_audited_and_app_only(control_database) -> None:
    environment = control_database["environments"]["production"]
    app_url = environment["urls"][environment["roles"][1]]
    rejected_request_id = uuid.uuid4()
    rollback_request_id = uuid.uuid4()
    operation_id = uuid.uuid4()
    with psycopg.connect(environment["admin"], autocommit=True) as admin:
        owner_id = _seed_owner(admin)
    with psycopg.connect(app_url) as app:
        for request_id, lookup in (
            (rejected_request_id, b"r" * 32),
            (rollback_request_id, b"b" * 32),
        ):
            app.execute(
                "select * from platform_control.record_partner_binding_request_v54("
                "%s,'partner-sso',%s,1,array[1],array[%s::bytea],%s,1,null,null,now())",
                (request_id, lookup, lookup, b"sealed-provider-subject-with-aead-tag"),
            )
        result = app.execute(
            "select * from platform_control.reject_partner_binding_request_v54("
            "%s,%s,'not on roster',%s,%s)",
            (owner_id, rejected_request_id, operation_id, uuid.uuid4()),
        ).fetchone()
        assert result[0:2] == (rejected_request_id, "rejected")

    with psycopg.connect(environment["admin"], autocommit=True) as admin:
        row = admin.execute(
            "select request.status,request.resolved_at is not null,audit.event_type,"
            "audit.reason_code,audit.sanitized_before_after "
            "from platform_control.partner_identity_binding_requests request "
            "join platform_control.audit_events audit "
            "on audit.request_id=%s where request.binding_request_id=%s",
            (operation_id, rejected_request_id),
        ).fetchone()
        assert row[:4] == (
            "rejected",
            True,
            "partner_identity_rejected",
            "not on roster",
        )
        assert set(row[4]) == {
            "binding_request_id",
            "operation_id",
            "provider_kind",
            "status",
        }
        with pytest.raises(
            psycopg.errors.InsufficientPrivilege,
            match="partner owner mutation caller invalid",
        ):
            admin.execute(
                "select * from platform_control.reject_partner_binding_request_v54("
                "%s,%s,'admin bypass',%s,%s)",
                (owner_id, rollback_request_id, uuid.uuid4(), uuid.uuid4()),
            )
        admin.execute(
            "create function platform_control.fail_partner_rejection_audit_test() "
            "returns trigger language plpgsql as $$ begin "
            "raise check_violation using message='forced partner audit failure'; "
            "end $$"
        )
        admin.execute(
            "create trigger fail_partner_rejection_audit_test before insert "
            "on platform_control.audit_events for each row execute function "
            "platform_control.fail_partner_rejection_audit_test()"
        )
    try:
        with (
            psycopg.connect(app_url) as app,
            pytest.raises(psycopg.Error, match="required_audit_unavailable"),
        ):
            app.execute(
                "select * from platform_control.reject_partner_binding_request_v54("
                "%s,%s,'must rollback',%s,%s)",
                (owner_id, rollback_request_id, uuid.uuid4(), uuid.uuid4()),
            )
    finally:
        with psycopg.connect(environment["admin"], autocommit=True) as admin:
            admin.execute(
                "drop trigger fail_partner_rejection_audit_test "
                "on platform_control.audit_events"
            )
            admin.execute(
                "drop function platform_control.fail_partner_rejection_audit_test()"
            )
    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select status,resolved_at from "
            "platform_control.partner_identity_binding_requests "
            "where binding_request_id=%s",
            (rollback_request_id,),
        ).fetchone() == ("pending", None)
    # Resolve the deliberately rolled-back request through the same audited,
    # app-only boundary so this module-scoped database remains eligible for
    # the transition-policy tests below.
    with psycopg.connect(app_url) as app:
        app.execute(
            "select * from platform_control.reject_partner_binding_request_v54("
            "%s,%s,'test cleanup after rollback proof',%s,%s)",
            (owner_id, rollback_request_id, uuid.uuid4(), uuid.uuid4()),
        )


@pytest.mark.postgres
def test_v54_real_repository_service_enforces_pending_and_access_layers(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    app_url = environment["urls"][environment["roles"][1]]
    with psycopg.connect(environment["admin"], autocommit=True) as admin:
        owner_id = _seed_owner(admin)
    identity_codec = PartnerProviderIdentityCodec(
        IdentityKeyring(1, "partner-provider-encryption", {1: b"e" * 32}),
        IdentityKeyring(
            1,
            "partner-provider-lookup-hmac",
            {1: b"h" * 32},
            transition_versions=(1,),
        ),
    )
    service = PartnerService(
        PartnerRepository(app_url, identity_codec=identity_codec),
        identity_codec=identity_codec,
        content_codec=ContentCodec(
            IdentityKeyring(1, "platform-content-encryption", {1: b"c" * 32})
        ),
    )
    organization = service.create_organization(
        actor_id=owner_id,
        display_name="Partner A",
        reason="pilot",
        request_id=uuid.uuid4(),
    )
    operator = service.create_operator(
        actor_id=owner_id,
        partner_organization_id=organization.partner_organization_id,
        display_name="Seat A",
        reason="pilot",
        request_id=uuid.uuid4(),
    )

    pending = service.resolve_verified_identity(
        VerifiedProviderSubject(
            provider_kind="partner-sso",
            provider_subject="synthetic-seat-a",
            verified_at=datetime.now(timezone.utc),
        )
    )
    assert pending.status == "pending"
    assert pending.subject_id is None
    linked = service.link_binding_request(
        actor_id=owner_id,
        binding_request_id=pending.binding_request_id,
        operator_id=operator.partner_operator_id,
        reason="roster verified",
        request_id=uuid.uuid4(),
    )
    assert linked.subject_id == operator.subject_id
    resolved = service.resolve_verified_identity(
        VerifiedProviderSubject(
            provider_kind="partner-sso",
            provider_subject="synthetic-seat-a",
            verified_at=datetime.now(timezone.utc),
        )
    )
    assert resolved.subject_id == operator.subject_id
    assert resolved.partner_operator_id == operator.partner_operator_id
    assert service.decide_fae_access(operator.subject_id).reason == "fae_access_denied"

    service.grant_fae(
        actor_id=owner_id,
        operator_id=operator.partner_operator_id,
        reason="pilot",
        request_id=uuid.uuid4(),
    )
    assert service.decide_fae_access(operator.subject_id).allowed is True
    service.set_operator_status(
        actor_id=owner_id,
        operator_id=operator.partner_operator_id,
        status="suspended",
        reason="contract ended",
        request_id=uuid.uuid4(),
    )
    assert service.decide_fae_access(operator.subject_id).reason == "operator_inactive"


@pytest.mark.postgres
def test_v54_transition_nodes_share_one_pending_identity_request(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    app_url = environment["urls"][environment["roles"][1]]
    maintenance_url = environment["urls"][environment["roles"][5]]
    with psycopg.connect(maintenance_url) as maintenance:
        maintenance.execute(
            "select platform_control.set_provider_identity_key_policy("
            "'partner',array[1,2])"
        )
    encryption_keys = {1: b"e" * 32, 2: b"E" * 32}
    lookup_keys = {1: b"h" * 32, 2: b"H" * 32}

    def service(active_version: int) -> PartnerService:
        codec = PartnerProviderIdentityCodec(
            IdentityKeyring(
                active_version,
                "partner-provider-encryption",
                encryption_keys,
            ),
            IdentityKeyring(
                active_version,
                "partner-provider-lookup-hmac",
                lookup_keys,
                transition_versions=(1, 2),
            ),
        )
        return PartnerService(
            PartnerRepository(app_url, identity_codec=codec),
            identity_codec=codec,
            content_codec=ContentCodec(
                IdentityKeyring(1, "platform-content-encryption", {1: b"c" * 32})
            ),
        )

    verified = VerifiedProviderSubject(
        provider_kind="partner-sso",
        provider_subject="one-seat-during-rollout",
        verified_at=datetime.now(timezone.utc),
    )
    old_node = service(1).resolve_verified_identity(verified)
    new_node = service(2).resolve_verified_identity(verified)

    assert new_node.binding_request_id == old_node.binding_request_id
    with psycopg.connect(environment["admin"]) as connection:
        assert connection.execute(
            "select count(*) from "
            "platform_control.partner_identity_binding_requests "
            "where status='pending' and provider_kind='partner-sso'"
        ).fetchone() == (1,)
        assert connection.execute(
            "select lookup_transition_versions from "
            "platform_control.provider_identity_key_policies "
            "where provider='partner'"
        ).fetchone() == ([1, 2],)


@pytest.mark.postgres
def test_v54_transition_policy_mismatch_fails_closed_before_identity_write(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    app_url = environment["urls"][environment["roles"][1]]
    maintenance_url = environment["urls"][environment["roles"][5]]
    with psycopg.connect(maintenance_url) as maintenance:
        maintenance.execute(
            "select platform_control.set_provider_identity_key_policy("
            "'partner',array[1,2])"
        )
    with psycopg.connect(environment["admin"]) as admin:
        before = admin.execute(
            "select count(*) from platform_control.partner_identity_binding_requests"
        ).fetchone()
    codec = PartnerProviderIdentityCodec(
        IdentityKeyring(2, "partner-provider-encryption", {2: b"E" * 32}),
        IdentityKeyring(
            2,
            "partner-provider-lookup-hmac",
            {2: b"H" * 32},
            transition_versions=(2,),
        ),
    )
    service = PartnerService(
        PartnerRepository(app_url, identity_codec=codec),
        identity_codec=codec,
        content_codec=ContentCodec(
            IdentityKeyring(1, "platform-content-encryption", {1: b"c" * 32})
        ),
    )

    with pytest.raises(PartnerIdentityError, match="^partner_identity_unavailable$"):
        service.resolve_verified_identity(
            VerifiedProviderSubject(
                provider_kind="partner-policy-mismatch",
                provider_subject="must-not-be-recorded",
                verified_at=datetime.now(timezone.utc),
            )
        )

    with psycopg.connect(environment["admin"]) as admin:
        assert (
            admin.execute(
                "select count(*) from "
                "platform_control.partner_identity_binding_requests"
            ).fetchone()
            == before
        )


@pytest.mark.postgres
def test_v54_link_rejects_conflict_across_all_transition_candidates(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    app_url = environment["urls"][environment["roles"][1]]
    maintenance_url = environment["urls"][environment["roles"][5]]
    organization_id = uuid.uuid4()
    operator_a_id = uuid.uuid4()
    operator_b_id = uuid.uuid4()
    subject_a_id = uuid.uuid4()
    subject_b_id = uuid.uuid4()
    request_a_id = uuid.uuid4()
    request_b_id = uuid.uuid4()
    lookup_v1 = b"1" * 32
    lookup_v2 = b"2" * 32
    with psycopg.connect(maintenance_url) as maintenance:
        maintenance.execute(
            "select platform_control.set_provider_identity_key_policy("
            "'partner',array[1,2])"
        )
    with psycopg.connect(environment["admin"], autocommit=True) as admin:
        owner_id = _seed_owner(admin)
        for request_id, active_lookup, active_version in (
            (request_a_id, lookup_v1, 1),
            (request_b_id, lookup_v2, 2),
        ):
            admin.execute(
                "insert into platform_control.partner_identity_binding_requests("
                "binding_request_id,provider_kind,"
                "provider_subject_lookup_hmac,lookup_key_version,"
                "lookup_transition_versions,"
                "provider_subject_lookup_hmac_candidates,"
                "provider_subject_ciphertext,encryption_key_version,verified_at) "
                "values (%s,'partner-conflict-sso',%s,%s,%s,%s,%s,1,now())",
                (
                    request_id,
                    active_lookup,
                    active_version,
                    [1, 2],
                    [lookup_v1, lookup_v2],
                    b"sealed-provider-subject-with-aead-tag",
                ),
            )
    with psycopg.connect(app_url) as app:
        app.execute(
            "select platform_control.create_partner_organization_v54("
            "%s,%s,%s,1,'pilot',%s,%s)",
            (
                organization_id,
                owner_id,
                b"o" * 32,
                uuid.uuid4(),
                uuid.uuid4(),
            ),
        )
        for operator_id, subject_id in (
            (operator_a_id, subject_a_id),
            (operator_b_id, subject_b_id),
        ):
            app.execute(
                "select platform_control.create_partner_operator_v54("
                "%s,%s,%s,%s,%s,1,'roster','active',%s,%s)",
                (
                    operator_id,
                    subject_id,
                    organization_id,
                    owner_id,
                    b"d" * 32,
                    uuid.uuid4(),
                    uuid.uuid4(),
                ),
            )
    with psycopg.connect(app_url) as app:
        app.execute(
            "select platform_control.link_partner_binding_request_v54("
            "%s,%s,%s,%s,'roster verified',%s,%s)",
            (
                uuid.uuid4(),
                owner_id,
                request_a_id,
                operator_a_id,
                uuid.uuid4(),
                uuid.uuid4(),
            ),
        )
    with (
        psycopg.connect(maintenance_url) as maintenance,
        pytest.raises(
            psycopg.errors.CheckViolation,
            match="partner identity key policy rollover unsafe",
        ),
    ):
        maintenance.execute(
            "select platform_control.set_provider_identity_key_policy("
            "'partner',array[2,3])"
        )
    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select lookup_transition_versions from "
            "platform_control.provider_identity_key_policies "
            "where provider='partner'"
        ).fetchone() == ([1, 2],)
    with (
        psycopg.connect(app_url) as app,
        pytest.raises(
            psycopg.errors.UniqueViolation,
            match="partner_identity_already_linked",
        ),
    ):
        app.execute(
            "select * from platform_control.record_partner_binding_request_v54("
            "%s,'partner-conflict-sso',%s,2,%s,%s,%s,1,null,null,now())",
            (
                uuid.uuid4(),
                lookup_v2,
                [1, 2],
                [lookup_v1, lookup_v2],
                b"new-randomized-ciphertext-with-aead-tag",
            ),
        )
    with (
        psycopg.connect(app_url) as app,
        pytest.raises(
            psycopg.errors.UniqueViolation, match="partner_identity_conflict"
        ),
    ):
        app.execute(
            "select platform_control.link_partner_binding_request_v54("
            "%s,%s,%s,%s,'roster verified',%s,%s)",
            (
                uuid.uuid4(),
                owner_id,
                request_b_id,
                operator_b_id,
                uuid.uuid4(),
                uuid.uuid4(),
            ),
        )
    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select count(*) from platform_control.partner_provider_identities "
            "where provider_kind='partner-conflict-sso'"
        ).fetchone() == (1,)
        assert admin.execute(
            "select status from platform_control.partner_identity_binding_requests "
            "where binding_request_id=%s",
            (request_b_id,),
        ).fetchone() == ("pending",)
