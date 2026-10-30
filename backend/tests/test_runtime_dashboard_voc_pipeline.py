"""Real PG producer/RLS/publication/dashboard pipeline; identity adapter is local."""
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
from psycopg.conninfo import make_conninfo
import pytest

from app.cloud_replica.crypto import FieldCipher
from app.cloud_replica.repository import ReplicaObservabilityRepository
from app.cloud_replica.sanitize import SanitizationPolicy
from app.cloud_sync.sources import DatabaseAgentSource
from app.cloud_sync.store import CloudSyncStore
from app.cloud_sync.worker import CloudSyncWorker
from app.runtime_dashboard.repository import ReplicaDashboardReader
from app.runtime_dashboard.service import DashboardService
from test_observability_migration_runner import observability_database  # noqa: F401
from test_runtime_dashboard_http import client_for
import test_voc_observation_contract as voc_contract
from test_voc_observation_contract import POLICY, UID, voc_database  # noqa: F401


@pytest.fixture
def pipeline_voc_database(monkeypatch, request):
    # Keep the two existing producer inputs outside the7d window deterministically.
    monkeypatch.setattr(voc_contract, 'NOW', datetime.now(UTC) - timedelta(days=8))
    return request.getfixturevalue('voc_database')


@pytest.mark.postgres
def test_restricted_voc_publication_reaches_dashboard_and_failure_preserves_counts(pipeline_voc_database):
    dsn, _, profile, _, producer_dsn = pipeline_voc_database
    root = Path(__file__).parents[1]
    with psycopg.connect(dsn, autocommit=True) as owner:
        for name in ('008_cloud_replica.sql', 'cloud_agent_sync/001_agent_sources.sql'):
            owner.execute((root / 'migrations' / name).read_text())
        owner.execute('grant platform_cloud_sync to voc_contract_writer')
        owner.execute('grant platform_replica_read to voc_contract_reader')
        owner.execute("select platform_replica.set_agent_publisher('voc','voc','cloud_direct','cloud','voc_contract_writer','voc-dashboard-pipeline')")
    # Actual constrained producer login adds a current input with no receipt.
    now = datetime.now(UTC)
    with psycopg.connect(producer_dsn) as producer:
        producer.execute('''insert into voc_raw.inbound_messages
            (id,sender_internal_user_id,conversation_id,conversation_type,received_at,dingtalk_created_at,text_content)
            values (3,%s,'conversation','group',%s,%s,'fresh pipeline private input')''', (UID, now, now))
        producer.execute("insert into voc_raw.interaction_jobs values (3,'done')")
    cipher = FieldCipher(b'p' * 32)
    store = CloudSyncStore(make_conninfo(dsn, user='voc_contract_writer'), cipher=cipher)
    events = []
    worker = CloudSyncWorker(store=store, identity_key=b'i' * 32,
        policy=SanitizationPolicy(), include_verified_subject=True, emit=events.append)
    published = worker.run_once(profile)
    assert published is not None and published.status == 'published' and published.source_turns == 3
    source = DatabaseAgentSource(profile).read_snapshot()
    assert sum(len(session.turns) for session in source.sessions) == 3
    replica = ReplicaObservabilityRepository(make_conninfo(dsn, user='voc_contract_reader'), cipher=cipher)
    reader = ReplicaDashboardReader(replica, ttl_seconds=0)
    client, _ = client_for(service=DashboardService(replica=reader), workspace_summaries=False)
    response = client.get('/api/dashboard/overview?days=7')
    assert response.status_code == 200 and 'no-store' in response.headers['cache-control']
    overview = response.json()
    voc = next(item for item in overview['applications'] if item['id'] == 'voc')
    assert overview['inventory']['applications'] == 7
    assert overview['inventory']['configured_agents'] == 10
    assert voc['source']['status'] == 'fresh' and voc['source']['complete'] is True
    assert voc['source']['input_time_basis'] == 'question_at'
    assert voc['source']['missing_question_time_records'] == 0
    assert voc['usage']['retained']['turns']['value'] == 3
    assert voc['usage']['retained']['deliveries']['value'] == 1
    assert voc['usage']['window']['turns']['value'] == 1
    assert voc['usage']['window']['deliveries']['value'] == 0
    for text in ('fresh pipeline private input', 'confirmed answer', 'queued answer', 'question 1'):
        assert text not in response.text
    with psycopg.connect(dsn) as owner:
        before = owner.execute('select session_key,display_payload,payload_nonce,payload_sha256 from platform_replica.sessions order by session_key').fetchall()
        success = owner.execute("select last_source_success_at,last_published_at,source_sessions,source_turns from platform_replica.agent_sources where agent_id='voc'").fetchone()
        owner.execute(f'drop policy {POLICY} on voc_core.outbox_messages')
    assert worker.run_once(profile) is None
    assert events[-1]['error_code'] == 'source_visibility_unverified'
    assert store.source_statuses()['voc']['last_error'] == 'source_visibility_unverified'
    with psycopg.connect(dsn) as owner:
        assert owner.execute('select session_key,display_payload,payload_nonce,payload_sha256 from platform_replica.sessions order by session_key').fetchall() == before
        assert owner.execute("select last_source_success_at,last_published_at,source_sessions,source_turns from platform_replica.agent_sources where agent_id='voc'").fetchone() == success
    stale = next(item for item in client.get('/api/dashboard/overview').json()['applications'] if item['id'] == 'voc')
    assert stale['source']['status'] == 'stale'
    assert stale['source']['error'] == 'source_failed'
    assert stale['usage']['retained']['turns']['value'] == 3
    assert stale['usage']['retained']['turns']['status'] == 'partial'
    client.cookies.clear()
    assert client.get('/api/dashboard/overview').status_code == 401
