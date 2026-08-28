from __future__ import annotations

# Pytest fixtures are imported into this module's namespace for discovery.
# ruff: noqa: F401,F811
from dataclasses import fields
from pathlib import Path

import psycopg
import pytest
from app.agent_brain.collaboration_models import (
    PUBLIC_EVENT_KINDS,
    WAIT_WAKE_KINDS,
    AgentTaskPublicEventInput,
    WaitSubscriptionSpec,
)
from app.agent_brain.loop_models import (
    AgentTaskRecord,
    AgentTaskStatus,
    BrainLoopStatus,
)
from test_control_plane_migration import control_database

MIGRATION = (
    Path(__file__).parents[1]
    / "control_migrations"
    / "050_agent_brain_task_wait_state.sql"
)


def _columns(
    connection: psycopg.Connection, schema: str, table: str
) -> dict[str, tuple[str, str, str | None]]:
    return {
        row[0]: (row[1], row[2], row[3])
        for row in connection.execute(
            "select column_name,data_type,is_nullable,column_default "
            "from information_schema.columns where table_schema=%s "
            "and table_name=%s",
            (schema, table),
        )
    }


def _constraint_text(
    connection: psycopg.Connection, schema: str, table: str
) -> str:
    return "\n".join(
        row[0]
        for row in connection.execute(
            "select pg_get_constraintdef(oid) from pg_constraint "
            "where conrelid=(%s || '.' || %s)::regclass and contype='c'",
            (schema, table),
        )
    )


def _primary_key(
    connection: psycopg.Connection, schema: str, table: str
) -> tuple[str, ...]:
    rows = connection.execute(
        "select a.attname from pg_constraint c "
        "join unnest(c.conkey) with ordinality key(attnum,position) on true "
        "join pg_attribute a on a.attrelid=c.conrelid and a.attnum=key.attnum "
        "where c.conrelid=(%s || '.' || %s)::regclass and c.contype='p' "
        "order by key.position",
        (schema, table),
    ).fetchall()
    return tuple(row[0] for row in rows)


def test_task_wait_migration_exists() -> None:
    assert MIGRATION.is_file(), f"missing migration: {MIGRATION}"


def test_python_state_and_event_contract_includes_intervention_states() -> None:
    assert BrainLoopStatus.WAITING_CONFIRMATION.value == "waiting_confirmation"
    assert {
        AgentTaskStatus.DISPATCHED.value,
        AgentTaskStatus.WAITING_INPUT.value,
        AgentTaskStatus.WAITING_CONFIRMATION.value,
    } == {"dispatched", "waiting_input", "waiting_confirmation"}
    assert {field.name for field in fields(AgentTaskRecord)} >= {
        "dispatched_at",
        "active_elapsed_ms",
        "terminal_reason_code",
    }
    assert {"input_required", "action_required"} <= PUBLIC_EVENT_KINDS
    assert {"input_required", "action_required"} <= WAIT_WAKE_KINDS


@pytest.mark.postgres
def test_v50_has_one_delivery_waterline(control_database) -> None:
    for environment in control_database["environments"].values():
        with psycopg.connect(environment["admin"]) as connection:
            wait_columns = _columns(
                connection, "platform_brain", "brain_wait_subscriptions"
            )
            assert "cursors" not in wait_columns
            cursor_columns = _columns(
                connection, "platform_brain", "brain_task_event_cursors"
            )
            assert {
                "task_id",
                "loop_id",
                "delivered_seq",
                "updated_at",
            }.issubset(cursor_columns)
            assert cursor_columns["delivered_seq"] == (
                "integer",
                "NO",
                "0",
            )
            assert _primary_key(
                connection, "platform_brain", "brain_task_event_cursors"
            ) == ("task_id",)
            index = connection.execute(
                "select indexdef from pg_indexes where schemaname='platform_brain' "
                "and indexname='brain_task_event_cursors_loop'"
            ).fetchone()
            assert index is not None
            assert "(loop_id, task_id)" in index[0]


