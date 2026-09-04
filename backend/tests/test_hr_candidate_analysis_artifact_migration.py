from __future__ import annotations

from pathlib import Path

import psycopg
import pytest
from test_agent_brain_conversation_repository import (
    conversation_database,  # noqa: F401  # noqa: F401
)
from test_control_plane_migration import control_database  # noqa: F401

MIGRATION = (
    Path(__file__).parents[1]
    / "control_migrations"
    / "077_hr_candidate_analysis_artifacts.sql"
)


def _sql() -> str:
    assert MIGRATION.is_file(), f"missing migration: {MIGRATION}"
    return " ".join(MIGRATION.read_text(encoding="utf-8").lower().split())


def test_migration_adds_nullable_artifact_version_fk_and_keeps_history_immutable() -> None:
    sql = _sql()

    assert "add column source_artifact_version_id uuid" in sql
    assert "references platform_attachments.artifact_versions" in sql
    assert "candidate_analysis_versions_immutable_v70" in sql
    assert "drop trigger candidate_analysis_versions_immutable_v70" not in sql
    assert "create_candidate_analysis_v77" in sql
    assert "claim_hr_task_result_projection_v77" in sql
    assert "candidate interview artifact required" in sql
    assert "order by candidate_version.version_no desc" in sql
    assert "current_artifact_versions" not in sql
    assert "revoke execute on function platform_hr.create_candidate_analysis_v70" in sql
    assert (
        "session_user not in ('platform_control_app','platform_control_app_preview')"
        in sql
    )
    assert (
        "(current_database()='agent_platform_control') <> "
        "(session_user='platform_control_app')" in sql
    )


@pytest.mark.postgres
def test_real_database_has_nullable_fk_and_enabled_immutability_trigger(
    conversation_database,  # noqa: F811
) -> None:
    environment, _, _ = conversation_database
    with psycopg.connect(environment["admin"]) as connection:
        column = connection.execute(
            "select is_nullable from information_schema.columns "
            "where table_schema='platform_hr' "
            "and table_name='candidate_analysis_versions' "
            "and column_name='source_artifact_version_id'"
        ).fetchone()
        foreign_key = connection.execute(
            "select confrelid::regclass::text from pg_constraint "
            "where conrelid='platform_hr.candidate_analysis_versions'::regclass "
            "and contype='f' and pg_get_constraintdef(oid) "
            "like '%source_artifact_version_id%'"
        ).fetchone()
        immutable_trigger = connection.execute(
            "select tgenabled from pg_trigger "
            "where tgrelid='platform_hr.candidate_analysis_versions'::regclass "
            "and tgname='candidate_analysis_versions_immutable_v70' "
            "and not tgisinternal"
        ).fetchone()

    v70_signature = (
        "platform_hr.create_candidate_analysis_v70(uuid,uuid,uuid,uuid,uuid,text,"
        "uuid[],uuid[],jsonb,jsonb,jsonb,jsonb,jsonb,text,text)"
    )
    v77_signature = (
        "platform_hr.create_candidate_analysis_v77(uuid,uuid,uuid,uuid,uuid,text,"
        "uuid[],uuid[],jsonb,jsonb,jsonb,jsonb,jsonb,text,text,uuid)"
    )
    with psycopg.connect(
        environment["urls"]["platform_control_app"]
    ) as connection:
        app_privileges = connection.execute(
            "select has_function_privilege(current_user,%s,'EXECUTE'),"
            "has_function_privilege(current_user,%s,'EXECUTE')",
            (v70_signature, v77_signature),
        ).fetchone()
    with psycopg.connect(
        environment["urls"]["platform_brain_worker"]
    ) as connection:
        brain_privileges = connection.execute(
            "select has_function_privilege(current_user,%s,'EXECUTE'),"
            "has_function_privilege(current_user,%s,'EXECUTE')",
            (v70_signature, v77_signature),
        ).fetchone()
    app_role = next(
        role for role in environment["roles"]
        if role.endswith(("control_app", "control_app_preview"))
    )
    denied_roles = tuple(role for role in environment["roles"] if role != app_role)
    with psycopg.connect(environment["admin"]) as connection:
        denied_privileges = tuple(
            connection.execute(
                "select has_function_privilege(%s,%s,'EXECUTE'),"
                "has_function_privilege(%s,%s,'EXECUTE')",
                (role, v70_signature, role, v77_signature),
            ).fetchone()
            for role in denied_roles
        )

    v70_probe = (
        "select platform_hr.create_candidate_analysis_v70("
        "null::uuid,null::uuid,null::uuid,null::uuid,null::uuid,null::text,"
        "null::uuid[],null::uuid[],null::jsonb,null::jsonb,null::jsonb,"
        "null::jsonb,null::jsonb,null::text,null::text)"
    )
    v77_probe = (
        "select platform_hr.create_candidate_analysis_v77("
        "null::uuid,null::uuid,null::uuid,null::uuid,null::uuid,"
        "'candidate_interview_plan',null::uuid[],null::uuid[],null::jsonb,"
        "null::jsonb,null::jsonb,null::jsonb,null::jsonb,null::text,null::text,"
        "null::uuid)"
    )
    for probe in (v70_probe, v77_probe):
        with (
            psycopg.connect(
                environment["urls"]["platform_brain_worker"], autocommit=True
            ) as connection,
            pytest.raises(psycopg.errors.InsufficientPrivilege),
        ):
            connection.execute(probe)
    with (
        psycopg.connect(
            environment["urls"]["platform_control_app"], autocommit=True
        ) as connection,
        pytest.raises(
            psycopg.errors.NoDataFound,
            match="candidate interview artifact required",
        ),
    ):
        connection.execute(v77_probe)

    assert column == ("YES",)
    assert foreign_key == ("platform_attachments.artifact_versions",)
    assert immutable_trigger == ("O",)
    assert app_privileges == (False, True)
    assert brain_privileges == (False, False)
    assert denied_privileges == ((False, False),) * len(denied_roles)
