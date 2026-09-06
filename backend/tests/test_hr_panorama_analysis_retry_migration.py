from pathlib import Path

MIGRATIONS = Path(__file__).parents[1] / "control_migrations"
MIGRATION = MIGRATIONS / "082_hr_panorama_analysis_retry.sql"


def test_v82_is_the_contiguous_analysis_retry_migration() -> None:
    versions = sorted(
        int(path.name.split("_", 1)[0]) for path in MIGRATIONS.glob("*.sql")
    )

    assert versions[-4:] == [82, 83, 84, 85]
    assert versions == list(range(1, 86))


def test_v82_allows_only_safe_failed_analysis_retry() -> None:
    sql = " ".join(MIGRATION.read_text("utf-8").lower().split())

    assert "create function platform_hr.retry_panorama_analysis_v82" in sql
    assert "current_batch.state<>'failed'" in sql
    assert "current_batch.error_code<>'analysis_failed'" in sql
    assert "not exists" in sql
    assert "platform_hr.panorama_publications" in sql
    assert "platform_hr.talent_insight_versions" in sql
    assert "state='analyzing'" in sql
    assert "error_code=null" in sql
    assert "finished_at=null" in sql
    assert "row_version=batch.row_version+1" in sql
    assert "revoke all on function platform_hr.retry_panorama_analysis_v82" in sql
    assert "grant execute on function platform_hr.retry_panorama_analysis_v82" in sql