@pytest.mark.postgres
def test_v50_extends_task_loop_and_turn_state(control_database) -> None:
    required_task_states = {
        "queued",
        "dispatched",
        "running",
        "waiting_input",
        "waiting_confirmation",
        "completed",
        "failed",
        "cancelled",
        "timed_out",
        "unavailable",
    }
    for environment in control_database["environments"].values():
        with psycopg.connect(environment["admin"]) as connection:
            task_columns = _columns(connection, "platform_brain", "agent_tasks")
            assert task_columns["dispatched_at"][:2] == (
                "timestamp with time zone",
                "YES",
            )
            assert task_columns["active_elapsed_ms"] == ("bigint", "NO", "0")
            assert task_columns["terminal_reason_code"][:2] == ("text", "YES")
            task_checks = _constraint_text(
                connection, "platform_brain", "agent_tasks"
            )
            for state in required_task_states:
                assert f"'{state}'::text" in task_checks
            assert "terminal_reason_code" in task_checks
            assert "active_elapsed_ms" in task_checks

            loop_columns = _columns(connection, "platform_brain", "brain_loops")
            assert loop_columns["intervention_expires_at"][:2] == (
                "timestamp with time zone",
                "YES",
            )
            loop_checks = _constraint_text(
                connection, "platform_brain", "brain_loops"
            )
            assert "'waiting_confirmation'::text" in loop_checks
            assert "intervention_expires_at" in loop_checks

            turn_checks = _constraint_text(
                connection, "platform_control", "conversation_turns"
            )
            assert "'waiting_confirmation'::text" in turn_checks
            active_index = connection.execute(
                "select indexdef from pg_indexes where "
                "schemaname='platform_control' "
                "and indexname='one_active_conversation_turn'"
            ).fetchone()
            assert active_index is not None
            assert "waiting_confirmation" in active_index[0]


@pytest.mark.postgres
def test_v50_extends_wait_and_event_allowlists(control_database) -> None:
    for environment in control_database["environments"].values():
        with psycopg.connect(environment["admin"]) as connection:
            wait_checks = _constraint_text(
                connection, "platform_brain", "brain_wait_subscriptions"
            )
            assert "'input_required'::text" in wait_checks
            assert "'action_required'::text" in wait_checks
            assert "cardinality(wake_on) >= 1" in wait_checks
            assert "cardinality(wake_on) <= 7" in wait_checks
            assert "trigger_origin" in wait_checks
            wait_columns = _columns(
                connection, "platform_brain", "brain_wait_subscriptions"
            )
            assert wait_columns["trigger_origin"] == (
                "text",
                "NO",
                "'agent_event'::text",
            )

            event_checks = _constraint_text(
                connection, "platform_control", "conversation_events"
            )
            assert "'agent.input_required'::text" in event_checks
            assert "'agent.action_required'::text" in event_checks

            function_body = connection.execute(
                "select pg_get_functiondef(" 
                "'platform_brain.append_agent_task_event_v50(uuid,integer,text,"
                "bytea,integer,bytea,timestamptz)'::regprocedure)"
            ).fetchone()[0]
            assert "'input_required'" in function_body
            assert "'action_required'" in function_body

            dispatch_body = connection.execute(
                "select pg_get_functiondef("
                "'platform_brain.mark_adapter_delivery_dispatched_v50(uuid,uuid)'"
                "::regprocedure)"
            ).fetchone()[0]
            assert "status='dispatched'" in dispatch_body
            assert "started_at" not in dispatch_body

            failure_body = connection.execute(
                "select pg_get_functiondef("
                "'platform_brain.fail_agent_task_protocol_v50(uuid)'"
                "::regprocedure)"
            ).fetchone()[0]
            assert "terminal_reason_code='protocol_violation'" in failure_body
            health_columns = _columns(
                connection, "platform_brain", "agent_runtime_health"
            )
            assert {"agent_id", "status", "reason_code", "source_task_id"} <= set(
                health_columns
            )


