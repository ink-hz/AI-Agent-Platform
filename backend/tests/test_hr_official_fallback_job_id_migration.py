from pathlib import Path


MIGRATION = (
    Path(__file__).parents[1]
    / "control_migrations"
    / "074_hr_official_fallback_job_ids.sql"
)


def test_migration_accepts_only_published_j_or_jobad_identifiers() -> None:
    assert MIGRATION.is_file(), f"missing migration: {MIGRATION}"
    sql = " ".join(MIGRATION.read_text(encoding="utf-8").lower().split())

    assert "alter table platform_hr.positions" in sql
    assert "alter table platform_hr.official_position_versions" in sql
    assert "jobad:[0-9]" in sql
    assert "validate constraint" in sql
