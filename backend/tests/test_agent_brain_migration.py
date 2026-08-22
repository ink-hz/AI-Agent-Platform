from __future__ import annotations

from uuid import UUID, uuid4

import psycopg
import pytest

from test_control_plane_migration import ROLES, control_database


AGENT_PATTERN = "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
MISSION_TABLES = (
    "missions",
    "mission_messages",
    "mission_tasks",
    "mission_runs",
    "mission_events",
)


def _seed_active_directory(connection, *, user_status: str = "active"):
    generation_id = uuid4()
    user_id = uuid4()
    member_key = uuid4()
    root_department = uuid4()
    child_department = uuid4()
    connection.execute(
        "insert into platform_control.directory_generations "
        "(generation_id,status,member_count,department_count,content_sha256,"
        "completed_at) values (%s,'complete',1,2,%s,now())",
        (generation_id, "a" * 64),
    )
    connection.execute(
        "insert into platform_control.internal_users "
        "(internal_user_id,display_name,status) values (%s,'Grant User',%s)",
        (user_id, user_status),
    )
    connection.execute(
        "insert into platform_control.directory_members "
        "(generation_id,member_key,internal_user_id,subject_kind,lookup_hmac,"
        "lookup_key_version,encrypted_provider_id,encryption_key_version,"
        "display_name,status) values "
        "(%s,%s,%s,'employee',%s,1,%s,1,'Grant User',%s)",
        (
            generation_id,
            member_key,
            user_id,
            b"m" * 32,
            b"m" * 29,
            user_status,
        ),
    )
    for index, (department_id, parent_id) in enumerate(
        ((root_department, None), (child_department, root_department)), start=1
    ):
        connection.execute(
            "insert into platform_control.directory_departments "
            "(generation_id,department_key,parent_department_key,lookup_hmac,"
            "lookup_key_version,encrypted_provider_id,encryption_key_version,"
            "display_name) values (%s,%s,%s,%s,1,%s,1,%s)",
            (
                generation_id,
                department_id,
                parent_id,
                bytes([index]) * 32,
                bytes([index]) * 29,
                f"Department {index}",
            ),
        )
    connection.execute(
        "insert into platform_control.department_closure "
        "(generation_id,ancestor_department_key,descendant_department_key,depth) "
        "values (%s,%s,%s,0),(%s,%s,%s,0),(%s,%s,%s,1)",
        (
            generation_id,
            root_department,
            root_department,
            generation_id,
            child_department,
            child_department,
            generation_id,
            root_department,
            child_department,
        ),
    )
    connection.execute(
        "insert into platform_control.member_departments "
        "(generation_id,member_key,department_key) values (%s,%s,%s)",
        (generation_id, member_key, child_department),
    )
    connection.execute(
        "update platform_control.directory_state set active_generation_id=%s,"
        "last_complete_at=now(),updated_at=now() where singleton",
        (generation_id,),
    )
    return user_id, root_department, child_department, generation_id


def _insert_grant(
    connection,
    *,
    agent_id: str,
    target_kind: str,
    actor_id: UUID,
    user_id: UUID | None = None,
    department_id: UUID | None = None,
    include_descendants: bool = False,
    revoked: bool = False,
) -> UUID:
    grant_id = uuid4()
    connection.execute(
        "insert into platform_control.agent_use_grants "
        "(agent_use_grant_id,agent_id,target_kind,target_internal_user_id,"
        "target_department_key,include_descendants,created_by,revoked_at,"
        "revoked_by) values (%s,%s,%s,%s,%s,%s,%s,"
        "case when %s then now() end,case when %s then %s end)",
        (
            grant_id,
            agent_id,
            target_kind,
            user_id,
            department_id,
            include_descendants,
            actor_id,
            revoked,
            revoked,
            actor_id,
        ),
    )
    return grant_id


