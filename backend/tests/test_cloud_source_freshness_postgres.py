from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from app.cloud_replica.crypto import FieldCipher
from app.cloud_replica.repository import ReplicaObservabilityRepository
from app.cloud_sync.store import CloudSyncStore
from app.observability.routes import router
from app.observability.service import ObservabilityService
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_cloud_repository import _record
from test_observability_migration_runner import (
    observability_database,  # noqa: F401 -- pytest fixture
)


@pytest.mark.postgres
def test_encrypted_publication_reader_role_and_http_preserve_independent_source_failure(observability_database):  # noqa: F811 -- pytest fixture
    """A real committed replica and SELECT-only reader feed the HTTP consumer."""
    dsn = observability_database
    root = Path(__file__).parents[1]
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute((root / "migrations/008_cloud_replica.sql").read_text())
        connection.execute((root / "migrations/cloud_agent_sync/001_agent_sources.sql").read_text())
        connection.execute("create role freshness_fae login in role platform_cloud_sync")
        connection.execute("create role freshness_admin login in role platform_cloud_sync")
        connection.execute("create role freshness_reader login in role platform_replica_read")
        for agent, kind, role in (("ai-fae-agent", "fae", "freshness_fae"),
                                  ("ai-admin-agent", "admin", "freshness_admin")):
            connection.execute("select platform_replica.set_agent_publisher(%s,%s,%s,%s,%s,%s)",
                               (agent, kind, "cloud_direct", "cloud", role, "fixture-cloud"))

    def role_dsn(role):
        parts = urlsplit(dsn)
        return urlunsplit(parts._replace(netloc=f"{role}@{parts.hostname}:{parts.port}"))

    now = datetime(2026, 10, 29, 8, tzinfo=UTC)
    cipher = FieldCipher(b"e" * 32)
    fae = CloudSyncStore(role_dsn("freshness_fae"), cipher=cipher)
    admin = CloudSyncStore(role_dsn("freshness_admin"), cipher=cipher)
    record = _record(now - timedelta(hours=1), agent_id="ai-fae-agent")
    record["source_kind"] = "fae"
    prototype = record["turns"][0]
    record["turns"] = [
        {**deepcopy(prototype), "key": chr(99 + index) * 52, "turn_index": index + 1}
        for index in range(7)
    ]
    def snapshot(agent_id, source_kind, observed, count):
        return SimpleNamespace(agent_id=agent_id, source_kind=source_kind, source_observed_at=observed,
                               sessions=(object(),) * count, complete=True, elapsed_ms=5)
    with fae.observation_cycle("ai-fae-agent") as lease:
        fae.publish_snapshot(snapshot("ai-fae-agent", "fae", now - timedelta(hours=1), 1), (record,), lease=lease)
    fae.record_failure("ai-fae-agent", "source_unavailable", now)
    with admin.observation_cycle("ai-admin-agent") as lease:
        admin.publish_snapshot(snapshot("ai-admin-agent", "admin", now, 0), (), lease=lease)

    repository = ReplicaObservabilityRepository(role_dsn("freshness_reader"), cipher=cipher, now=lambda: now)
    app = FastAPI()
    app.include_router(router)
    app.state.replica_repository = repository
    app.state.observability_service = ObservabilityService(repository)
    with TestClient(app) as client:
        detail = client.get(f"/api/sessions/{record['key']}")
        assert detail.status_code == 200
        body = detail.json()
        assert body["turn_count"] == 7
        assert len(body["turns"]) == 7
        assert body["turns"][-1]["answer"] == prototype["answer"]["text"]
        assert body["freshness"] == "stale"
        assert body["source_status"]["last_error"] == "source_unavailable"
        assert body["source_status"]["lag_seconds"] == 3600
        assert client.get("/api/agents/ai-fae-agent").json()["freshness"] == "stale"
        assert client.get("/api/agents/ai-admin-agent").json()["freshness"] == "fresh"

        with psycopg.connect(dsn) as connection:
            connection.execute("revoke select on platform_replica.agent_sources from platform_replica_read")
        assert client.get("/api/agents").status_code == 503
        assert client.get(f"/api/sessions/{record['key']}").status_code == 503
