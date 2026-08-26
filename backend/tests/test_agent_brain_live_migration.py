from __future__ import annotations

from pathlib import Path

import psycopg
import pytest

from test_control_plane_migration import control_database


MIGRATION = (
    Path(__file__).parents[1]
    / "control_migrations"
    / "045_agent_brain_live_collaboration.sql"
)
LIVE_TABLES = {
    "agent_task_sessions",
    "agent_task_messages",
    "brain_thinking_summaries",
    "brain_wait_subscriptions",
    "brain_user_interventions",
}
LIVE_TOOL_NAMES = {
    "await_agent_events",
    "send_agent_message",
    "stop_agent_task",
}
LIVE_PUBLIC_EVENTS = {
    "brain.thinking_summary",
    "brain.waiting_agents",
    "brain.user_intervention",
    "brain.agent_message_sent",
    "brain.agent_stop_requested",
    "agent.thinking_summary",
    "agent.message",
    "agent.work_update",
    "agent.artifact",
    "agent.question",
    "agent.cancelled",
    "agent.task_recovered",
}


def test_live_collaboration_migration_exists() -> None:
    assert MIGRATION.is_file(), f"missing migration: {MIGRATION}"


@pytest.mark.postgres
def test_live_collaboration_schema_and_indexes(control_database) -> None:
    for environment in control_database["environments"].values():
        with psycopg.connect(environment["admin"]) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "select table_name from information_schema.tables "
                    "where table_schema='platform_brain'"
                )
            }
            assert LIVE_TABLES.issubset(tables)

            indexes = {
                row[0]: row[1]
                for row in connection.execute(
                    "select indexname,indexdef from pg_indexes "
                    "where schemaname='platform_brain'"
                )
            }
            assert "one_task_session" in indexes
            assert "one_active_wait_subscription" in indexes
            assert "agent_task_messages_pkey" in indexes
            assert "brain_thinking_summaries_pkey" in indexes
            assert "adapter_delivery_identity_v45" in indexes
            assert "NULLS NOT DISTINCT" in indexes["adapter_delivery_identity_v45"]

            delivery_columns = {
                row[0]: (row[1], row[2])
                for row in connection.execute(
                    "select column_name,is_nullable,column_default "
                    "from information_schema.columns "
                    "where table_schema='platform_brain' "
                    "and table_name='adapter_deliveries'"
                )
            }
            assert delivery_columns["delivery_kind"] == ("NO", "'initial'::text")
            assert delivery_columns["source_message_seq"] == ("YES", None)


@pytest.mark.postgres
def test_live_collaboration_constraints_extend_protocol(control_database) -> None:
    for environment in control_database["environments"].values():
        with psycopg.connect(environment["admin"]) as connection:
            tool_constraint = "\n".join(
                row[0]
                for row in connection.execute(
                    "select pg_get_constraintdef(oid) from pg_constraint "
                    "where conrelid='platform_brain.brain_tool_calls'::regclass "
                    "and contype='c'"
                )
            )
            for tool_name in LIVE_TOOL_NAMES:
                assert f"'{tool_name}'::text" in tool_constraint

            event_constraint = "\n".join(
                row[0]
                for row in connection.execute(
                    "select pg_get_constraintdef(oid) from pg_constraint "
                    "where conrelid="
                    "'platform_control.conversation_events'::regclass "
                    "and contype='c'"
                )
            )
            for event_type in LIVE_PUBLIC_EVENTS:
                assert f"'{event_type}'::text" in event_constraint

            wait_constraint = "\n".join(
                row[0]
                for row in connection.execute(
                    "select pg_get_constraintdef(oid) from pg_constraint "
                    "where conrelid="
                    "'platform_brain.brain_wait_subscriptions'::regclass"
                )
            )
            for state in ("active", "triggered", "cancelled", "expired"):
                assert f"'{state}'::text" in wait_constraint


@pytest.mark.postgres
def test_live_collaboration_roles_are_environment_scoped(control_database) -> None:
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
            brain_expected = {
                "agent_task_sessions": (True, True, True, False, False),
                "agent_task_messages": (True, True, False, False, False),
                "brain_thinking_summaries": (True, True, True, False, False),
                "brain_wait_subscriptions": (True, True, True, False, False),
                "brain_user_interventions": (True, False, True, False, False),
            }
            app_expected = {
                "agent_task_sessions": (True, False, False, False),
                "agent_task_messages": (True, False, False, False),
                "brain_thinking_summaries": (True, False, False, False),
                "brain_wait_subscriptions": (True, False, False, False),
                "brain_user_interventions": (True, True, False, False),
            }
            for table in LIVE_TABLES:
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
                ).fetchone() == brain_expected[table]
                assert connection.execute(
                    "select has_table_privilege(%s,%s,'select'),"
                    "has_table_privilege(%s,%s,'insert'),"
                    "has_table_privilege(%s,%s,'update'),"
                    "has_table_privilege(%s,%s,'delete')",
                    (
                        app_role,
                        f"platform_brain.{table}",
                        app_role,
                        f"platform_brain.{table}",
                        app_role,
                        f"platform_brain.{table}",
                        app_role,
                        f"platform_brain.{table}",
                    ),
                ).fetchone() == app_expected[table]