@pytest.mark.postgres
def test_agent_brain_schema_uses_uuid_ownership_and_ciphertext_only_storage(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as connection:
        columns = connection.execute(
            "select table_name,column_name,data_type from information_schema.columns "
            "where table_schema='platform_control' and table_name=any(%s) "
            "order by table_name,column_name",
            (list(MISSION_TABLES),),
        ).fetchall()
        column_types = {(table, column): data_type for table, column, data_type in columns}

        for table, identifier in (
            ("missions", "mission_id"),
            ("mission_messages", "message_id"),
            ("mission_tasks", "task_id"),
            ("mission_runs", "run_id"),
            ("mission_events", "event_id"),
        ):
            assert column_types[(table, identifier)] == "uuid"
        assert column_types[("missions", "owner_internal_user_id")] == "uuid"

        ciphertext_columns = {
            (table, column)
            for (table, column), data_type in column_types.items()
            if column.endswith("_ciphertext") and data_type == "bytea"
        }
        assert ciphertext_columns == {
            ("mission_messages", "content_ciphertext"),
            ("mission_tasks", "objective_ciphertext"),
            ("mission_runs", "input_ciphertext"),
            ("mission_runs", "output_ciphertext"),
            ("mission_events", "payload_ciphertext"),
        }
        assert column_types[("mission_runs", "encryption_key_version")] == "integer"
        assert not {
            column
            for _table, column, data_type in columns
            if data_type in ("text", "character varying")
        } & {"prompt", "response", "content", "objective", "payload"}

        foreign_keys = connection.execute(
            "select tc.table_name,kcu.column_name,ccu.table_name,ccu.column_name "
            "from information_schema.table_constraints tc "
            "join information_schema.key_column_usage kcu "
            "on kcu.constraint_schema=tc.constraint_schema "
            "and kcu.constraint_name=tc.constraint_name "
            "join information_schema.constraint_column_usage ccu "
            "on ccu.constraint_schema=tc.constraint_schema "
            "and ccu.constraint_name=tc.constraint_name "
            "where tc.constraint_schema='platform_control' "
            "and tc.constraint_type='FOREIGN KEY'"
        ).fetchall()
        assert (
            "missions",
            "owner_internal_user_id",
            "internal_users",
            "internal_user_id",
        ) in foreign_keys

        owner_id = uuid4()
        mission_id = uuid4()
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id,display_name,status) values (%s,'Mission Owner','active')",
            (owner_id,),
        )
        connection.execute(
            "insert into platform_control.missions "
            "(mission_id,owner_internal_user_id,client_request_id,mode,status) "
            "values (%s,%s,%s,'brain','planning')",
            (mission_id, owner_id, uuid4()),
        )
        connection.execute(
            "insert into platform_control.mission_messages "
            "(message_id,mission_id,seq,role,content_ciphertext,"
            "encryption_key_version) values (%s,%s,1,'user',%s,1)",
            (uuid4(), mission_id, b"c" * 29),
        )
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                "insert into platform_control.mission_messages "
                "(message_id,mission_id,seq,role,content_ciphertext,"
                "encryption_key_version) values (%s,%s,2,'user',%s,0)",
                (uuid4(), mission_id, b"c" * 29),
            )
        connection.rollback()


