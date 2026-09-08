import re
from pathlib import Path

MIGRATIONS = Path(__file__).parents[1] / "control_migrations"
MIGRATION = MIGRATIONS / "085_hr_intelligence_bundle_import.sql"


def _sql() -> str:
    assert MIGRATION.is_file(), f"missing migration: {MIGRATION}"
    return " ".join(MIGRATION.read_text(encoding="utf-8").lower().split())


def test_control_migrations_remain_contiguous_through_v87() -> None:
    versions = sorted(
        int(path.name.split("_", 1)[0]) for path in MIGRATIONS.glob("*.sql")
    )

    assert versions[:87] == list(range(1, 88))


def test_v85_defines_immutable_bundle_jobs_and_current_publication() -> None:
    sql = _sql()

    for table in (
        "intelligence_bundles",
        "intelligence_bundle_jobs",
        "intelligence_current_publication",
    ):
        assert f"create table platform_hr.{table}" in sql
    assert "create function platform_hr.import_intelligence_bundle_v85" in sql
    assert "create function platform_hr.read_current_intelligence_bundle_v85" in sql
    assert "pg_advisory_xact_lock" in sql
    assert "on conflict (workspace_key) do update" in sql
    assert "unique (owner_internal_user_id,manifest_sha256)" in sql


def test_v85_revokes_all_legacy_execution_functions() -> None:
    sql = _sql()

    for function in (
        "claim_next_panorama_run_v79",
        "read_panorama_run_runtime_v79",
        "transition_panorama_run_v79",
        "retry_panorama_analysis_v82",
    ):
        assert re.search(
            rf"revoke all on function platform_hr\.{function}\([^;]+\) from platform_control_app",
            sql,
        )
        assert re.search(
            rf"revoke all on function platform_hr\.{function}\([^;]+\) from platform_control_app_preview",
            sql,
        )


def test_v85_import_and_read_functions_are_role_restricted() -> None:
    sql = _sql()
    functions = set(re.findall(r"create function platform_hr\.([a-z0-9_]+_v85)", sql))

    assert functions
    for function in functions:
        assert f"revoke all on function platform_hr.{function}" in sql
        assert f"grant execute on function platform_hr.{function}" in sql
    assert "revoke all on platform_hr.intelligence_bundles" in sql
