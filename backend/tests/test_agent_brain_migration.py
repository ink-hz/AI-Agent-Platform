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
TASK_2_TABLES = ("agent_use_grants", *MISSION_TABLES)
V28_FUNCTIONS = (
    "grant_agent_use_scope_v28",
    "has_agent_use_scope_v28",
    "revoke_agent_use_scope_v28",
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
        mission_comment = connection.execute(
            "select obj_description('platform_control.missions'::regclass)"
        ).fetchone()[0]
        assert "trusted backend" in mission_comment
        assert "Task 4" in mission_comment and "Task 6" in mission_comment
        assert "keyring" in mission_comment

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
def test_mission_state_matrices_sequences_and_single_lifetime_child_are_enforced(
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
            "encryption_key_version,status,terminal_at) values "
            "(%s,%s,'hr-bot',%s,1,'completed',now())",
            (uuid4(), mission_id, b"o" * 29),
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            connection.execute(
                "insert into platform_control.mission_tasks "
                "(task_id,mission_id,task_index,agent_id,objective_ciphertext,"
                "encryption_key_version,status) values "
                "(%s,%s,2,'fae-bot',%s,1,'queued')",
                (uuid4(), mission_id, b"o" * 29),
            )
        connection.rollback()

    with psycopg.connect(environment["admin"]) as connection:
        owner_id = uuid4()
        mission_id = uuid4()
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id,display_name,status) values "
            "(%s,'Invalid State Owner','active')",
            (owner_id,),
        )
        with pytest.raises(psycopg.errors.CheckViolation), connection.transaction():
            connection.execute(
                "insert into platform_control.missions "
                "(mission_id,owner_internal_user_id,client_request_id,mode,status) "
                "values (%s,%s,%s,'workflow','planning')",
                (uuid4(), owner_id, uuid4()),
            )
        with pytest.raises(psycopg.errors.CheckViolation), connection.transaction():
            connection.execute(
                "insert into platform_control.missions "
                "(mission_id,owner_internal_user_id,client_request_id,mode,status) "
                "values (%s,%s,%s,'brain','running')",
                (uuid4(), owner_id, uuid4()),
            )
        connection.execute(
            "insert into platform_control.missions "
            "(mission_id,owner_internal_user_id,client_request_id,mode,status) "
            "values (%s,%s,%s,'brain','planning')",
            (mission_id, owner_id, uuid4()),
        )
        with pytest.raises(psycopg.errors.CheckViolation), connection.transaction():
            connection.execute(
                "insert into platform_control.mission_tasks "
                "(task_id,mission_id,agent_id,objective_ciphertext,"
                "encryption_key_version,status) values "
                "(%s,%s,'hr-bot',%s,1,'planning')",
                (uuid4(), mission_id, b"o" * 29),
            )
        with pytest.raises(psycopg.errors.CheckViolation), connection.transaction():
            connection.execute(
                "insert into platform_control.mission_runs "
                "(run_id,mission_id,phase,agent_id,status,input_ciphertext,"
                "encryption_key_version) values "
                "(%s,%s,'review','hr-bot','queued',%s,1)",
                (uuid4(), mission_id, b"i" * 29),
            )
        with pytest.raises(psycopg.errors.CheckViolation), connection.transaction():
            connection.execute(
                "insert into platform_control.mission_events "
                "(event_id,mission_id,seq,event_type,payload_ciphertext,"
                "encryption_key_version) values "
                "(%s,%s,1,'agent.debug',%s,1)",
                (uuid4(), mission_id, b"e" * 29),
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
        assert "unique" in indexes and "mission_tasks" in indexes


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
            agent_id="hr-bot",
            target_kind="user",
            actor_id=actor_id,
            user_id=direct_user,
        )
        _insert_grant(
            connection,
            agent_id="fae-bot",
            target_kind="department",
            actor_id=actor_id,
            department_id=child_department,
            include_descendants=True,
        )
        _insert_grant(
            connection,
            agent_id="marketing-prospecting-bot",
            target_kind="department",
            actor_id=actor_id,
            department_id=root_department,
            include_descendants=True,
        )
        _insert_grant(
            connection,
            agent_id="marketing-inbound-bot",
            target_kind="all_members",
            actor_id=actor_id,
        )
        _insert_grant(
            connection,
            agent_id="marketing-voice-bot",
            target_kind="user",
            actor_id=actor_id,
            user_id=direct_user,
            revoked=True,
        )
        stale_department = uuid4()
        _insert_grant(
            connection,
            agent_id="marketing-intelligence-bot",
            target_kind="department",
            actor_id=actor_id,
            department_id=stale_department,
            include_descendants=True,
        )
        for excluded_agent_id in (
            "codex-assistant",
            "test-bot",
            "feishu-default",
            "ai-fae-agent",
            "ai-admin-agent",
        ):
            _insert_grant(
                connection,
                agent_id=excluded_agent_id,
                target_kind="user",
                actor_id=actor_id,
                user_id=direct_user,
            )

        decisions = connection.execute(
            "select agent_id,platform_control.has_agent_use_scope_v28(%s,agent_id) "
            "from unnest(%s::text[]) agent_id order by agent_id",
            (
                direct_user,
                [
                    "hr-bot",
                    "fae-bot",
                    "marketing-prospecting-bot",
                    "marketing-inbound-bot",
                    "marketing-voice-bot",
                    "marketing-gtm-bot",
                    "marketing-intelligence-bot",
                    "codex-assistant",
                    "test-bot",
                    "feishu-default",
                    "ai-fae-agent",
                    "ai-admin-agent",
                ],
            ),
        ).fetchall()
        assert dict(decisions) == {
            "ai-admin-agent": False,
            "ai-fae-agent": False,
            "codex-assistant": False,
            "fae-bot": True,
            "feishu-default": False,
            "hr-bot": True,
            "marketing-gtm-bot": False,
            "marketing-inbound-bot": True,
            "marketing-intelligence-bot": False,
            "marketing-prospecting-bot": True,
            "marketing-voice-bot": False,
            "test-bot": False,
        }

        connection.execute(
            "update platform_control.internal_users set status='inactive' "
            "where internal_user_id=%s",
            (direct_user,),
        )
        assert connection.execute(
            "select platform_control.has_agent_use_scope_v28("
            "%s,'marketing-inbound-bot')",
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
                (app_role, list(TASK_2_TABLES)),
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
                        *V28_FUNCTIONS,
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
                (list(TASK_2_TABLES),),
            ).fetchone()
            assert public_acl == (0,)

            opposite_roles = [role for role in ROLES if role not in roles]
            for role in opposite_roles:
                for table in TASK_2_TABLES:
                    qualified_table = f"platform_control.{table}"
                    for privilege in ("select", "insert", "update", "references"):
                        assert connection.execute(
                            "select has_any_column_privilege(%s,%s,%s)",
                            (role, qualified_table, privilege),
                        ).fetchone() == (False,)
                    for privilege in ("delete", "truncate", "trigger"):
                        assert connection.execute(
                            "select has_table_privilege(%s,%s,%s)",
                            (role, qualified_table, privilege),
                        ).fetchone() == (False,)
                for function_name in V28_FUNCTIONS:
                    assert connection.execute(
                        "select has_function_privilege(%s,proc.oid,'execute') "
                        "from pg_proc proc where proc.pronamespace="
                        "'platform_control'::regnamespace and proc.proname=%s",
                        (role, function_name),
                    ).fetchone() == (False,)


