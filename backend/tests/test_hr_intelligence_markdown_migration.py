from pathlib import Path

MIGRATION = (
    Path(__file__).parents[1]
    / "control_migrations/087_hr_intelligence_markdown_context.sql"
)


def test_v87_imports_v2_chunk_index_but_exposes_no_execution_surface() -> None:
    sql = MIGRATION.read_text("utf-8").lower()

    assert "agent_chunk_index jsonb" in sql
    assert "agent_document_index jsonb" in sql
    assert "conversation_intelligence_bundle_references" in sql
    assert "import_intelligence_bundle_v87" in sql
    assert "read_intelligence_bundle_chunks_v87" in sql
    assert "create_conversation_intelligence_reference_v87" in sql
    for forbidden in ("collect", "analyze", "model_secret", "http", "scheduler"):
        assert forbidden not in sql


def test_v87_keeps_existing_v1_rows_and_restricts_all_new_surfaces() -> None:
    sql = MIGRATION.read_text("utf-8").lower()

    assert "schema_version in (1,2)" in sql
    assert "default '[]'::jsonb" in sql
    assert "on delete restrict" in sql
    assert "revoke all on platform_hr.conversation_intelligence_bundle_references" in sql
    assert "grant execute on function platform_hr.import_intelligence_bundle_v87" in sql
