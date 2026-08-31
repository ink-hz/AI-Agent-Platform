from __future__ import annotations

# Pytest fixture is imported into this module's namespace for discovery.
# ruff: noqa: F401,F811
import uuid

import psycopg
import pytest
from test_control_plane_migration import (
    MIGRATIONS,
    ROLES,
    control_database,
)

MIGRATION = MIGRATIONS / "053_agent_access_subjects.sql"


def test_generic_subject_schema_and_enterprise_backfill_are_explicit() -> None:
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    assert "create type platform_control.agent_subject_type" in sql
    assert "('enterprise_member','partner_operator')" in sql.replace(" ", "")
    assert "create table platform_control.agent_access_subjects" in sql
    assert "create table platform_control.enterprise_subject_links" in sql
    assert "subject_id=users.internal_user_id" in sql.replace(" ", "")
    assert "unique (internal_user_id)" in sql
    assert "revoke all on platform_control.agent_access_subjects from public" in sql


def test_partner_subject_cannot_claim_an_enterprise_internal_user() -> None:
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    assert "enterprise subject type required" in sql
    assert "partner subject cannot have enterprise link" in sql


@pytest.mark.postgres
def test_v53_enforces_subject_display_and_enterprise_link_shapes(
    control_database,
) -> None:
    for environment in control_database["environments"].values():
        with psycopg.connect(environment["admin"], autocommit=True) as connection:
            internal_user_id = uuid.uuid4()
            connection.execute(
                "insert into platform_control.internal_users "
                "(internal_user_id,display_name,status) values (%s,'Enterprise','active')",
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

            with pytest.raises(
                psycopg.errors.CheckViolation,
                match="Enterprise subject type required",
            ):
                connection.execute(
                    "update platform_control.agent_access_subjects "
                    "set subject_type='partner_operator' where subject_id=%s",
                    (internal_user_id,),
                )

            partner_subject_id = uuid.uuid4()
            with pytest.raises(
                psycopg.errors.CheckViolation,
                match="Partner subject display name required",
            ):
                connection.execute(
                    "insert into platform_control.agent_access_subjects "
                    "(subject_id,subject_type,status) "
                    "values (%s,'partner_operator','active')",
                    (partner_subject_id,),
                )

            connection.execute(
                "insert into platform_control.internal_users "
                "(internal_user_id,display_name,status) values (%s,'Collision','active')",
                (partner_subject_id,),
            )
            connection.execute(
                "insert into platform_control.agent_access_subjects "
                "(subject_id,subject_type,status,display_name_ciphertext,"
                "display_name_key_version) "
                "values (%s,'partner_operator','active',%s,1)",
                (partner_subject_id, b"sealed-display-name"),
            )
            with pytest.raises(
                psycopg.errors.CheckViolation,
                match="Partner subject cannot have enterprise link",
            ):
                connection.execute(
                    "insert into platform_control.enterprise_subject_links "
                    "(subject_id,internal_user_id) values (%s,%s)",
                    (partner_subject_id, partner_subject_id),
                )


@pytest.mark.postgres
def test_v53_rejects_enterprise_subject_with_encrypted_display_fields(
    control_database,
) -> None:
    for environment in control_database["environments"].values():
        with (
            psycopg.connect(environment["admin"], autocommit=True) as connection,
            pytest.raises(
                psycopg.errors.CheckViolation,
                match="Enterprise subject display name must be null",
            ),
        ):
            connection.execute(
                "insert into platform_control.agent_access_subjects "
                "(subject_id,subject_type,status,display_name_ciphertext,"
                "display_name_key_version) "
                "values (%s,'enterprise_member','active',%s,1)",
                (uuid.uuid4(), b"must-not-be-stored"),
            )


@pytest.mark.postgres
def test_v53_rejects_partner_to_enterprise_mutation_with_encrypted_display(
    control_database,
) -> None:
    for environment in control_database["environments"].values():
        with psycopg.connect(environment["admin"], autocommit=True) as connection:
            subject_id = uuid.uuid4()
            connection.execute(
                "insert into platform_control.agent_access_subjects "
                "(subject_id,subject_type,status,display_name_ciphertext,"
                "display_name_key_version) "
                "values (%s,'partner_operator','active',%s,1)",
                (subject_id, b"sealed-display-name"),
            )

            with pytest.raises(
                psycopg.errors.CheckViolation,
                match="Enterprise subject display name must be null",
            ):
                connection.execute(
                    "update platform_control.agent_access_subjects "
                    "set subject_type='enterprise_member' where subject_id=%s",
                    (subject_id,),
                )


@pytest.mark.postgres
def test_v53_subject_tables_deny_runtime_mutations(control_database) -> None:
    tables = ("agent_access_subjects", "enterprise_subject_links")
    privileges = ("INSERT", "UPDATE", "DELETE")
    grantees = ("public", *ROLES)

    for environment in control_database["environments"].values():
        with psycopg.connect(environment["admin"]) as connection:
            rows = connection.execute(
                "select role_name,table_name,privilege_name,"
                "has_table_privilege(role_name,'platform_control.' || table_name,"
                "privilege_name) "
                "from unnest(%s::text[]) role_rows(role_name) "
                "cross join unnest(%s::text[]) table_rows(table_name) "
                "cross join unnest(%s::text[]) privilege_rows(privilege_name)",
                (list(grantees), list(tables), list(privileges)),
            ).fetchall()

        assert len(rows) == len(grantees) * len(tables) * len(privileges)
        assert all(not allowed for *_labels, allowed in rows)


@pytest.mark.postgres
def test_v53_upgrade_backfills_enterprise_subjects_without_plaintext(
    control_database,
) -> None:
    from app.control_plane.migrate import migrate_control_database

    environment = control_database["environments"]["production"]
    owner_role = environment["owner"]
    migrator_role = environment["roles"][0]
    admin_url = control_database["cluster_admin"]
    database_admin_url = environment["admin"]
    migrator_url = environment["urls"][migrator_role]

    try:
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute(
                psycopg.sql.SQL("grant {} to {}").format(
                    psycopg.sql.Identifier(owner_role),
                    psycopg.sql.Identifier(migrator_role),
                )
            )

        internal_user_id = uuid.uuid4()
        with psycopg.connect(database_admin_url, autocommit=True) as connection:
            connection.execute(
                "drop table if exists "
                "platform_control.enterprise_subject_links cascade"
            )
            connection.execute(
                "drop table if exists platform_control.agent_access_subjects cascade"
            )
            connection.execute(
                "drop function if exists "
                "platform_control.guard_agent_access_subject_v53() cascade"
            )
            connection.execute(
                "drop function if exists "
                "platform_control.guard_enterprise_subject_link_v53() cascade"
            )
            connection.execute(
                "drop type if exists platform_control.agent_subject_type cascade"
            )
            connection.execute(
                "delete from platform_control.schema_migrations where version=53"
            )
            connection.execute(
                "insert into platform_control.internal_users "
                "(internal_user_id,display_name,status) "
                "values (%s,'Must stay plaintext only here','active')",
                (internal_user_id,),
            )

        migrate_control_database(migrator_url, MIGRATIONS, owner_role=owner_role)

        with psycopg.connect(database_admin_url) as connection:
            subject = connection.execute(
                "select subject_id,subject_type::text,status,"
                "display_name_ciphertext,display_name_key_version "
                "from platform_control.agent_access_subjects where subject_id=%s",
                (internal_user_id,),
            ).fetchone()
            assert subject == (
                internal_user_id,
                "enterprise_member",
                "active",
                None,
                None,
            )
            assert connection.execute(
                "select subject_id,internal_user_id "
                "from platform_control.enterprise_subject_links "
                "where internal_user_id=%s",
                (internal_user_id,),
            ).fetchone() == (internal_user_id, internal_user_id)
    finally:
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute(
                psycopg.sql.SQL("revoke {} from {}").format(
                    psycopg.sql.Identifier(owner_role),
                    psycopg.sql.Identifier(migrator_role),
                )
            )