@pytest.mark.postgres
def test_app_updates_only_lifecycle_columns(control_database) -> None:
    environment = control_database["environments"]["production"]
    app_role = environment["roles"][1]
    expected_update_columns = {
        ("missions", "cancel_requested"),
        ("missions", "row_version"),
        ("missions", "status"),
        ("missions", "terminal_at"),
        ("missions", "updated_at"),
        ("mission_tasks", "started_at"),
        ("mission_tasks", "status"),
        ("mission_tasks", "terminal_at"),
        ("mission_tasks", "updated_at"),
        ("mission_runs", "output_ciphertext"),
        ("mission_runs", "output_encryption_key_version"),
        ("mission_runs", "started_at"),
        ("mission_runs", "status"),
        ("mission_runs", "terminal_at"),
        ("mission_runs", "updated_at"),
    }
    with psycopg.connect(environment["admin"]) as connection:
        actual_update_columns = set(
            connection.execute(
                "select table_name,column_name "
                "from information_schema.column_privileges "
                "where grantee=%s and table_schema='platform_control' "
                "and table_name=any(%s) and privilege_type='UPDATE'",
                (app_role, list(MISSION_TABLES)),
            ).fetchall()
        )
        assert actual_update_columns == expected_update_columns

        owner_id = uuid4()
        mission_id = uuid4()
        task_id = uuid4()
        run_id = uuid4()
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id,display_name,status) values "
            "(%s,'Immutable Owner','active')",
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
            (uuid4(), mission_id, b"m" * 29),
        )
        connection.execute(
            "insert into platform_control.mission_tasks "
            "(task_id,mission_id,agent_id,objective_ciphertext,"
            "encryption_key_version,status) values "
            "(%s,%s,'hr-bot',%s,1,'queued')",
            (task_id, mission_id, b"t" * 29),
        )
        connection.execute(
            "insert into platform_control.mission_runs "
            "(run_id,mission_id,phase,agent_id,status,input_ciphertext,"
            "encryption_key_version) values "
            "(%s,%s,'planning','agent-brain-bot','queued',%s,1)",
            (run_id, mission_id, b"r" * 29),
        )
        connection.execute(
            "insert into platform_control.mission_events "
            "(event_id,mission_id,run_id,seq,event_type,payload_ciphertext,"
            "encryption_key_version) values "
            "(%s,%s,%s,1,'mission.started',%s,1)",
            (uuid4(), mission_id, run_id, b"e" * 29),
        )

    immutable_updates = (
        (
            "missions",
            "owner_internal_user_id=owner_internal_user_id",
            "mission_id",
            mission_id,
        ),
        (
            "mission_messages",
            "content_ciphertext=content_ciphertext",
            "mission_id",
            mission_id,
        ),
        ("mission_tasks", "agent_id=agent_id", "task_id", task_id),
        ("mission_runs", "phase=phase", "run_id", run_id),
        (
            "mission_events",
            "payload_ciphertext=payload_ciphertext",
            "mission_id",
            mission_id,
        ),
    )
    for table, assignment, key_column, key_value in immutable_updates:
        with psycopg.connect(environment["urls"][app_role]) as connection:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(
                    f"update platform_control.{table} set {assignment} "
                    f"where {key_column}=%s",
                    (key_value,),
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
    for invalid_reference in (None, "", "lowercase", "SHORT"):
        with psycopg.connect(maintenance_url) as connection:
            with pytest.raises(psycopg.errors.CheckViolation):
                connection.execute(
                    "select platform_control.grant_agent_use_scope_v28("
                    "%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        grant_id,
                        "hr-bot",
                        "user",
                        target_id,
                        None,
                        False,
                        owner_id,
                        invalid_reference,
                        uuid4(),
                    ),
                )

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

    for invalid_reference in (None, "", "lowercase", "SHORT"):
        with psycopg.connect(maintenance_url) as connection:
            with pytest.raises(psycopg.errors.CheckViolation):
                connection.execute(
                    "select platform_control.revoke_agent_use_scope_v28("
                    "%s,%s,%s,%s)",
                    (grant_id, owner_id, invalid_reference, uuid4()),
                )

    with psycopg.connect(maintenance_url) as connection:
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
