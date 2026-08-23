from __future__ import annotations

import hashlib
import uuid

import psycopg
import pytest

from test_control_plane_migration import control_database


RELAY_TABLES = {
    "execution_workers",
    "execution_worker_keys",
    "execution_jobs",
    "execution_events",
    "execution_worker_nonces",
}
LIFECYCLE_FUNCTIONS = {
    "register_execution_worker_v28",
    "add_execution_worker_key_v28",
    "revoke_execution_worker_key_v28",
    "revoke_execution_worker_v28",
}
ALL_RUNTIME_ROLES = (
    "platform_control_migrator",
    "platform_control_app",
    "platform_directory_worker",
    "platform_stream_ingest",
    "platform_audit_append",
    "platform_control_maintenance",
    "platform_control_migrator_preview",
    "platform_control_app_preview",
    "platform_directory_worker_preview",
    "platform_stream_ingest_preview",
    "platform_audit_append_preview",
    "platform_control_maintenance_preview",
)


def _call(connection, function: str, values: tuple[object, ...]) -> None:
    placeholders = ",".join(["%s"] * len(values))
    connection.execute(
        f"select platform_control.{function}({placeholders})",
        values,
    )


@pytest.mark.postgres
def test_execution_relay_schema_is_versioned_encrypted_and_append_only(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "select table_name from information_schema.tables "
                "where table_schema='platform_control'"
            )
        }
        grants = connection.execute(
            "select table_name,privilege_type "
            "from information_schema.role_table_grants "
            "where grantee='platform_control_app' "
            "and table_name like 'execution_%'"
        ).fetchall()
        columns = connection.execute(
            "select table_name,column_name,data_type,is_nullable "
            "from information_schema.columns "
            "where table_schema='platform_control' "
            "and table_name in ('execution_jobs','execution_events')"
        ).fetchall()
        constraints = connection.execute(
            "select conrelid::regclass::text,pg_get_constraintdef(oid) "
            "from pg_constraint where connamespace='platform_control'::regnamespace "
            "and conrelid in ("
            "'platform_control.execution_worker_keys'::regclass,"
            "'platform_control.execution_jobs'::regclass,"
            "'platform_control.execution_events'::regclass)"
        ).fetchall()

    assert RELAY_TABLES <= tables
    assert {
        table for table, privilege in grants if privilege == "DELETE"
    } == {"execution_worker_nonces"}
    column_map = {(table, column): (kind, nullable) for table, column, kind, nullable in columns}
    assert column_map[("execution_jobs", "payload_ciphertext")] == ("bytea", "NO")
    assert column_map[("execution_jobs", "encryption_key_version")] == (
        "integer",
        "NO",
    )
    assert column_map[("execution_events", "payload_ciphertext")] == ("bytea", "NO")
    assert column_map[("execution_events", "encryption_key_version")] == (
        "integer",
        "NO",
    )
    definitions = "\n".join(definition for _, definition in constraints)
    assert "octet_length(public_key) = 32" in definitions
    assert "UNIQUE (worker_id, key_id)" in definitions or (
        "PRIMARY KEY (worker_id, key_id)" in definitions
    )
    assert "PRIMARY KEY (run_id, seq)" in definitions
    assert "encryption_key_version > 0" in definitions
    for status in (
        "queued",
        "leased",
        "dispatched",
        "running",
        "completed",
        "failed",
        "cancelled",
        "interrupted",
    ):
        assert f"'{status}'" in definitions


@pytest.mark.postgres
def test_execution_relay_grants_are_environment_scoped_and_least_privilege(
    control_database,
) -> None:
    for environment in control_database["environments"].values():
        roles = environment["roles"]
        app, maintenance = roles[1], roles[5]
        opposite = (
            control_database["environments"]["preview"]
            if environment["database"] == "agent_platform_control"
            else control_database["environments"]["production"]
        )
        with psycopg.connect(environment["admin"]) as connection:
            app_privileges = {
                (table, privilege)
                for table, privilege in connection.execute(
                    "select table_name,privilege_type "
                    "from information_schema.role_table_grants "
                    "where grantee=%s and table_name=any(%s) "
                    "and table_schema='platform_control'",
                    (app, list(RELAY_TABLES)),
                )
            }
            assert app_privileges == {
                ("execution_workers", "SELECT"),
                ("execution_worker_keys", "SELECT"),
                ("execution_jobs", "SELECT"),
                ("execution_jobs", "INSERT"),
                ("execution_jobs", "UPDATE"),
                ("execution_events", "SELECT"),
                ("execution_events", "INSERT"),
                ("execution_events", "UPDATE"),
                ("execution_worker_nonces", "SELECT"),
                ("execution_worker_nonces", "INSERT"),
                ("execution_worker_nonces", "DELETE"),
            }
            assert not connection.execute(
                "select has_table_privilege(%s,"
                "'platform_control.execution_workers','insert,update,delete') "
                "or has_table_privilege(%s,"
                "'platform_control.execution_worker_keys','insert,update,delete')",
                (app, app),
            ).fetchone()[0]
            for role in roles[0:1] + roles[2:]:
                assert not connection.execute(
                    "select bool_or(has_table_privilege(%s,"
                    "format('platform_control.%%I',table_name),privilege)) "
                    "from unnest(%s::text[]) table_name cross join "
                    "unnest(array['select','insert','update','delete']) privilege",
                    (role, list(RELAY_TABLES)),
                ).fetchone()[0]
            for role in opposite["roles"]:
                assert not connection.execute(
                    "select bool_or(has_table_privilege(%s,"
                    "format('platform_control.%%I',table_name),privilege)) "
                    "from unnest(%s::text[]) table_name cross join "
                    "unnest(array['select','insert','update','delete']) privilege",
                    (role, list(RELAY_TABLES)),
                ).fetchone()[0]
            assert maintenance not in {role for role in roles if role == app}


