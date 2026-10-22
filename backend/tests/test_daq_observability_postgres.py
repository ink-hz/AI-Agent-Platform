"""Producer → isolated mirror → normalized repository integration; synthetic data only."""

import io
import os
import subprocess
import sys
import tempfile
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote

import psycopg
import pytest
from app.cloud_replica.source import ATTACHMENT_SQL
from app.observability.repository import PsycopgObservabilityRepository
from app.sync_remote.config import default_sources
from app.sync_remote.export import build_remote_program, parse_export
from app.sync_remote.importer import import_bundle
from psycopg import sql
from test_daq_observability import NOW, OWNER, ROOT, SID, TID
from test_observability_migration_runner import (
    observability_database,  # noqa: F401 -- pytest fixture dependency
)

MIGRATION = ROOT / "backend/migrations/015_daq_observability.sql"


def apply(dsn, path):
    result = subprocess.run(
        ["psql", "-X", "-v", "ON_ERROR_STOP=1", dsn, "-f", str(path)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.fixture(scope="module")
def daq_database(request):
    dsn = request.getfixturevalue("observability_database")
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute("create role flywheel_analyst nologin")
        connection.execute("create role flywheel_ingest nologin")
        connection.execute("create role platform_review_writer nologin")
        for schema in ("flywheel_analytics", "flywheel_core", "flywheel_identity"):
            connection.execute(
                sql.SQL("create schema {} authorization flywheel_owner").format(
                    sql.Identifier(schema)
                )
            )
        connection.execute("""create table flywheel_analytics.conversations (
            id uuid primary key, bot_id text, platform text, platform_conversation_id text,
            created_at timestamptz, updated_at timestamptz, business_domain text,
            conversation_type text, is_synthetic boolean default false)""")
        connection.execute("""create table flywheel_analytics.messages (
            id uuid, conversation_id uuid, turn_id uuid, role text, content text,
            occurred_at timestamptz, sender_user_id uuid, is_synthetic boolean default false)""")
        connection.execute("""create table flywheel_analytics.runs (
            id uuid, bot_id text, turn_id uuid, status text, started_at timestamptz,
            completed_at timestamptz, duration_ms bigint, engine text, backend text,
            model text, input_tokens bigint, output_tokens bigint, cost_usd numeric,
            error_class text, error_message text, retry_count integer,
            agent_version text, prompt_version text, knowledge_version text,
            is_synthetic boolean default false)""")
        connection.execute("""create table flywheel_analytics.events (
            id uuid, bot_id text, run_id uuid, event_type text, payload jsonb,
            seq bigint, occurred_at timestamptz, is_synthetic boolean default false)""")
        connection.execute("""create table flywheel_core.feedback (
            id uuid, bot_id text, conversation_id uuid, turn_id uuid, kind text,
            payload jsonb, raw_text text, occurred_at timestamptz,
            is_synthetic boolean default false)""")
        connection.execute("""create table flywheel_identity.external_identities (
            user_id uuid, display_name text, department text, subject_kind text, provider text)""")
        connection.execute("""create table flywheel_identity.resolved_user_names (
            user_id uuid, name_source text, preferred_name text)""")
        for schema in ("flywheel_analytics", "flywheel_core", "flywheel_identity"):
            connection.execute(
                sql.SQL(
                    "grant select on all tables in schema {} to flywheel_owner"
                ).format(sql.Identifier(schema))
            )
    for name in (
        "001_observability_sources.sql",
        "002_sender_identity_views.sql",
        "004_session_message_timestamps.sql",
        "006_manual_user_names.sql",
        "009_feedback_review_grants.sql",
        "011_admin_session_subject_links.sql",
        "014_admin_verified_sender_view.sql",
    ):
        path = ROOT / "backend/migrations" / name
        if name == "014_admin_verified_sender_view.sql":
            with tempfile.TemporaryDirectory(prefix="daq-ledger-") as directory:
                credential = Path(directory) / "dsn"
                credential.write_text(dsn + "\n")
                credential.chmod(0o600)
                result = subprocess.run(
                    [
                        str(ROOT / "deploy/migrate-observability"),
                        str(credential),
                        str(path),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                assert result.returncode == 0, result.stderr
        else:
            apply(dsn, path)
    # The real migration must preserve a current camera record and its UUID.
    with psycopg.connect(dsn) as connection:
        connection.execute(
            """insert into platform_source_fae.chat_sessions
            (id, external_session_id, channel, created_at, last_active_at, source_synced_at)
            values (%s, 'camera:existing', 'fae', %s, %s, %s)""",
            (SID, NOW, NOW, NOW),
        )
    yield dsn


@pytest.mark.postgres
def test_daq_migration_import_repository_and_replica_boundary(daq_database, tmp_path):
    dsn = daq_database
    assert MIGRATION.exists(), "DAQ mirror migration is missing"
    dsn_file = tmp_path / "owner-dsn"
    dsn_file.write_text(dsn + "\n")
    dsn_file.chmod(0o600)
    for _ in range(2):
        migration = subprocess.run(
            [str(ROOT / "deploy/migrate-observability"), str(dsn_file), str(MIGRATION)],
            text=True,
            capture_output=True,
            check=False,
        )
        assert migration.returncode == 0, migration.stderr
        assert migration.stdout.startswith("OBSERVABILITY_MIGRATION_OK version=015")
        assert dsn not in migration.stdout + migration.stderr
    repeated = subprocess.run(
        [
            str(ROOT / "deploy/migrate-observability"),
            str(dsn_file),
            str(ROOT / "backend/migrations/014_admin_verified_sender_view.sql"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert repeated.returncode == 0, repeated.stderr
    with psycopg.connect(dsn) as connection:
        # A successful rerun must verify the retained Admin identity dependency.
        assert connection.execute(
            "select position('session_subject_links' in pg_get_viewdef('platform_read.sessions'::regclass)) > 0 or (position('sessions_without_daq' in pg_get_viewdef('platform_read.sessions'::regclass)) > 0 and position('session_subject_links' in pg_get_viewdef('platform_read.sessions_without_daq'::regclass)) > 0)"
        ).fetchone() == (True,)
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute("alter role platform_sync_writer login")
        connection.execute("alter role flywheel_analyst login")
        connection.execute("create schema daq_fae")
        for table in (
            "chat_sessions",
            "chat_turns",
            "turn_feedback",
            "turn_reviews",
            "eval_candidates",
            "knowledge_improvement_tasks",
            "qa_review_items",
        ):
            connection.execute(
                sql.SQL(
                    "create table daq_fae.{} (like platform_source_daq.{} including all)"
                ).format(sql.Identifier(table), sql.Identifier(table))
            )
            connection.execute(
                sql.SQL(
                    "alter table daq_fae.{} add column metadata jsonb not null default '{{}}'::jsonb"
                ).format(sql.Identifier(table))
            )
        connection.execute(
            "create table daq_fae.daq_installation_identity (singleton boolean, agent_id text, database_name text)"
        )
        connection.execute(
            "insert into daq_fae.daq_installation_identity values (true, 'ai-daq-fae-agent', current_database())"
        )
        connection.execute(
            """insert into daq_fae.chat_sessions
            (id, external_session_id, channel, user_id, owner_subject_id, owner_subject_type,
             created_at, last_active_at, source_synced_at, metadata)
            values (%s, 'daq:external', 'fae', %s, %s, 'enterprise_member', %s, %s, %s,
             '{"authentication_mode":"platform_enterprise","display_name":"unverified"}')""",
            (SID, OWNER, OWNER, NOW, NOW, NOW),
        )
        connection.execute(
            """insert into daq_fae.chat_turns
            (id, session_id, external_session_id, turn_index, trace_id, channel, question,
             answer, created_at, question_at, answer_at, source_synced_at, metadata, done,
             sources, stages, fallback_used, fallback_reason, outcome, duration_ms)
            values (%s, %s, 'daq:external', 0, 'real-trace', 'fae', 'synthetic question',
             'synthetic answer', %s, %s, %s, %s,
             '{"agent_id":"ai-daq-fae-agent","runtime_release":"actual-runtime","knowledge_release":"actual-knowledge"}',
             '{"actual_provider_model":"actual-model","configured_model":"configured-other"}',
             '[{"title":"evidence","path":"private/path"}]',
             '[{"stage":"synthesis","status":"completed","elapsed_ms":1}]',
             true, 'source_gap', 'safe_abstained', 100)""",
            (TID, SID, NOW, NOW, NOW, NOW),
        )
    trace = tmp_path / "traces.jsonl"
    trace.write_text(
        '{"type":"root","trace_id":"real-trace","span_id":"real-root",'
        '"node":"daq_chat","started_at":"2026-10-22T00:00:00+00:00",'
        '"metadata":{"agent_id":"ai-daq-fae-agent","runtime_release":"actual-runtime"}}\n'
        '{"type":"span","trace_id":"real-trace","span_id":"child",'
        '"parent_span_id":"real-root","node":"synthesis","started_at":"2026-10-22T00:00:00+00:00"}\n'
    )
    from app.sync_remote.cli import required_daq_schema_available

    writer_dsn = dsn.replace("observability_test_admin@", "platform_sync_writer@")
    assert required_daq_schema_available(writer_dsn) is True
    program = build_remote_program(default_sources("unused", "unused")["daq"])
    env = {
        **os.environ,
        "DAQ_DATABASE_URL": dsn + "?options=" + quote("-csearch_path=daq_fae"),
        "DAQ_TRACE_LOG_PATH": str(trace),
    }
    result = subprocess.run(
        [sys.executable, "-c", program],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    bundle = parse_export("daq", result.stdout)
    assert bundle.malformed_lines == 0
    for _ in range(2):
        synced = import_bundle(
            dsn.replace("observability_test_admin@", "platform_sync_writer@"),
            bundle,
            now=NOW,
        )
        assert synced.validation["turns_without_root_span"] == 0
    analyst_dsn = dsn.replace("observability_test_admin@", "flywheel_analyst@")
    repo = PsycopgObservabilityRepository(analyst_dsn, now=lambda: NOW)
    daq = repo.get_agent("ai-daq-fae-agent")
    assert daq.session_count == 1 and daq.source_kind == "fae"
    assert repo.get_agent("ai-fae-agent").session_count == 1
    session = repo.get_session("daq:" + SID)
    assert session.turn_count == 1
    assert (
        session.primary_sender_name is None
        and session.sender_identity_status == "unavailable"
    )
    assert session.turns[0].details["runtime_release"] == "actual-runtime"
    assert session.turns[0].fallback_used is True
    assert session.turns[0].outcome == "safe_abstained"
    assert session.turns[0].question_time_status == "exact"
    trace_detail = repo.get_trace("daq:" + TID)
    stage = next(step for step in trace_detail.steps if step.kind == "stage")
    assert stage.started_at is None
    assert stage.duration_ms == 1
    assert stage.safe_metadata["timing_status"] == "unavailable"
    assert trace_detail.trace_key == "daq:real-trace"
    assert trace_detail.model == "actual-model"
    assert trace_detail.details["runtime_release"] == "actual-runtime"
    assert (
        next(
            s for s in trace_detail.steps if s.name == "synthesis" and s.kind == "span"
        ).parent_step_key
        == "daq:span:real-trace:real-root"
    )
    assert (
        repo.get_latest_runtime_observation("ai-daq-fae-agent").model == "actual-model"
    )
    assert (
        next(s for s in repo.get_sync_status() if s.source_kind == "daq").status
        == "succeeded"
    )
    with psycopg.connect(dsn) as connection:
        assert connection.execute(
            "select count(*) from platform_source_daq.chat_turns"
        ).fetchone() == (1,)
        assert connection.execute(
            "select count(*) from platform_source_fae.chat_turns"
        ).fetchone() == (0,)
        assert (
            connection.execute(
                "select user_identity from platform_read.sessions where session_key=%s",
                ("daq:" + SID,),
            )
            .fetchone()[0]
            .startswith("daq:")
        )
        for role in (
            "public",
            "flywheel_ingest",
            "flywheel_analyst",
            "platform_review_writer",
        ):
            assert connection.execute(
                "select has_schema_privilege(%s, 'platform_source_daq', 'usage')",
                (role,),
            ).fetchone() == (False,)
        assert connection.execute(
            "select has_table_privilege('platform_sync_writer','platform_source_daq.chat_turns','insert')"
        ).fetchone() == (True,)
        assert connection.execute(
            "select has_table_privilege('platform_review_writer','platform_read.turns','select')"
        ).fetchone() == (True,)
        assert connection.execute(
            "select has_table_privilege('platform_review_writer','platform_read.feedback','select')"
        ).fetchone() == (True,)
        connection.execute("""create table platform_read.attachments (attachment_id uuid, turn_key text,
            direction text, display_name text, mime_type text, size_bytes bigint,
            received_or_generated_at timestamptz, archive_status text, delivery_status text,
            expires_at timestamptz)""")
        connection.execute(
            "insert into platform_read.attachments (turn_key) values (%s)", (TID,)
        )
        assert (
            connection.execute(
                ATTACHMENT_SQL, {"turn_keys": ["daq:" + TID], "through": NOW}
            ).fetchall()
            == []
        )
        camera_only = "00000000-0000-4000-8000-000000000099"
        connection.execute(
            "insert into platform_source_fae.chat_sessions (id, external_session_id, channel, created_at, last_active_at, source_synced_at) values (%s, 'camera:only', 'fae', now(), now(), now())",
            (camera_only,),
        )
        with (
            pytest.raises(psycopg.errors.ForeignKeyViolation),
            connection.transaction(),
        ):
            connection.execute(
                """insert into platform_source_daq.chat_turns
                    (id, session_id, external_session_id, turn_index, trace_id, channel,
                     question, answer, created_at, source_synced_at)
                    values (gen_random_uuid(), %s, 'daq:foreign', 0, 'x', 'fae', '', '', now(), now())""",
                (camera_only,),
            )
    # Real normalized source -> sanitizer -> signed batch -> encrypted replica
    # -> cloud repository. This source adapter excludes unrelated management data.
    from app.cloud_replica.crypto import BatchSigner, BatchVerifier, FieldCipher
    from app.cloud_replica.exporter import ReplicaExporter
    from app.cloud_replica.protocol import BatchLimits
    from app.cloud_replica.repository import ReplicaObservabilityRepository
    from app.cloud_replica.sanitize import SanitizationPolicy
    from app.cloud_replica.source import ReplicaSource
    from app.cloud_replica.store import ReplicaStore, import_verified_stream
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    apply(dsn, ROOT / "backend/migrations/008_cloud_replica.sql")
    source = ReplicaSource(dsn)

    class SessionsOnly:
        fetch_sessions = source.fetch_sessions

    private = Ed25519PrivateKey.generate()
    exporter = ReplicaExporter(
        source=SessionsOnly(),
        policy=SanitizationPolicy(version="daq-test"),
        identity_key=b"i" * 32,
        signer=BatchSigner(private),
        source_instance_id="daq-local-test",
        state_path=tmp_path / "state.json",
        queue_dir=tmp_path / "queue",
        clock=lambda: NOW + timedelta(minutes=1),
    )
    exported = exporter.export_batch(
        after=NOW - timedelta(days=1), through=NOW, limit=100
    )
    cipher = FieldCipher(b"e" * 32)
    store = ReplicaStore(dsn, cipher=cipher)
    payload = exported.batch_path.read_bytes()
    for expected in ("imported", "replayed"):
        imported = import_verified_stream(
            io.BytesIO(payload),
            BatchVerifier(private.public_key()),
            BatchLimits(),
            store,
        )
        assert imported.status == expected
    cloud = ReplicaObservabilityRepository(dsn, cipher=cipher, now=lambda: NOW)
    cloud_daq = cloud.get_agent("ai-daq-fae-agent")
    assert cloud_daq.session_count == 1 and cloud_daq.source_kind == "fae"
    from app.observability.models import SessionFilters

    cloud_sessions = cloud.list_sessions(
        SessionFilters(agent_id="ai-daq-fae-agent"), 10, 0
    )
    detail = cloud.get_session(cloud_sessions.items[0].session_key)
    assert detail.channel == "fae" and detail.turns[0].outcome == "safe_abstained"
    assert detail.turns[0].fallback_used is True
    assert detail.turns[0].input_attachments == []
    aggregate = cloud.get_trace(detail.turns[0].turn_key)
    assert aggregate.status == "completed" and aggregate.model == "actual"
    assert aggregate.steps == [] and aggregate.detail_availability == "unavailable"
    with psycopg.connect(dsn) as connection:
        assert connection.execute(
            "select count(*) from platform_replica.sessions where agent_id='ai-daq-fae-agent'"
        ).fetchone() == (1,)
        row = connection.execute(
            "select display_payload from platform_replica.sessions where agent_id='ai-daq-fae-agent'"
        ).fetchone()[0]
        assert b"synthetic question" not in bytes(row)
    # Producer generations must not count DAQ under the camera producer.
    from app.operations.source import PsycopgOperationsSource

    operations = PsycopgOperationsSource(dsn)
    assert operations.fetch_remote_usage_batch("fae").cumulative_totals == {}
    daq_usage = operations.fetch_remote_usage_batch("daq")
    assert daq_usage.cumulative_totals == {"ai-daq-fae-agent": 1}
    assert daq_usage.occurrences[0].source_kind == "fae"
    assert operations.fetch_remote_execution("fae") == ()
    daq_failures = operations.fetch_remote_execution("daq")
    assert [(o.agent_id, o.source_kind, o.signal_type) for o in daq_failures] == [
        ("ai-daq-fae-agent", "fae", "fallback")
    ]
    # Missing/foreign ownership never becomes an attributed display name.
    with psycopg.connect(dsn) as connection:
        connection.execute(
            "update platform_source_daq.chat_sessions set user_id='different' where id=%s",
            (SID,),
        )
        assert connection.execute(
            "select user_identity, primary_sender_name from platform_read.sessions where session_key=%s",
            ("daq:" + SID,),
        ).fetchone() == (None, None)

    # Misconfiguration fails closed before producing any table data.
    for bad in (
        {},
        {"DAQ_DATABASE_URL": dsn},
        {"DAQ_DATABASE_URL": dsn + "?options=" + quote("-csearch_path=public")},
    ):
        bad_env = {k: v for k, v in env.items() if k != "DAQ_DATABASE_URL"} | bad
        failed = subprocess.run(
            [sys.executable, "-c", program],
            env=bad_env,
            text=True,
            capture_output=True,
            check=False,
        )
        assert failed.returncode != 0 and failed.stdout == ""


@pytest.mark.postgres
def test_runner_rejects_checksum_mismatch(daq_database, tmp_path):
    dsn_file = tmp_path / "owner-dsn"
    dsn_file.write_text(daq_database + "\n")
    dsn_file.chmod(0o600)
    with psycopg.connect(daq_database) as connection:
        old = connection.execute(
            "select sha256 from platform_sync.schema_migrations where version=14"
        ).fetchone()[0]
        connection.execute(
            "update platform_sync.schema_migrations set sha256=%s where version=14",
            ("0" * 64,),
        )
    try:
        result = subprocess.run(
            [
                str(ROOT / "deploy/migrate-observability"),
                str(dsn_file),
                str(ROOT / "backend/migrations/014_admin_verified_sender_view.sql"),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode != 0
        assert result.stdout == ""
        assert result.stderr.strip() == "OBSERVABILITY_MIGRATION_FAILED"
    finally:
        with psycopg.connect(daq_database) as connection:
            connection.execute(
                "update platform_sync.schema_migrations set sha256=%s where version=14",
                (old,),
            )