@pytest.mark.postgres
def test_mission_state_matrices_sequences_and_single_active_child_are_enforced(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as connection:
        owner_id = uuid4()
        mission_id = uuid4()
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id,display_name,status) values (%s,'State Owner','active')",
            (owner_id,),
        )
        connection.execute(
            "insert into platform_control.missions "
            "(mission_id,owner_internal_user_id,client_request_id,mode,status,"
            "direct_agent_id) values (%s,%s,%s,'direct_agent','delegated','hr-bot')",
            (mission_id, owner_id, uuid4()),
        )
        connection.execute(
            "insert into platform_control.mission_tasks "
            "(task_id,mission_id,agent_id,objective_ciphertext,"
            "encryption_key_version,status) values (%s,%s,'hr-bot',%s,1,'queued')",
            (uuid4(), mission_id, b"o" * 29),
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            connection.execute(
                "insert into platform_control.mission_tasks "
                "(task_id,mission_id,agent_id,objective_ciphertext,"
                "encryption_key_version,status,started_at) values "
                "(%s,%s,'fae-bot',%s,1,'running',now())",
                (uuid4(), mission_id, b"o" * 29),
            )
        connection.rollback()

    invalid_values = (
        ("missions", "mode", "workflow"),
        ("missions", "status", "running"),
        ("mission_tasks", "status", "planning"),
        ("mission_runs", "phase", "review"),
        ("mission_events", "event_type", "agent.debug"),
    )
    with psycopg.connect(environment["admin"]) as connection:
        for table, column, invalid in invalid_values:
            checks = connection.execute(
                "select pg_get_constraintdef(oid) from pg_constraint "
                "where conrelid=(%s::regclass) and contype='c'",
                (f"platform_control.{table}",),
            ).fetchall()
            assert any(
                column in definition and invalid not in definition
                for definition, in checks
            )

        indexes = "\n".join(
            row[0]
            for row in connection.execute(
                "select indexdef from pg_indexes where schemaname='platform_control' "
                "and tablename in ('mission_messages','mission_events','mission_tasks')"
            ).fetchall()
        ).lower()
        assert "mission_messages" in indexes and "mission_id, seq" in indexes
        assert "mission_events" in indexes and "mission_id, seq" in indexes
        assert "unique" in indexes and "status" in indexes
        assert "queued" in indexes and "running" in indexes


@pytest.mark.postgres
def test_agent_use_scope_tracks_active_generation_and_defaults_to_deny(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as connection:
        direct_user, root_department, child_department, _generation = (
            _seed_active_directory(connection)
        )
        actor_id = uuid4()
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id,display_name,status) values (%s,'Grant Actor','active')",
            (actor_id,),
        )
        _insert_grant(
            connection,
            agent_id="direct-bot",
            target_kind="user",
            actor_id=actor_id,
            user_id=direct_user,
        )
        _insert_grant(
            connection,
            agent_id="department-bot",
            target_kind="department",
            actor_id=actor_id,
            department_id=child_department,
            include_descendants=True,
        )
        _insert_grant(
            connection,
            agent_id="ancestor-bot",
            target_kind="department",
            actor_id=actor_id,
            department_id=root_department,
            include_descendants=True,
        )
        _insert_grant(
            connection,
            agent_id="all-bot",
            target_kind="all_members",
            actor_id=actor_id,
        )
        _insert_grant(
            connection,
            agent_id="revoked-bot",
            target_kind="user",
            actor_id=actor_id,
            user_id=direct_user,
            revoked=True,
        )
        stale_department = uuid4()
        _insert_grant(
            connection,
            agent_id="fabricated-bot",
            target_kind="department",
            actor_id=actor_id,
            department_id=stale_department,
            include_descendants=True,
        )

        decisions = connection.execute(
            "select agent_id,platform_control.has_agent_use_scope_v28(%s,agent_id) "
            "from unnest(%s::text[]) agent_id order by agent_id",
            (
                direct_user,
                [
                    "direct-bot",
                    "department-bot",
                    "ancestor-bot",
                    "all-bot",
                    "revoked-bot",
                    "missing-bot",
                    "fabricated-bot",
                    "codex-assistant",
                ],
            ),
        ).fetchall()
        assert dict(decisions) == {
            "all-bot": True,
            "ancestor-bot": True,
            "codex-assistant": False,
            "department-bot": True,
            "direct-bot": True,
            "fabricated-bot": False,
            "missing-bot": False,
            "revoked-bot": False,
        }

        connection.execute(
            "update platform_control.internal_users set status='inactive' "
            "where internal_user_id=%s",
            (direct_user,),
        )
        assert connection.execute(
            "select platform_control.has_agent_use_scope_v28(%s,'all-bot')",
            (direct_user,),
        ).fetchone() == (False,)


