from pathlib import Path


MIGRATION = Path(__file__).parents[1] / "migrations/006_manual_user_names.sql"


def migration_sql() -> str:
    return MIGRATION.read_text(encoding="utf-8").lower()


def test_migration_wraps_all_metabot_sessions_and_turns() -> None:
    sql = migration_sql()
    assert "alter view platform_read.sessions rename to sessions_raw_identity" in sql
    assert "alter view platform_read.turns rename to turns_raw_identity" in sql
    assert "create or replace view platform_read.sessions as" in sql
    assert "create or replace view platform_read.turns as" in sql
    assert sql.count("flywheel_identity.resolved_user_names") == 2
    assert "latest_sender" in sql
    assert "turn_sender" in sql


def test_migration_never_exposes_union_id_fallbacks() -> None:
    sql = migration_sql()
    assert sql.count("name_source in ('manual', 'feishu')") == 2
    assert "preferred_name" in sql


def test_migration_preserves_non_metabot_and_timestamp_contracts() -> None:
    sql = migration_sql()
    assert sql.count("source_kind = 'metabot'") >= 4
    for column in (
        "question_at",
        "answer_at",
        "question_time_status",
        "answer_time_status",
    ):
        assert column in sql


def test_migration_restores_owner_and_read_permissions() -> None:
    sql = migration_sql()
    assert "alter view platform_read.sessions owner to flywheel_owner" in sql
    assert "alter view platform_read.turns owner to flywheel_owner" in sql
    assert (
        "grant select on platform_read.sessions, platform_read.turns "
        "to flywheel_analyst"
    ) in sql
    assert "revoke all on platform_read.sessions_raw_identity" in sql
    assert "platform_read.turns_raw_identity" in sql


def test_migration_switches_both_views_in_one_transaction() -> None:
    sql = migration_sql()
    assert sql.startswith("\\set on_error_stop on\n\nbegin;")
    assert sql.rstrip().endswith("commit;")
