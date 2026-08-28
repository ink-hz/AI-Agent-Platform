from __future__ import annotations

# Pytest fixtures are imported into this module's namespace for discovery.
# ruff: noqa: F401,F811
from pathlib import Path

import psycopg
import pytest
from test_agent_brain_live_repository import live_database, seeded_live_task
from test_control_plane_migration import control_database

MIGRATION = (
    Path(__file__).parents[1]
    / "control_migrations"
    / "051_agent_brain_actions.sql"
)


def test_action_migration_exists() -> None:
    assert MIGRATION.is_file(), f"missing migration: {MIGRATION}"


@pytest.mark.postgres
def test_action_schema_and_web_update_boundary(
    live_database, seeded_live_task
) -> None:
    environment, *_unused = live_database
    _repository, _loop_repository, _loop_id, task_id, _conversation_id = (
        seeded_live_task
    )
    with psycopg.connect(environment["admin"]) as connection:
        columns = {
            row[0]
            for row in connection.execute(
                "select column_name from information_schema.columns where "
                "table_schema='platform_brain' and table_name='agent_task_actions'"
            )
        }
        functions = {
            row[0]
            for row in connection.execute(
                "select proname from pg_proc where pronamespace="
                    "'platform_brain'::regnamespace and proname like '%action%v51'"
            )
        }
    assert {
        "action_id",
        "task_id",
        "action_seq",
        "action_kind",
        "summary_ciphertext",
        "impact_ciphertext",
        "parameters_ciphertext",
        "action_digest",
        "status",
        "expires_at",
        "confirmed_by_internal_user_id",
        "execution_status",
        "execution_deadline_at",
    } <= columns
    assert {
        "propose_agent_task_action_v51",
        "confirm_agent_task_action_v51",
        "reject_agent_task_action_v51",
        "expire_agent_task_actions_v51",
        "supersede_agent_task_action_v51",
        "resume_action_resolution_v51",
    } <= functions

    app_role = next(role for role in environment["roles"] if "control_app" in role)
    with psycopg.connect(environment["admin"]) as connection:
        assert connection.execute(
            "select has_table_privilege(%s,%s,'update')",
            (app_role, "platform_brain.agent_task_actions"),
        ).fetchone()[0] is False
