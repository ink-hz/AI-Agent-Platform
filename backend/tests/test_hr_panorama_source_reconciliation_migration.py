from pathlib import Path

MIGRATIONS = Path(__file__).parents[1] / "control_migrations"
MIGRATION = MIGRATIONS / "084_hr_panorama_source_reconciliation.sql"


def _sql() -> str:
    assert MIGRATION.is_file(), f"missing migration: {MIGRATION}"
    return " ".join(MIGRATION.read_text("utf-8").lower().split())


def test_v84_is_the_contiguous_source_reconciliation_migration() -> None:
    versions = sorted(
        int(path.name.split("_", 1)[0]) for path in MIGRATIONS.glob("*.sql")
    )

    assert versions[-1] == 85
    assert versions == list(range(1, 86))


def test_v84_reconciles_only_the_same_catalog_source_identity() -> None:
    sql = _sql()

    assert "create function platform_hr.reconcile_talent_source_v84" in sql
    assert "pg_advisory_xact_lock" in sql
    assert "selected.source_id<>selected_source_id" in sql
    assert "source.company_key=btrim(selected_company_key)" in sql
    assert "approved_public_urls=selected_approved_public_urls" in sql
    assert "updated_at=now()" in sql
    assert "session_user not in ('platform_control_app','platform_control_app_preview')" in sql
    assert "revoke all on function platform_hr.reconcile_talent_source_v84" in sql
    assert "grant execute on function platform_hr.reconcile_talent_source_v84" in sql
