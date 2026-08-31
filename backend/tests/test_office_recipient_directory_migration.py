from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from test_control_plane_migration import control_database


MIGRATION = (
    Path(__file__).parents[1]
    / "control_migrations"
    / "053_office_recipient_directory.sql"
)


def test_v53_reader_is_security_definer_and_granted_only_to_app(control_database):
    del control_database
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    assert "read_office_recipient_directory_v53" in sql
    assert "security definer" in sql
    assert "set search_path = pg_catalog, platform_control" in sql
    assert "revoke all" in sql
    assert "grant execute" in sql
    assert "platform_control_app" in sql
    assert "platform_control_app_preview" in sql


def test_v53_reader_is_bounded_to_the_complete_active_generation():
    sql = " ".join(MIGRATION.read_text(encoding="utf-8").lower().split())

    assert "directory_state" in sql
    assert "active_generation_id" in sql
    assert "generation.status='complete'" in sql
    assert "selected_limit not between 1 and 200" in sql
    assert "member.member_key>selected_cursor" in sql
    assert "member.status='active'" in sql
    assert "department_closure" in sql
    assert "inactive" in sql
    assert "disabled" in sql
    assert "not_found" in sql


@pytest.mark.postgres
def test_v53_reader_has_exact_environment_grants(control_database):
    signature = (
        "platform_control.read_office_recipient_directory_v53("
        "text,text,uuid[],boolean,integer,uuid,uuid[],uuid[])"
    )
    for name, environment in control_database["environments"].items():
        app = environment["roles"][1]
        other_app = (
            "platform_control_app_preview"
            if name == "production"
            else "platform_control_app"
        )
        with psycopg.connect(environment["admin"]) as connection:
            privilege = connection.execute(
                "select has_function_privilege(%s,%s,'execute'),"
                "has_function_privilege(%s,%s,'execute'),"
                "has_function_privilege('public',%s,'execute'),"
                "prosecdef,proconfig from pg_proc where oid=%s::regprocedure",
                (app, signature, other_app, signature, signature, signature),
            ).fetchone()

        assert privilege == (
            True,
            False,
            False,
            True,
            ["search_path=pg_catalog, platform_control"],
        )


@pytest.mark.postgres
def test_v53_reader_searches_descendants_and_resolves_inactive_states(
    control_database,
):
    environment = control_database["environments"]["production"]
    generation_id = uuid4()
    internal_user_id = uuid4()
    root_department_id = uuid4()
    child_department_id = uuid4()
    active_member_id = uuid4()
    inactive_member_id = uuid4()
    disabled_member_id = uuid4()
    missing_member_id = uuid4()

    with psycopg.connect(environment["admin"], autocommit=True) as connection:
        connection.execute(
            "insert into platform_control.directory_generations "
            "(generation_id,status) values (%s,'complete')",
            (generation_id,),
        )
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id,display_name,status) values (%s,'Mapped','active')",
            (internal_user_id,),
        )
        for department_id, parent_id, name, discriminator in (
            (root_department_id, None, "Office", b"r"),
            (child_department_id, root_department_id, "AI Lab", b"c"),
        ):
            connection.execute(
                "insert into platform_control.directory_departments "
                "(generation_id,department_key,parent_department_key,"
                "lookup_hmac,lookup_key_version,encrypted_provider_id,"
                "encryption_key_version,display_name) values "
                "(%s,%s,%s,%s,1,%s,1,%s)",
                (
                    generation_id,
                    department_id,
                    parent_id,
                    discriminator * 32,
                    discriminator * 28,
                    name,
                ),
            )
        for member_id, mapped_user_id, name, status, discriminator in (
            (active_member_id, internal_user_id, "苍渊", "active", b"a"),
            (inactive_member_id, None, "Inactive", "inactive", b"i"),
            (disabled_member_id, None, "Disabled", "disabled", b"d"),
        ):
            connection.execute(
                "insert into platform_control.directory_members "
                "(generation_id,member_key,internal_user_id,subject_kind,"
                "lookup_hmac,lookup_key_version,encrypted_provider_id,"
                "encryption_key_version,display_name,status) values "
                "(%s,%s,%s,'employee',%s,1,%s,1,%s,%s)",
                (
                    generation_id,
                    member_id,
                    mapped_user_id,
                    discriminator * 32,
                    discriminator * 28,
                    name,
                    status,
                ),
            )
        connection.execute(
            "insert into platform_control.department_closure values "
            "(%s,%s,%s,0),(%s,%s,%s,1),(%s,%s,%s,0)",
            (
                generation_id,
                root_department_id,
                root_department_id,
                generation_id,
                root_department_id,
                child_department_id,
                generation_id,
                child_department_id,
                child_department_id,
            ),
        )
        connection.execute(
            "insert into platform_control.member_departments values (%s,%s,%s)",
            (generation_id, active_member_id, child_department_id),
        )
        connection.execute(
            "update platform_control.directory_state set active_generation_id=%s",
            (generation_id,),
        )

    with psycopg.connect(environment["urls"]["platform_control_app"]) as connection:
        search = connection.execute(
            "select * from platform_control.read_office_recipient_directory_v53("
            "'search','苍渊',%s::uuid[],true,20,null,%s::uuid[],%s::uuid[])",
            ([root_department_id], [], []),
        ).fetchall()
        resolved = connection.execute(
            "select * from platform_control.read_office_recipient_directory_v53("
            "'resolve','',%s::uuid[],false,20,null,%s::uuid[],%s::uuid[])",
            (
                [],
                [
                    active_member_id,
                    inactive_member_id,
                    disabled_member_id,
                    missing_member_id,
                ],
                [],
            ),
        ).fetchall()

    assert [(row[1], row[2], row[3], row[11]) for row in search] == [
        ("member", active_member_id, internal_user_id, ["AI Lab"])
    ]
    outcomes = {row[12]: (row[1], row[13]) for row in resolved}
    assert outcomes == {
        active_member_id: ("member", None),
        inactive_member_id: ("issue", "inactive"),
        disabled_member_id: ("issue", "disabled"),
        missing_member_id: ("issue", "not_found"),
    }