@pytest.mark.postgres
def test_execution_relay_functions_are_hardened_and_role_bounded(
    control_database,
) -> None:
    for environment in control_database["environments"].values():
        roles = environment["roles"]
        with psycopg.connect(environment["admin"]) as connection:
            functions = connection.execute(
                "select proname,oid,prosecdef,proconfig,"
                "has_function_privilege(%s,oid,'execute'),"
                "has_function_privilege(%s,oid,'execute'),"
                "has_function_privilege('public',oid,'execute') "
                "from pg_proc where pronamespace='platform_control'::regnamespace "
                "and proname = any(%s) order by proname",
                (
                    roles[5],
                    roles[1],
                    sorted(
                        LIFECYCLE_FUNCTIONS
                        | {
                            "append_execution_worker_audit_v28",
                            "replay_execution_worker_audit_v28",
                            "touch_execution_worker_v28",
                        }
                    ),
                ),
            ).fetchall()
        assert {row[0] for row in functions} == LIFECYCLE_FUNCTIONS | {
            "append_execution_worker_audit_v28",
            "replay_execution_worker_audit_v28",
            "touch_execution_worker_v28",
        }
        for name, oid, security_definer, settings, maintenance_execute, app_execute, public_execute in functions:
            assert security_definer is True
            assert settings == ["search_path=pg_catalog, platform_control"]
            assert public_execute is False
            if name in LIFECYCLE_FUNCTIONS:
                assert (maintenance_execute, app_execute) == (True, False)
            elif name == "touch_execution_worker_v28":
                assert (maintenance_execute, app_execute) == (False, True)
            else:
                assert (maintenance_execute, app_execute) == (False, False)
            with psycopg.connect(environment["admin"]) as connection:
                allowed_role = (
                    roles[1]
                    if name == "touch_execution_worker_v28"
                    else roles[5] if name in LIFECYCLE_FUNCTIONS else None
                )
                privileges = dict(
                    connection.execute(
                        "select role_name,has_function_privilege(role_name,%s,'execute') "
                        "from unnest(%s::text[]) role_name",
                        (
                            oid,
                            list(ALL_RUNTIME_ROLES),
                        ),
                    )
                )
                assert privileges == {
                    role: role == allowed_role for role in ALL_RUNTIME_ROLES
                }