@pytest.mark.postgres
def test_grant_shape_acl_and_audited_maintenance_boundary(control_database) -> None:
    for environment in control_database["environments"].values():
        roles = environment["roles"]
        app_role = roles[1]
        maintenance_role = roles[5]
        with psycopg.connect(environment["admin"]) as connection:
            constraints = "\n".join(
                row[0]
                for row in connection.execute(
                    "select pg_get_constraintdef(oid) from pg_constraint "
                    "where conrelid='platform_control.agent_use_grants'::regclass "
                    "and contype='c'"
                ).fetchall()
            )
            for target_kind in ("user", "department", "all_members"):
                assert target_kind in constraints

            app_grants = connection.execute(
                "select table_name,privilege_type from information_schema.role_table_grants "
                "where grantee=%s and table_schema='platform_control' "
                "and table_name=any(%s) order by table_name,privilege_type",
                (app_role, ["agent_use_grants", *MISSION_TABLES]),
            ).fetchall()
            assert not any(privilege == "DELETE" for _table, privilege in app_grants)
            assert not any(table == "agent_use_grants" for table, _privilege in app_grants)
            assert {table for table, _privilege in app_grants} == set(MISSION_TABLES)

            function_acl = connection.execute(
                "select proname,has_function_privilege(%s,proc.oid,'execute'),"
                "has_function_privilege(%s,proc.oid,'execute'),"
                "has_function_privilege('public',proc.oid,'execute') "
                "from pg_proc proc where proc.pronamespace="
                "'platform_control'::regnamespace and proname=any(%s) "
                "order by proname",
                (
                    app_role,
                    maintenance_role,
                    [
                        "grant_agent_use_scope_v28",
                        "has_agent_use_scope_v28",
                        "revoke_agent_use_scope_v28",
                    ],
                ),
            ).fetchall()
            assert function_acl == [
                ("grant_agent_use_scope_v28", False, True, False),
                ("has_agent_use_scope_v28", True, False, False),
                ("revoke_agent_use_scope_v28", False, True, False),
            ]

            public_acl = connection.execute(
                "select count(*) from information_schema.role_table_grants "
                "where grantee='PUBLIC' and table_schema='platform_control' "
                "and table_name=any(%s)",
                (["agent_use_grants", *MISSION_TABLES],),
            ).fetchone()
            assert public_acl == (0,)

            opposite_roles = [role for role in ROLES if role not in roles]
            assert all(
                not connection.execute(
                    "select has_table_privilege(%s,'platform_control.missions','select')",
                    (role,),
                ).fetchone()[0]
                for role in opposite_roles
            )


@pytest.mark.postgres
def test_maintenance_grant_and_revoke_are_idempotent_and_audited(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    owner_id = uuid4()
    target_id = uuid4()
    grant_id = uuid4()
    grant_request_id = uuid4()
    revoke_request_id = uuid4()
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id,display_name,status,role) values "
            "(%s,'Grant Owner','active','platform_owner'),"
            "(%s,'Grant Target','active','member')",
            (owner_id, target_id),
        )

    maintenance_url = environment["urls"]["platform_control_maintenance"]
    with psycopg.connect(maintenance_url) as connection:
        parameters = (
            grant_id,
            "hr-bot",
            "user",
            target_id,
            None,
            False,
            owner_id,
            "AGENT_GRANT_001",
            grant_request_id,
        )
        assert connection.execute(
            "select platform_control.grant_agent_use_scope_v28("
            "%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            parameters,
        ).fetchone() == (grant_id,)
        assert connection.execute(
            "select platform_control.grant_agent_use_scope_v28("
            "%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            parameters,
        ).fetchone() == (grant_id,)
        assert connection.execute(
            "select platform_control.revoke_agent_use_scope_v28(%s,%s,%s,%s)",
            (grant_id, owner_id, "AGENT_REVOKE_001", revoke_request_id),
        ).fetchone() == (grant_id,)
        assert connection.execute(
            "select platform_control.revoke_agent_use_scope_v28(%s,%s,%s,%s)",
            (grant_id, owner_id, "AGENT_REVOKE_001", revoke_request_id),
        ).fetchone() == (grant_id,)

    with psycopg.connect(environment["admin"]) as connection:
        grant = connection.execute(
            "select created_by,created_audit_event_id,revoked_by,"
            "revoked_audit_event_id,revoked_at is not null "
            "from platform_control.agent_use_grants "
            "where agent_use_grant_id=%s",
            (grant_id,),
        ).fetchone()
        assert grant == (
            owner_id,
            grant_request_id,
            owner_id,
            revoke_request_id,
            True,
        )
        audits = connection.execute(
            "select event_type,result,reason_code from platform_control.audit_events "
            "where audit_event_id=any(%s) order by event_type",
            ([grant_request_id, revoke_request_id],),
        ).fetchall()
        assert audits == [
            ("agent_use_scope_granted", "completed", "offline_maintenance"),
            ("agent_use_scope_revoked", "completed", "offline_maintenance"),
        ]

    app_url = environment["urls"]["platform_control_app"]
    with psycopg.connect(app_url) as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(
                "delete from platform_control.agent_use_grants "
                "where agent_use_grant_id=%s",
                (grant_id,),
            )


def test_agent_id_contract_remains_exact() -> None:
    assert AGENT_PATTERN == "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
