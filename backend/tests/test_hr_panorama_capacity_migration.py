from pathlib import Path

MIGRATIONS = Path(__file__).parents[1] / "control_migrations"
MIGRATION = MIGRATIONS / "081_hr_panorama_report_capacity.sql"


def _sql() -> str:
    assert MIGRATION.is_file(), f"missing migration: {MIGRATION}"
    return " ".join(MIGRATION.read_text(encoding="utf-8").lower().split())


def test_v81_is_the_contiguous_large_report_capacity_migration() -> None:
    versions = sorted(
        int(path.name.split("_", 1)[0]) for path in MIGRATIONS.glob("*.sql")
    )

    assert versions[-5:] == [82, 83, 84, 85, 86]
    assert versions == list(range(1, 87))


def test_v81_preserves_old_migrations_and_adds_a_bounded_production_entrypoint() -> None:
    sql = _sql()

    assert "drop constraint talent_insight_versions_snapshot_ids_check" in sql
    assert "cardinality(snapshot_ids) between 1 and 10000" in sql
    assert "create function platform_hr.create_production_insight_v81" in sql
    assert "cardinality(selected_snapshot_ids) not between 1 and 10000" in sql
    assert "revoke all on function platform_hr.create_production_insight_v81" in sql
    assert "grant execute on function platform_hr.create_production_insight_v81" in sql