@pytest.mark.postgres
def test_execution_worker_lifecycle_is_validated_audited_and_idempotent(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    maintenance_url = environment["urls"]["platform_control_maintenance"]
    worker_id = "worker.alpha-1"
    first_key_id = "worker-v1"
    first_key = bytes(range(32))
    request_id = uuid.uuid4()
    args = (
        worker_id,
        first_key_id,
        first_key,
        ["agent-a", "Agent_B"],
        "OPS_20260821",
        request_id,
    )
    with psycopg.connect(maintenance_url) as connection:
        _call(connection, "register_execution_worker_v28", args)
        _call(connection, "register_execution_worker_v28", args)

    with psycopg.connect(environment["admin"]) as connection:
        assert connection.execute(
            "select status,allowed_agent_ids from platform_control.execution_workers "
            "where worker_id=%s",
            (worker_id,),
        ).fetchone() == ("active", ["agent-a", "Agent_B"])
        assert connection.execute(
            "select count(*) from platform_control.execution_worker_keys "
            "where worker_id=%s",
            (worker_id,),
        ).fetchone() == (1,)
        event = connection.execute(
            "select audit_event_id,request_id,event_type,target_type,"
            "target_internal_id,result,reason_code,sanitized_before_after "
            "from platform_control.audit_events where audit_event_id=%s",
            (request_id,),
        ).fetchone()
    assert event == (
        request_id,
        request_id,
        "execution_worker_registered",
        "execution_worker",
        worker_id,
        "completed",
        "offline_maintenance",
        {
            "reference": "OPS_20260821",
            "worker_id": worker_id,
            "key_id": first_key_id,
            "public_key_sha256": hashlib.sha256(first_key).hexdigest(),
            "allowed_agent_ids": ["agent-a", "Agent_B"],
        },
    )

    with psycopg.connect(maintenance_url) as connection:
        with pytest.raises(psycopg.errors.CheckViolation, match="identity collision"):
            _call(
                connection,
                "register_execution_worker_v28",
                (*args[:-2], "OPS_20260822", request_id),
            )
        connection.rollback()
        with pytest.raises(psycopg.errors.CheckViolation, match="identity collision"):
            _call(
                connection,
                "register_execution_worker_v28",
                (
                    worker_id,
                    first_key_id,
                    first_key,
                    ["Agent_B", "agent-a"],
                    "OPS_20260821",
                    request_id,
                ),
            )
        connection.rollback()
        collision_calls = (
            (
                "add_execution_worker_key_v28",
                (
                    "worker.missing",
                    "worker-v2",
                    b"m" * 32,
                    "OPS_20260822",
                    request_id,
                ),
            ),
            (
                "revoke_execution_worker_key_v28",
                (
                    "worker.missing",
                    "worker-v2",
                    "OPS_20260822",
                    request_id,
                ),
            ),
            (
                "revoke_execution_worker_v28",
                ("worker.missing", "OPS_20260822", request_id),
            ),
        )
        for function, values in collision_calls:
            with pytest.raises(
                psycopg.errors.CheckViolation,
                match="identity collision",
            ):
                _call(connection, function, values)
            connection.rollback()
        with pytest.raises(psycopg.errors.CheckViolation, match="audit input invalid"):
            _call(
                connection,
                "add_execution_worker_key_v28",
                (
                    worker_id,
                    "worker-v2",
                    b"v" * 32,
                    "OPS_20260822",
                    uuid.uuid1(),
                ),
            )
        connection.rollback()
        for invalid_reference in (None, "lowercase", "OPS-123"):
            with pytest.raises(psycopg.errors.CheckViolation):
                _call(
                    connection,
                    "add_execution_worker_key_v28",
                    (
                        worker_id,
                        "worker-v2",
                        bytes(reversed(range(32))),
                        invalid_reference,
                        uuid.uuid4(),
                    ),
                )
            connection.rollback()
        with pytest.raises(psycopg.errors.CheckViolation):
            _call(
                connection,
                "add_execution_worker_key_v28",
                (
                    worker_id,
                    first_key_id,
                    b"x" * 32,
                    "OPS_20260821",
                    uuid.uuid4(),
                ),
            )
        connection.rollback()

        second_key_id = "worker-v2"
        second_key = bytes(reversed(range(32)))
        add_request = uuid.uuid4()
        _call(
            connection,
            "add_execution_worker_key_v28",
            (
                worker_id,
                second_key_id,
                second_key,
                "OPS_20260821",
                add_request,
            ),
        )
        _call(
            connection,
            "add_execution_worker_key_v28",
            (
                worker_id,
                second_key_id,
                second_key,
                "OPS_20260821",
                add_request,
            ),
        )
        revoke_key_request = uuid.uuid4()
        _call(
            connection,
            "revoke_execution_worker_key_v28",
            (
                worker_id,
                second_key_id,
                "OPS_20260822",
                revoke_key_request,
            ),
        )
        _call(
            connection,
            "revoke_execution_worker_key_v28",
            (
                worker_id,
                second_key_id,
                "OPS_20260822",
                revoke_key_request,
            ),
        )
        revoke_worker_request = uuid.uuid4()
        _call(
            connection,
            "revoke_execution_worker_v28",
            (worker_id, "OPS_20260823", revoke_worker_request),
        )
        _call(
            connection,
            "revoke_execution_worker_v28",
            (worker_id, "OPS_20260823", revoke_worker_request),
        )

    with psycopg.connect(environment["admin"]) as connection:
        assert connection.execute(
            "select key_id,status,revoked_at is not null "
            "from platform_control.execution_worker_keys where worker_id=%s "
            "order by key_id",
            (worker_id,),
        ).fetchall() == [
            (first_key_id, "revoked", True),
            (second_key_id, "revoked", True),
        ]
        assert connection.execute(
            "select status,revoked_at is not null "
            "from platform_control.execution_workers where worker_id=%s",
            (worker_id,),
        ).fetchone() == ("revoked", True)
        event_shapes = connection.execute(
            "select audit_event_id,event_type,target_type,target_internal_id,"
            "sanitized_before_after from platform_control.audit_events "
            "where audit_event_id=any(%s) order by event_type",
            ([add_request, revoke_key_request, revoke_worker_request],),
        ).fetchall()
    assert event_shapes == [
        (
            add_request,
            "execution_worker_key_added",
            "execution_worker_key",
            f"{worker_id}/{second_key_id}",
            {
                "reference": "OPS_20260821",
                "worker_id": worker_id,
                "key_id": second_key_id,
                "public_key_sha256": hashlib.sha256(second_key).hexdigest(),
                "allowed_agent_ids": ["agent-a", "Agent_B"],
            },
        ),
        (
            revoke_key_request,
            "execution_worker_key_revoked",
            "execution_worker_key",
            f"{worker_id}/{second_key_id}",
            {
                "reference": "OPS_20260822",
                "worker_id": worker_id,
                "key_id": second_key_id,
                "public_key_sha256": hashlib.sha256(second_key).hexdigest(),
                "allowed_agent_ids": ["agent-a", "Agent_B"],
            },
        ),
        (
            revoke_worker_request,
            "execution_worker_revoked",
            "execution_worker",
            worker_id,
            {
                "reference": "OPS_20260823",
                "worker_id": worker_id,
                "allowed_agent_ids": ["agent-a", "Agent_B"],
            },
        ),
    ]


@pytest.mark.postgres
def test_execution_worker_audit_failure_rolls_back_mutation(control_database) -> None:
    environment = control_database["environments"]["production"]
    worker_id = "worker.rollback"
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "create function platform_control.reject_relay_audit_for_test() "
            "returns trigger language plpgsql as $$ begin "
            "if new.target_internal_id='worker.rollback' then "
            "raise check_violation using message='forced relay audit failure'; "
            "end if; return new; end $$"
        )
        connection.execute(
            "create trigger reject_relay_audit_for_test before insert "
            "on platform_control.audit_events for each row execute function "
            "platform_control.reject_relay_audit_for_test()"
        )
    try:
        maintenance_url = environment["urls"]["platform_control_maintenance"]
        with psycopg.connect(maintenance_url) as connection:
            with pytest.raises(psycopg.errors.CheckViolation, match="forced relay"):
                _call(
                    connection,
                    "register_execution_worker_v28",
                    (
                        worker_id,
                        "worker-v1",
                        b"r" * 32,
                        ["agent-a"],
                        "OPS_20260821",
                        uuid.uuid4(),
                    ),
                )
        with psycopg.connect(environment["admin"]) as connection:
            assert connection.execute(
                "select count(*) from platform_control.execution_workers "
                "where worker_id=%s",
                (worker_id,),
            ).fetchone() == (0,)
            assert connection.execute(
                "select count(*) from platform_control.execution_worker_keys "
                "where worker_id=%s",
                (worker_id,),
            ).fetchone() == (0,)
    finally:
        with psycopg.connect(environment["admin"]) as connection:
            connection.execute(
                "drop trigger if exists reject_relay_audit_for_test "
                "on platform_control.audit_events"
            )
            connection.execute(
                "drop function if exists platform_control.reject_relay_audit_for_test()"
            )


