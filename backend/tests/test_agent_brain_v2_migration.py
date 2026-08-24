from __future__ import annotations

from pathlib import Path

import psycopg
import pytest

from test_control_plane_migration import ROLES, control_database


MIGRATION = (
    Path(__file__).parents[1]
    / "control_migrations"
    / "039_agent_brain_durable_loop.sql"
)
BRAIN_TABLES = {
    "authorization_snapshots",
    "brain_loops",
    "brain_steps",
    "brain_tool_calls",
    "agent_tasks",
    "agent_task_events",
    "adapter_deliveries",
    "brain_checkpoints",
}
NON_TERMINAL_TURN_STATES = {
    "accepted",
    "running",
    "waiting_agents",
    "waiting_user",
    "completing",
}
BRAIN_WORKER_NAMES = {
    "agent-brain-step",
    "agent-brain-adapter",
    "agent-brain-reaper",
}


def test_v2_migration_file_exists() -> None:
    assert MIGRATION.is_file(), f"missing migration: {MIGRATION}"


@pytest.mark.postgres
def test_v2_schema_enforces_durable_loop_invariants(control_database) -> None:
    for environment in control_database["environments"].values():
        with psycopg.connect(environment["admin"]) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "select table_name from information_schema.tables "
                    "where table_schema='platform_brain'"
                )
            }
            assert BRAIN_TABLES.issubset(tables)

            indexes = "\n".join(
                row[0]
                for row in connection.execute(
                    "select indexdef from pg_indexes "
                    "where schemaname='platform_brain'"
                )
            )
            for required in (
                "one_active_brain_step",
                "one_active_adapter_delivery",
                "brain_tool_call_id",
                "(task_id, seq)",
                "(step_id, tool_index)",
                "(step_id, provider_tool_call_id)",
            ):
                assert required in indexes

            constraints = "\n".join(
                row[0]
                for row in connection.execute(
                    "select pg_get_constraintdef(oid) from pg_constraint "
                    "where connamespace='platform_brain'::regnamespace"
                )
            )
            for status in (
                "queued",
                "running",
                "waiting_agents",
                "waiting_user",
                "completing",
                "completed",
                "failed",
                "cancelled",
                "interrupted",
                "leased",
                "requesting_model",
                "waiting_tool_results",
                "accepted",
                "waiting_result",
                "result_ready",
                "consumed",
                "timed_out",
                "unavailable",
                "dispatched",
                "expired",
            ):
                assert f"'{status}'::text" in constraints


@pytest.mark.postgres
def test_v2_turn_and_event_contract_is_expanded(control_database) -> None:
    for environment in control_database["environments"].values():
        with psycopg.connect(environment["admin"]) as connection:
            turn_columns = {
                row[0]
                for row in connection.execute(
                    "select column_name from information_schema.columns "
                    "where table_schema='platform_control' "
                    "and table_name='conversation_turns'"
                )
            }
            assert "retry_of_turn_id" in turn_columns

            turn_constraints = "\n".join(
                row[0]
                for row in connection.execute(
                    "select pg_get_constraintdef(oid) from pg_constraint "
                    "where conrelid="
                    "'platform_control.conversation_turns'::regclass"
                )
            )
            for status in NON_TERMINAL_TURN_STATES:
                assert f"'{status}'::text" in turn_constraints

            active_index = connection.execute(
                "select indexdef from pg_indexes "
                "where schemaname='platform_control' "
                "and indexname='one_active_conversation_turn'"
            ).fetchone()[0]
            for status in NON_TERMINAL_TURN_STATES:
                assert f"'{status}'::text" in active_index

            event_constraint = "\n".join(
                row[0]
                for row in connection.execute(
                    "select pg_get_constraintdef(oid) from pg_constraint "
                    "where conrelid="
                    "'platform_control.conversation_events'::regclass "
                    "and contype='c'"
                )
            )
            for event_type in (
                "brain.started",
                "brain.step_started",
                "agent.task_dispatched",
                "agent.task_completed",
                "brain.batch_settled",
                "brain.resumed",
                "brain.user_input_requested",
                "brain.answer_submitted",
                "brain.failed",
            ):
                assert f"'{event_type}'::text" in event_constraint


@pytest.mark.postgres
def test_brain_role_is_environment_scoped_and_least_privileged(
    control_database,
) -> None:
    for environment in control_database["environments"].values():
        brain_role = next(role for role in environment["roles"] if "brain_worker" in role)
        opposite_role = (
            "platform_brain_worker_preview"
            if brain_role == "platform_brain_worker"
            else "platform_brain_worker"
        )
        with psycopg.connect(environment["admin"]) as connection:
            for table in BRAIN_TABLES:
                assert connection.execute(
                    "select has_table_privilege(%s,%s,'select'),"
                    "has_table_privilege(%s,%s,'insert'),"
                    "has_table_privilege(%s,%s,'update'),"
                    "has_table_privilege(%s,%s,'delete'),"
                    "has_table_privilege(%s,%s,'select')",
                    (
                        brain_role,
                        f"platform_brain.{table}",
                        brain_role,
                        f"platform_brain.{table}",
                        brain_role,
                        f"platform_brain.{table}",
                        brain_role,
                        f"platform_brain.{table}",
                        opposite_role,
                        f"platform_brain.{table}",
                    ),
                ).fetchone() == (True, True, True, False, False)

            for protected in (
                "provider_identities",
                "web_sessions",
                "directory_members",
                "audit_events",
            ):
                assert connection.execute(
                    "select has_table_privilege(%s,%s,'select')",
                    (brain_role, f"platform_control.{protected}"),
                ).fetchone() == (False,)


@pytest.mark.postgres
def test_brain_heartbeat_function_limits_worker_names(control_database) -> None:
    for environment in control_database["environments"].values():
        brain_role = next(role for role in environment["roles"] if "brain_worker" in role)
        with psycopg.connect(environment["admin"]) as connection:
            function = connection.execute(
                "select oid,prosecdef,proconfig from pg_proc "
                "where pronamespace='platform_control'::regnamespace "
                "and proname='upsert_brain_worker_heartbeat_v39'"
            ).fetchone()
            assert function is not None
            oid, security_definer, config = function
            assert security_definer is True
            assert config == ["search_path=pg_catalog, platform_control"]
            assert connection.execute(
                "select has_function_privilege(%s,%s,'execute'),"
                "has_table_privilege(%s,'platform_control.worker_heartbeats','insert'),"
                "has_table_privilege(%s,'platform_control.worker_heartbeats','update')",
                (brain_role, oid, brain_role, brain_role),
            ).fetchone() == (True, False, False)

        with psycopg.connect(environment["urls"][brain_role]) as worker:
            for worker_name in BRAIN_WORKER_NAMES:
                assert worker.execute(
                    "select platform_control.upsert_brain_worker_heartbeat_v39("
                    "%s,'healthy',null,clock_timestamp())",
                    (worker_name,),
                ).fetchone() == (True,)
            with pytest.raises(psycopg.errors.CheckViolation):
                worker.execute(
                    "select platform_control.upsert_brain_worker_heartbeat_v39("
                    "'dingtalk-directory-event','healthy',null,clock_timestamp())"
                )


def test_all_runtime_roles_include_brain_workers() -> None:
    assert "platform_brain_worker" in ROLES
    assert "platform_brain_worker_preview" in ROLES
