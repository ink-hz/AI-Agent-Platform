"""Real source PostgreSQL -> worker -> encrypted replica -> HTTP consumer."""
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import NAMESPACE_URL, uuid5

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_cloud_sync_sources import NOW, SID, TID
from test_cloud_sync_sources_postgres import source_database  # noqa: F401
from test_observability_migration_runner import observability_database  # noqa: F401

from app.cloud_replica.crypto import FieldCipher, stable_id
from app.cloud_replica.repository import ReplicaObservabilityRepository
from app.cloud_replica.sanitize import SanitizationPolicy
from app.cloud_sync.models import SourceProfile
from app.cloud_sync.sources import DatabaseAgentSource
from app.cloud_sync.store import CloudSyncStore
from app.cloud_sync.worker import CloudSyncWorker
from app.observability.routes import router
from app.observability.service import ObservabilityService


def role_dsn(dsn, role):
    parts = urlsplit(dsn)
    return urlunsplit(parts._replace(netloc=f"{role}@{parts.hostname}:{parts.port}"))


@pytest.mark.postgres
def test_complete_seven_turn_recovery_child_change_restart_and_source_outage(source_database, tmp_path):  # noqa: F811 -- imported pytest fixture
    dsn, _ = source_database
    root = Path(__file__).parents[1]
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute((root / "migrations/008_cloud_replica.sql").read_text())
        connection.execute((root / "migrations/cloud_agent_sync/001_agent_sources.sql").read_text())
        connection.execute("create role pipeline_source login")
        connection.execute("alter role pipeline_source set default_transaction_read_only=on")
        connection.execute("grant usage on schema public to pipeline_source")
        connection.execute("grant select on public.chat_sessions,public.chat_turns,public.turn_feedback,public.turn_reviews to pipeline_source")
        connection.execute("create role pipeline_writer login in role platform_cloud_sync")
        connection.execute("create role pipeline_reader login in role platform_replica_read")
        connection.execute("select platform_replica.set_agent_publisher(%s,%s,%s,%s,%s,%s)",
                           ("ai-fae-agent", "fae", "cloud_direct", "cloud", "pipeline_writer", "fixture-cloud"))
        for index in range(1, 7):
            connection.execute("""insert into public.chat_turns
                (id,session_id,turn_index,question,answer,created_at,question_at,answer_at,
                 outcome,fallback_used,duration_ms,sources,done,metadata)
                values (%s,%s,%s,%s,%s,%s,%s,%s,'resolved',false,20,'[]','{}','{}')""",
                (str(uuid5(NAMESPACE_URL, f"pipeline-turn-{index}")), SID, index,
                 f"Question {index}", f"Complete answer {index}", NOW, NOW, NOW))

    credentials = tmp_path / "pipeline-source-dsn"
    credentials.write_text(role_dsn(dsn, "pipeline_source"))
    credentials.chmod(0o600)
    profile = SourceProfile("ai-fae-agent", "fae", str(credentials), "postgres")
    clock = [NOW]
    cipher = FieldCipher(b"e" * 32)
    store = CloudSyncStore(role_dsn(dsn, "pipeline_writer"), cipher=cipher)
    def make_worker():
        return CloudSyncWorker(store=store, identity_key=b"k" * 32,
            policy=SanitizationPolicy(), include_verified_subject=True,
            clock=lambda: clock[0], emit=lambda value: None,
            source_factory=lambda p: DatabaseAgentSource(p, clock=lambda: clock[0]))
    worker = make_worker()
    result = worker.run_once(profile)
    assert result is not None and result.source_turns == 7
    key = stable_id("session", "fae:" + SID, b"k" * 32)
    repository = ReplicaObservabilityRepository(role_dsn(dsn, "pipeline_reader"),
                                               cipher=cipher, now=lambda: clock[0])
    app = FastAPI()
    app.include_router(router)
    app.state.replica_repository = repository
    app.state.observability_service = ObservabilityService(repository)
    with TestClient(app) as client:
        detail = client.get(f"/api/sessions/{key}").raise_for_status().json()
        assert detail["title"] == "original question"
        assert detail["turn_count"] == 7
        assert [turn["turn_index"] for turn in detail["turns"]] == list(range(7))
        assert detail["turns"][-1]["answer"] == "Complete answer 6"
        assert detail["freshness"] == "fresh"
        with psycopg.connect(dsn) as connection:
            before = connection.execute("select display_payload,payload_nonce from platform_replica.sessions where session_key=%s", (key,)).fetchone()
        clock[0] += timedelta(seconds=10)
        assert make_worker().run_once(profile).status == "unchanged"
        with psycopg.connect(dsn) as connection:
            assert connection.execute("select display_payload,payload_nonce from platform_replica.sessions where session_key=%s", (key,)).fetchone() == before
            connection.execute("insert into public.turn_feedback values (%s,%s,'bad',%s)",
                               (str(uuid5(NAMESPACE_URL, "pipeline-feedback")), TID, clock[0]))
        clock[0] += timedelta(seconds=10)
        assert worker.run_once(profile).changed_sessions == 1
        body = client.get(f"/api/sessions/{key}").raise_for_status().json()
        assert body["feedback_count"] == 1
        success = body["source_status"]["last_source_success_at"]
        with psycopg.connect(dsn) as connection:
            connection.execute("drop table public.turn_reviews")
        clock[0] += timedelta(seconds=10)
        assert worker.run_once(profile) is None
        retained = client.get(f"/api/sessions/{key}").raise_for_status().json()
        assert retained["turn_count"] == 7
        assert retained["turns"][-1]["answer"] == "Complete answer 6"
        assert retained["freshness"] == "stale"
        assert retained["source_status"]["last_source_success_at"] == success
        assert retained["source_status"]["last_error"] == "source_schema_unverified"
    with psycopg.connect(role_dsn(dsn, "pipeline_source")) as connection, pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        connection.execute("delete from public.chat_turns")
