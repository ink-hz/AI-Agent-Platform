"""Scope proofs must scan normalized identity views once per export query."""

from datetime import UTC, datetime
from uuid import UUID

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.cloud_replica.source import REVIEW_ISSUE_SQL
from test_observability_migration_runner import observability_database  # noqa: F401


@pytest.fixture(scope="module")
def scope_database(observability_database):
    with psycopg.connect(observability_database) as c:
        c.execute("create schema platform_read; create schema platform_review")
        c.execute("""
            create table platform_read.raw_turns (
                turn_key text, agent_id text, source_kind text);
            create table platform_read.raw_feedback (
                feedback_key text, agent_id text, turn_key text);
            create view platform_read.turns as
                select *, row_number() over (order by turn_key) as turn_index
                from platform_read.raw_turns;
            create view platform_read.feedback as
                select * from platform_read.raw_feedback;
            create table platform_review.feedback_issues (
                id uuid primary key, agent_id text, disposition text default 'open',
                priority text default 'normal', title text, failure_layer text,
                owner text, created_at timestamptz default now(),
                updated_at timestamptz default now(), origin_turn_key text,
                secondary_layers text[] default '{}', root_cause text,
                impact_scope text, fix_ready_at timestamptz,
                canonical_issue_id uuid, disposition_reason text,
                row_version int default 1);
            create table platform_review.feedback_issue_links (
                id uuid primary key, issue_id uuid, agent_id text,
                source_turn_key text, source_feedback_keys text[], active boolean);
            create table platform_review.feedback_replay_runs (
                issue_id uuid, issue_link_id uuid);
            create table platform_review.feedback_issue_events (
                issue_id uuid, event_type text, before jsonb, after jsonb,
                actor text, reason text);
        """)
        c.execute("""insert into platform_read.raw_turns values
            ('fae:good','ai-fae-agent','fae'),
            ('daq:other','ai-daq-fae-agent','fae'),
            ('admin:wrong-family','ai-fae-agent','admin');
            insert into platform_read.raw_feedback values
            ('feedback:good','ai-fae-agent','fae:good'),
            ('feedback:other','ai-daq-fae-agent','daq:other');
        """)
        cases = (
            ("valid", "fae:good"),
            ("missing_origin", "fae:missing"),
            ("cross_agent_origin", "daq:other"),
            ("wrong_family", "admin:wrong-family"),
            ("cross_agent_link", "fae:good"),
            ("cross_agent_feedback", "fae:good"),
            ("duplicate_feedback", "fae:good"),
            ("missing_replay_link", "fae:good"),
            ("bad_history", "fae:good"),
            ("unpaired_merge", "fae:good"),
            ("cycle_a", "fae:good"),
            ("cycle_b", "fae:good"),
        )
        ids = {name: UUID(int=i) for i, (name, _) in enumerate(cases, 1)}
        for name, origin in cases:
            c.execute("""insert into platform_review.feedback_issues
                (id,agent_id,title,origin_turn_key) values (%s,'ai-fae-agent',%s,%s)
            """, (ids[name], name, origin))
        for name, agent, feedback in (
            ("valid", "ai-fae-agent", ["feedback:good"]),
            ("cross_agent_link", "ai-daq-fae-agent", ["feedback:good"]),
            ("cross_agent_feedback", "ai-fae-agent", ["feedback:other"]),
            ("duplicate_feedback", "ai-fae-agent", ["feedback:good"] * 2),
        ):
            c.execute("""insert into platform_review.feedback_issue_links values
                (%s,%s,%s,'fae:good',%s,true)
            """, (ids[name], ids[name], agent, feedback))
        c.execute("insert into platform_review.feedback_replay_runs values (%s,%s)",
                  (ids["missing_replay_link"], UUID(int=999)))
        c.execute("""insert into platform_review.feedback_issue_events
            (issue_id,event_type,before,after) values (%s,'turn_linked','{}',%s)
        """, (ids["valid"], Jsonb({
            "id": str(ids["valid"]), "issue_id": str(ids["valid"]),
            "agent_id": "ai-fae-agent", "source_turn_key": "fae:good",
            "source_feedback_keys": ["feedback:good"],
        })))
        c.execute("""insert into platform_review.feedback_issue_events
            (issue_id,event_type,before,after) values (%s,'turn_linked','{}',%s)
        """, (ids["bad_history"], Jsonb({"agent_id": "ai-daq-fae-agent"})))
        for name, target in (("unpaired_merge", "valid"),
                             ("cycle_a", "cycle_b"), ("cycle_b", "cycle_a")):
            c.execute("""update platform_review.feedback_issues
                set canonical_issue_id=%s where id=%s""", (ids[target], ids[name]))
    return observability_database


@pytest.mark.postgres
def test_scope_query_preserves_fail_closed_proofs(scope_database):
    with psycopg.connect(scope_database, row_factory=dict_row) as c:
        c.execute("set transaction read only")
        rows = c.execute(REVIEW_ISSUE_SQL, {"through": datetime.now(UTC)}).fetchall()
    assert len(rows) == 12
    assert {row["title"] for row in rows if row["scope_valid"]} == {"valid"}


@pytest.mark.postgres
def test_scope_query_reads_each_identity_view_once(scope_database):
    with psycopg.connect(scope_database) as c:
        c.execute("set transaction read only")
        plan = c.execute("explain (analyze, format json, timing off) " + REVIEW_ISSUE_SQL,
                         {"through": datetime.now(UTC)}).fetchone()[0][0]["Plan"]

    scans = {"raw_turns": 0, "raw_feedback": 0}

    def visit(node):
        relation = node.get("Relation Name")
        if relation in scans:
            scans[relation] += node["Actual Loops"]
        for child in node.get("Plans", []):
            visit(child)

    visit(plan)
    assert scans == {"raw_turns": 1, "raw_feedback": 1}
