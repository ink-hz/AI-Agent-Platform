from pathlib import Path


MIGRATION = Path(__file__).parents[1] / "migrations/004_session_message_timestamps.sql"


def sql_text() -> str:
    return MIGRATION.read_text(encoding="utf-8").lower()


def test_mirror_tables_accept_source_message_timestamps():
    sql = sql_text()
    assert "alter table platform_source_fae.chat_turns" in sql
    assert "alter table platform_source_admin.chat_turns" in sql
    assert sql.count("add column if not exists question_at timestamptz") == 2
    assert sql.count("add column if not exists answer_at timestamptz") == 2


def test_metabot_view_keeps_separate_role_times():
    sql = sql_text()
    assert "min(m.occurred_at) filter (where m.role = 'user') as question_at" in sql
    assert "max(m.occurred_at) filter (where m.role = 'assistant') as answer_at" in sql


def test_legacy_remote_times_are_explicitly_estimated():
    sql = sql_text()
    assert "t.created_at - (t.duration_ms * interval '1 millisecond')" in sql
    assert "'estimated'::text" in sql
    assert "'unavailable'::text" in sql


def test_read_privileges_survive_view_replacement():
    sql = sql_text()
    assert "alter view platform_read.turns owner to flywheel_owner" in sql
    assert "grant select on platform_read.turns to flywheel_analyst" in sql
