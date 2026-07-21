from pathlib import Path


MIGRATION = (
    Path(__file__).parents[1] / "migrations/001_observability_sources.sql"
)


def migration_sql() -> str:
    return MIGRATION.read_text(encoding="utf-8").lower()


def test_migration_creates_isolated_schemas() -> None:
    sql = migration_sql()
    for schema in (
        "platform_source_fae",
        "platform_source_admin",
        "platform_sync",
        "platform_read",
    ):
        assert f"create schema if not exists {schema}" in sql


def test_migration_creates_sync_writer_without_broad_privileges() -> None:
    sql = migration_sql()
    assert "create role platform_sync_writer" in sql
    assert "revoke all on schema platform_source_fae" in sql
    assert "revoke all on schema platform_source_admin" in sql
    assert "revoke all on schema platform_sync" in sql
    assert "grant usage on schema platform_read to flywheel_analyst" in sql
    assert "grant select on all tables in schema platform_read to flywheel_analyst" in sql


def test_fae_span_deduplication_key_is_present() -> None:
    sql = migration_sql()
    assert "primary key (trace_id, span_id)" in sql


def test_mirror_tables_preserve_sync_timestamp_and_details() -> None:
    sql = migration_sql()
    assert "source_synced_at timestamptz not null" in sql
    assert "details jsonb not null default '{}'::jsonb" in sql


def test_sync_runs_capture_validation_and_last_good_state() -> None:
    sql = migration_sql()
    assert "create table if not exists platform_sync.runs" in sql
    assert "validation jsonb not null default '{}'::jsonb" in sql
    assert "error_summary text" in sql


def test_metabot_views_apply_current_catalog_aliases_and_exclusions() -> None:
    sql = migration_sql()
    assert "when c.bot_id = 'marketing-bot' then 'marketing-prospecting-bot'" in sql
    assert "c.bot_id not in ('pc-bot', 'quality-bot')" in sql


def test_conversation_counts_require_a_non_empty_answer() -> None:
    sql = migration_sql()
    assert "m.role = 'assistant' and nullif(btrim(m.content), '') is not null" in sql
    assert "where t.session_id = s.id and nullif(btrim(t.answer), '') is not null" in sql
