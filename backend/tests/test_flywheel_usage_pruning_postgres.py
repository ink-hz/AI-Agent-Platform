"""Stats must not evaluate per-session detail lookups; disposable data only."""

import json
import subprocess
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row

from app.fleet.repository import USAGE_SQL, TREND_SQL
from test_daq_observability_postgres import apply, daq_database  # noqa: F401
from test_observability_migration_runner import observability_database  # noqa: F401

ROOT = Path(__file__).parents[2]
MIGRATION = ROOT / "backend/migrations/017_usage_projection_pruning.sql"
STATS = """select agent_id, count(*) as sessions, max(source_synced_at)
           from platform_read.sessions group by agent_id order by agent_id"""


def nodes(plan):
    yield plan
    for child in plan.get("Plans", []):
        yield from nodes(child)


def detail_reads(plan):
    return [n for n in nodes(plan)
            if n.get("Schema") == "platform_source_fae"
            and n.get("Relation Name") in {"chat_turns", "turn_feedback", "turn_reviews"}]


@pytest.mark.postgres
def test_stats_prune_unneeded_detail_without_changing_rows_oids_or_grants(daq_database, tmp_path):
    apply(daq_database, ROOT / "backend/migrations/015_daq_observability.sql")
    apply(daq_database, ROOT / "backend/migrations/016_voc_observability.sql")
    with psycopg.connect(daq_database, row_factory=dict_row) as connection:
        connection.execute("""insert into platform_source_fae.chat_turns
            (id,session_id,external_session_id,turn_index,trace_id,channel,question,answer,created_at,source_synced_at)
            select '11111111-1111-4111-8111-111111111111'::uuid,
                id,external_session_id,0,'usage-fixture','fae','first question','answer',now(),now()
            from platform_source_fae.chat_sessions limit 1""")
        connection.execute("""insert into platform_source_fae.chat_sessions
            (id,external_session_id,channel,created_at,last_active_at,source_synced_at)
            values ('22222222-2222-4222-8222-222222222222','usage:empty','fae',now(),now(),now())""")
        for index, days in enumerate((6, 7, 13, 14), 1):
            connection.execute("""insert into platform_source_fae.chat_turns
                (id,session_id,external_session_id,turn_index,trace_id,channel,question,answer,created_at,source_synced_at)
                select gen_random_uuid(),id,external_session_id,%s,'window-fixture','fae',
                    'older question','answer',now()-(%s * interval '1 day'),now()
                from platform_source_fae.chat_sessions where external_session_id='camera:existing'""", (index, days))
        connection.execute("""insert into platform_source_fae.chat_turns
            (id,session_id,external_session_id,turn_index,trace_id,channel,question,answer,created_at,source_synced_at)
            select gen_random_uuid(),id,external_session_id,5,'empty-fixture','fae',
                'unanswered question','',now(),now()
            from platform_source_fae.chat_sessions where external_session_id='camera:existing'""")
        for schema in ("platform_source_admin", "platform_source_daq", "platform_source_voc"):
            connection.execute(sql.SQL("""insert into {}.chat_sessions
                (id,external_session_id,channel,conversation_title,created_at,last_active_at,source_synced_at)
                values ('33333333-3333-4333-8333-333333333333',%s,'usage-test','other source',now(),now(),now())""").format(sql.Identifier(schema)), (schema,))
            connection.execute(sql.SQL("""insert into {}.chat_turns
                (id,session_id,external_session_id,turn_index,trace_id,channel,question,answer,created_at,source_synced_at)
                values (gen_random_uuid(),'33333333-3333-4333-8333-333333333333',%s,0,
                    'other-source-trace','usage-test','other source question','answer',now(),now())""").format(sql.Identifier(schema)), (schema,))
        connection.commit()
        before_stats = connection.execute(STATS).fetchall()
        before_sessions = connection.execute("select * from platform_read.sessions order by session_key").fetchall()
        before_usage = connection.execute(USAGE_SQL).fetchall()
        camera = next(row for row in before_usage if row["bot_id"] == "ai-fae-agent")
        assert (camera["total_conversations"], camera["conversations_last_7d"],
                camera["conversations_previous_7d"], camera["session_count"]) == (5, 2, 2, 2)
        assert camera["recent_summary"] == "first question"
        assert {"ai-admin-agent", "ai-daq-fae-agent", "voc"} <= {row["bot_id"] for row in before_usage}
        before_trend = connection.execute(TREND_SQL).fetchall()
        metadata_sql = """select oid,relname,relowner,relacl,reloptions from pg_class
            where relnamespace='platform_read'::regnamespace and relkind='v' order by oid"""
        before_metadata = connection.execute(metadata_sql).fetchall()
        old_plan = connection.execute("explain (verbose, format json) " + STATS).fetchone()["QUERY PLAN"][0]["Plan"]
        assert detail_reads(old_plan), "fixture must reproduce unused detail reads"

    assert MIGRATION.exists(), "usage projection repair migration is missing"
    credential = tmp_path / "owner-dsn"
    credential.write_text(daq_database + "\n")
    credential.chmod(0o600)
    for _ in range(2):
        result = subprocess.run([str(ROOT / "deploy/migrate-observability"), str(credential), str(MIGRATION)],
                                text=True, capture_output=True)
        assert result.returncode == 0, result.stderr
        assert "version=017" in result.stdout

    with psycopg.connect(daq_database, row_factory=dict_row) as connection:
        assert connection.execute(STATS).fetchall() == before_stats
        assert connection.execute("select * from platform_read.sessions order by session_key").fetchall() == before_sessions
        assert connection.execute(USAGE_SQL).fetchall() == before_usage
        assert connection.execute(TREND_SQL).fetchall() == before_trend
        assert connection.execute(metadata_sql).fetchall() == before_metadata
        new_plan = connection.execute("explain (verbose, format json) " + STATS).fetchone()["QUERY PLAN"][0]["Plan"]
        assert not detail_reads(new_plan), json.dumps(new_plan, default=str)
        assert connection.execute("""select count(*) as n from platform_sync.schema_migrations
            where version=17""").fetchone()["n"] == 1
        connection.execute("set local role flywheel_analyst")
        assert connection.execute(STATS).fetchall() == before_stats
        denied = connection.execute("""select has_schema_privilege(current_user,'platform_source_fae','USAGE') as raw_access""").fetchone()
        assert denied["raw_access"] is False
