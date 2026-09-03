from __future__ import annotations

from pathlib import Path


MIGRATION = (
    Path(__file__).parents[1]
    / "control_migrations"
    / "065_hr_position_spine.sql"
)


def _sql() -> str:
    assert MIGRATION.is_file(), f"missing migration: {MIGRATION}"
    return " ".join(MIGRATION.read_text(encoding="utf-8").lower().split())


def test_hr_position_migration_defines_owned_position_spine() -> None:
    sql = _sql()

    assert "create schema platform_hr authorization current_user" in sql
    for table in (
        "positions",
        "position_drafts",
        "position_conversations",
        "position_materials",
        "position_artifacts",
        "position_import_evidence",
    ):
        assert f"create table platform_hr.{table}" in sql
    assert "position_id uuid primary key" in sql
    assert "unique (position_id,owner_internal_user_id)" in sql
    assert "source_kind in ('official_site','manual')" in sql
    assert "internal_status in ('draft','active','archived')" in sql
    assert "official_status in ('active','stale','suspected_inactive','inactive')" in sql


def test_hr_position_migration_enforces_draft_and_single_conversation_binding() -> None:
    sql = _sql()

    assert "state in ('proposed','confirmed','merged','dismissed')" in sql
    assert "source_kind in ('historical_conversation','new_conversation')" in sql
    assert "conversation_id uuid primary key" in sql
    assert "references platform_control.conversations(conversation_id)" in sql
    assert "foreign key (position_id,owner_internal_user_id) references platform_hr.positions" in sql
    assert "foreign key (conversation_id,owner_internal_user_id) references platform_control.conversations" in sql


def test_hr_position_migration_references_attachment_and_artifact_authorities() -> None:
    sql = _sql()

    assert "references platform_attachments.attachments" in sql
    assert "references platform_attachments.artifacts(artifact_id)" in sql
    assert "active boolean not null default true" in sql
    assert "unique (position_id,attachment_id)" in sql
    assert "unique (position_id,artifact_id)" in sql


def test_hr_position_migration_has_audited_idempotent_mutation_functions() -> None:
    sql = _sql()

    for function in (
        "create_position_v65",
        "confirm_position_draft_v65",
        "bind_conversation_v65",
        "promote_material_v65",
        "link_artifact_v65",
    ):
        assert f"create function platform_hr.{function}" in sql
        assert f"revoke all on function platform_hr.{function}" in sql
        assert f"grant execute on function platform_hr.{function}" in sql
    assert sql.count("security definer") >= 5
    assert "client_request_id uuid" in sql
    assert "row_version bigint not null default 1" in sql


def test_hr_position_migration_is_private_and_has_no_ats_or_external_fields() -> None:
    sql = _sql()

    assert "revoke all on schema platform_hr from public" in sql
    assert "revoke all on all tables in schema platform_hr from public" in sql
    for forbidden in (
        "beisen",
        "offer_status",
        "onboarding",
        "interview_schedule",
        "application_stage",
        "boss_zhipin",
        "liepin",
    ):
        assert forbidden not in sql
