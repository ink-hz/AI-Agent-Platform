from __future__ import annotations

import re
from pathlib import Path

MIGRATION = (
    Path(__file__).parents[1]
    / "control_migrations"
    / "076_hr_position_packages.sql"
)


def _sql() -> str:
    assert MIGRATION.is_file(), f"missing migration: {MIGRATION}"
    return " ".join(MIGRATION.read_text(encoding="utf-8").lower().split())


def _migration_objects(sql: str) -> set[str]:
    return set(re.findall(
        r"create (?:table|function) platform_hr\.([a-z0-9_]+)", sql
    ))


def test_migration_defines_immutable_versioned_position_packages() -> None:
    sql = _sql()

    assert {
        "position_draft_versions",
        "create_position_draft_version_v76",
        "confirm_position_package_v76",
    } <= _migration_objects(sql)
    for column in (
        "draft_version_id uuid primary key",
        "owner_internal_user_id uuid not null",
        "draft_id uuid not null",
        "client_request_id uuid not null",
        "version_number integer not null",
        "title text not null",
        "modules jsonb not null",
        "source_conversation_id uuid not null",
        "source_turn_id uuid not null",
        "source_assistant_message_id uuid not null",
        "agent_id text not null",
        "model_version text not null",
        "row_version bigint not null default 1",
        "created_at timestamptz not null default now()",
        "updated_at timestamptz not null default now()",
    ):
        assert column in sql
    assert "guard_position_draft_version_immutability_v76" in sql
    assert "position draft version is immutable" in sql


def test_migration_requires_exact_modules_and_scoped_source_identity() -> None:
    sql = _sql()

    assert "modules ?& array['mission','jd','jr']" in sql
    assert "modules-'mission'-'jd'-'jr'='{}'::jsonb" in sql
    assert (
        "unique (owner_internal_user_id,draft_id,source_assistant_message_id)"
        in sql
    )
    assert (
        "foreign key (draft_id,owner_internal_user_id) references "
        "platform_hr.position_drafts"
    ) in sql
    assert (
        "foreign key (source_conversation_id,owner_internal_user_id) references "
        "platform_control.conversations"
    ) in sql
    assert (
        "foreign key (source_conversation_id,source_turn_id) references "
        "platform_control.conversation_turns"
    ) in sql
    assert (
        "foreign key (source_conversation_id,source_assistant_message_id) "
        "references platform_control.conversation_messages"
    ) in sql


def test_migration_exposes_app_only_idempotent_package_mutations() -> None:
    sql = _sql()

    for function in (
        "create_position_draft_version_v76",
        "confirm_position_package_v76",
    ):
        assert f"revoke all on function platform_hr.{function}" in sql
        assert f"grant execute on function platform_hr.{function}" in sql
    assert "platform_brain_worker" not in sql
    assert sql.count("security definer") >= 2
    assert "position draft version idempotency payload mismatch" in sql
    assert "position package confirmation payload mismatch" in sql
    assert "for update" in sql
    assert "'manual'" in sql
    assert "'active'" in sql
    assert "'confirmed'" in sql
    assert "'draft_confirmed'" in sql