@pytest.mark.postgres
def test_execution_worker_heartbeat_only_touches_active_last_seen_at(
    control_database,
) -> None:
    environment = control_database["environments"]["preview"]
    maintenance_url = environment["urls"]["platform_control_maintenance_preview"]
    app_url = environment["urls"]["platform_control_app_preview"]
    worker_id = "worker.preview"
    with psycopg.connect(maintenance_url) as connection:
        _call(
            connection,
            "register_execution_worker_v28",
            (
                worker_id,
                "worker-v1",
                b"p" * 32,
                ["agent-a"],
                "OPS_20260821",
                uuid.uuid4(),
            ),
        )
    with psycopg.connect(app_url) as connection:
        _call(connection, "touch_execution_worker_v28", (worker_id,))
    with psycopg.connect(environment["admin"]) as connection:
        assert connection.execute(
            "select last_seen_at is not null,status,allowed_agent_ids "
            "from platform_control.execution_workers where worker_id=%s",
            (worker_id,),
        ).fetchone() == (True, "active", ["agent-a"])
    with psycopg.connect(maintenance_url) as connection:
        _call(
            connection,
            "revoke_execution_worker_v28",
            (worker_id, "OPS_20260822", uuid.uuid4()),
        )
    with psycopg.connect(app_url) as connection:
        with pytest.raises(psycopg.errors.CheckViolation):
            _call(connection, "touch_execution_worker_v28", (worker_id,))