@pytest.mark.postgres
def test_v50_cursor_and_function_grants_are_environment_scoped(
    control_database,
) -> None:
    for environment in control_database["environments"].values():
        brain_role = next(
            role for role in environment["roles"] if "brain_worker" in role
        )
        app_role = next(
            role for role in environment["roles"] if "control_app" in role
        )
        opposite_role = (
            "platform_brain_worker_preview"
            if brain_role == "platform_brain_worker"
            else "platform_brain_worker"
        )
        with psycopg.connect(environment["admin"]) as connection:
            assert connection.execute(
                "select has_table_privilege(%s,%s,'select'),"
                "has_table_privilege(%s,%s,'insert'),"
                "has_table_privilege(%s,%s,'update'),"
                "has_table_privilege(%s,%s,'delete'),"
                "has_table_privilege(%s,%s,'select'),"
                "has_table_privilege(%s,%s,'select')",
                (
                    brain_role,
                    "platform_brain.brain_task_event_cursors",
                    brain_role,
                    "platform_brain.brain_task_event_cursors",
                    brain_role,
                    "platform_brain.brain_task_event_cursors",
                    brain_role,
                    "platform_brain.brain_task_event_cursors",
                    app_role,
                    "platform_brain.brain_task_event_cursors",
                    opposite_role,
                    "platform_brain.brain_task_event_cursors",
                ),
            ).fetchone() == (True, True, True, False, False, False)
            assert connection.execute(
                "select has_function_privilege(%s,"
                "'platform_brain.append_agent_task_event_v50(uuid,integer,text,"
                "bytea,integer,bytea,timestamptz)','execute'),"
                "has_function_privilege(%s,"
                "'platform_brain.append_agent_task_event_v50(uuid,integer,text,"
                "bytea,integer,bytea,timestamptz)','execute'),"
                "has_function_privilege(%s,"
                "'platform_brain.append_agent_task_event_v50(uuid,integer,text,"
                "bytea,integer,bytea,timestamptz)','execute')",
                (brain_role, app_role, opposite_role),
            ).fetchone() == (True, False, False)
            assert connection.execute(
                "select has_function_privilege(%s,"
                "'platform_brain.mark_adapter_delivery_dispatched_v50(uuid,uuid)',"
                "'execute'),has_function_privilege(%s,"
                "'platform_brain.fail_agent_task_protocol_v50(uuid)','execute'),"
                "has_function_privilege(%s,"
                "'platform_brain.fail_agent_task_protocol_v50(uuid)','execute')",
                (brain_role, brain_role, app_role),
            ).fetchone() == (True, True, False)
            assert connection.execute(
                "select has_table_privilege(%s,%s,'select'),"
                "has_table_privilege(%s,%s,'update'),"
                "has_table_privilege(%s,%s,'select'),"
                "has_table_privilege(%s,%s,'update')",
                (
                    brain_role,
                    "platform_brain.agent_runtime_health",
                    brain_role,
                    "platform_brain.agent_runtime_health",
                    app_role,
                    "platform_brain.agent_runtime_health",
                    app_role,
                    "platform_brain.agent_runtime_health",
                ),
            ).fetchone() == (True, True, True, False)


def test_intervention_events_validate_in_python_models() -> None:
    from datetime import UTC, datetime
    from uuid import uuid4

    task_id = uuid4()
    event = AgentTaskPublicEventInput(
        task_id=task_id,
        seq=1,
        event_type="action_required",
        payload={"action_id": str(uuid4())},
        created_at=datetime.now(UTC),
    )
    wait = WaitSubscriptionSpec(
        tool_call_id=uuid4(),
        loop_id=uuid4(),
        task_ids=(task_id,),
        wake_on=("input_required", "action_required"),
    )
    assert event.event_type == "action_required"
    assert wait.wake_on == ("input_required", "action_required")
