from __future__ import annotations

import re
from pathlib import Path

MIGRATIONS = Path(__file__).parents[1] / "control_migrations"
MIGRATION = MIGRATIONS / "080_hr_panorama_publication.sql"


def _sql() -> str:
    assert MIGRATION.is_file(), f"missing migration: {MIGRATION}"
    return " ".join(MIGRATION.read_text(encoding="utf-8").lower().split())


def test_v80_is_the_contiguous_publication_migration() -> None:
    versions = sorted(
        int(path.name.split("_", 1)[0]) for path in MIGRATIONS.glob("*.sql")
    )

    assert versions[-6:] == [82, 83, 84, 85, 86, 87]
    assert versions == list(range(1, 88))


def test_v80_defines_background_batches_attempts_and_atomic_publication() -> None:
    sql = _sql()

    for table in (
        "panorama_production_batches",
        "panorama_source_attempts",
        "panorama_publications",
        "panorama_current_publications",
    ):
        assert f"create table platform_hr.{table}" in sql
    assert "create function platform_hr.create_panorama_production_batch_v80" in sql
    assert "create function platform_hr.record_panorama_source_attempt_v80" in sql
    assert "create function platform_hr.publish_panorama_version_v80" in sql
    assert "create function platform_hr.read_current_panorama_publication_v80" in sql
    assert "create function platform_hr.create_production_job_snapshot_v80" in sql
    assert "create function platform_hr.create_production_insight_v80" in sql
    assert "cardinality(selected_snapshot_ids) not between 1 and 1000" in sql
    assert "pg_advisory_xact_lock" in sql
    assert "workspace_key text primary key" in sql
    assert "coverage_state text not null" in sql
    assert "source_coverage jsonb not null" in sql


def test_v80_decouples_snapshots_and_insights_from_conversations() -> None:
    sql = _sql()

    assert (
        "alter table platform_hr.public_job_snapshots add column production_batch_id uuid"
        in sql
    )
    assert (
        "alter table platform_hr.public_job_snapshots alter column run_id drop not null"
        in sql
    )
    assert (
        "alter table platform_hr.talent_insight_versions add column production_batch_id uuid"
        in sql
    )
    assert (
        "alter table platform_hr.talent_insight_versions alter column run_id drop not null"
        in sql
    )
    assert (
        "alter table platform_hr.talent_insight_versions alter column source_conversation_id drop not null"
        in sql
    )
    assert (
        "alter table platform_hr.talent_insight_versions alter column source_turn_id drop not null"
        in sql
    )
    assert re.search(
        r"check \(\(run_id is not null\).*\(production_batch_id is not null\)\)",
        sql,
    )


def test_v80_evidence_metadata_is_bounded_and_credentials_are_not_stored() -> None:
    sql = _sql()

    for column in (
        "evidence_sha256 text",
        "evidence_locator text",
        "evidence_mime text",
        "evidence_size_bytes bigint",
        "normalized_job_count integer not null",
    ):
        assert column in sql
    assert "evidence_sha256 ~ '^[a-f0-9]{64}$'" in sql
    assert "evidence_size_bytes between 0 and 10485760" in sql
    assert "failed responses may retain raw evidence" in sql
    for forbidden in ("authorization", "cookie", "access_token", "api_key", "secret"):
        assert f"{forbidden} text" not in sql


def test_v80_functions_are_restricted_to_application_roles() -> None:
    sql = _sql()
    functions = set(re.findall(r"create function platform_hr\.([a-z0-9_]+_v80)", sql))

    assert functions
    for function in functions:
        assert f"revoke all on function platform_hr.{function}" in sql
        assert f"grant execute on function platform_hr.{function}" in sql
    assert sql.count(
        "session_user not in ('platform_control_app','platform_control_app_preview')"
    ) >= len(functions)
