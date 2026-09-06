from pathlib import Path

MIGRATIONS = Path(__file__).parents[1] / "control_migrations"
MIGRATION = MIGRATIONS / "086_hr_intelligence_task_context.sql"


def test_v86_pins_one_immutable_bundle_context_per_position_turn() -> None:
    sql = MIGRATION.read_text("utf-8").lower()
    assert "create table platform_hr.position_intelligence_bundle_references" in sql
    assert "bundle_id uuid not null" in sql
    assert "referencesplatform_hr.intelligence_bundles(bundle_id,owner_internal_user_id)" in sql.replace("\n", "").replace(" ", "")
    assert "unique(owner_internal_user_id,position_id,turn_id)" in sql.replace(" ", "")
    assert "create_intelligence_bundle_reference_v86" in sql
    assert "read_intelligence_bundle_reference_for_turn_v86" in sql
    assert "before update or delete" in sql


def test_v86_has_no_collection_analysis_or_model_execution_function() -> None:
    sql = MIGRATION.read_text("utf-8").lower()
    for forbidden in ("collect", "analyze", "provider", "model_secret", "http"):
        assert forbidden not in sql
