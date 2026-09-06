from pathlib import Path

MIGRATIONS = Path(__file__).parents[1] / "control_migrations"
MIGRATION = MIGRATIONS / "083_hr_panorama_link_capacity.sql"


def test_v83_is_the_contiguous_link_capacity_migration() -> None:
    versions = sorted(
        int(path.name.split("_", 1)[0]) for path in MIGRATIONS.glob("*.sql")
    )

    assert versions[-2:] == [83, 84]
    assert versions == list(range(1, 85))


def test_v83_expands_the_snapshot_link_ordinal_without_mutating_history() -> None:
    sql = " ".join(MIGRATION.read_text("utf-8").lower().split())

    assert "alter table platform_hr.talent_insight_snapshots" in sql
    assert "drop constraint talent_insight_snapshots_snapshot_ordinal_check" in sql
    assert "snapshot_ordinal between 1 and 10000" in sql
