from __future__ import annotations

from pathlib import Path

MIGRATION = (
    Path(__file__).parents[1]
    / "control_migrations"
    / "068_hr_position_intelligence.sql"
)


def _sql() -> str:
    assert MIGRATION.is_file(), f"missing migration: {MIGRATION}"
    return " ".join(MIGRATION.read_text(encoding="utf-8").lower().split())


def test_migration_defines_versioned_position_intelligence_spine() -> None:
    sql = _sql()

    for table in (
        "official_position_versions",
        "position_context_versions",
        "position_task_records",
    ):
        assert f"create table platform_hr.{table}" in sql
    assert "current_official_version_id uuid" in sql
    assert "current_context_version_id uuid" in sql
    assert "unique (official_position_version_id,owner_internal_user_id)" in sql
    assert "unique (context_version_id,owner_internal_user_id)" in sql
    assert "foreign key (position_id,owner_internal_user_id) references platform_hr.positions" in sql


def test_confirmed_context_is_immutable_and_one_current_version_per_position() -> None:
    sql = _sql()

    assert "create unique index one_current_confirmed_context_v68" in sql
    assert "where state='confirmed'" in sql
    assert "create function platform_hr.guard_context_version_immutability_v68" in sql
    assert "create trigger guard_context_version_immutability_v68" in sql
    assert "confirmed context version is immutable" in sql
    assert "revoke all on all tables in schema platform_hr from public" in sql
    assert "grant select on all tables in schema platform_hr" in sql
    assert "grant insert" not in sql
    assert "grant update" not in sql
    assert "grant delete" not in sql


def test_migration_exposes_only_bounded_idempotent_mutations() -> None:
    sql = _sql()

    for function in (
        "project_official_version_v68",
        "create_context_draft_v68",
        "confirm_context_modules_v68",
        "create_position_task_record_v68",
    ):
        assert f"create function platform_hr.{function}" in sql
        assert f"revoke all on function platform_hr.{function}" in sql
        assert f"grant execute on function platform_hr.{function}" in sql
    assert sql.count("security definer") >= 5
    assert "context baseline conflict" in sql
    assert "idempotency payload mismatch" in sql


def test_task_records_pin_exact_envelope_identity_without_ats_fields() -> None:
    sql = _sql()

    for column in (
        "official_position_version_id uuid",
        "context_version_id uuid",
        "material_attachment_ids uuid[]",
        "document_attachment_ids uuid[]",
        "human_feedback_ids uuid[]",
        "canonical_sha256 text",
    ):
        assert column in sql
    for forbidden in (
        "application_stage",
        "offer_status",
        "interview_schedule",
        "onboarding",
        "boss_zhipin",
        "liepin",
        "beisen",
    ):
        assert forbidden not in sql
