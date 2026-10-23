"""VOC producer -> real isolated mirror -> read repositories; no business writes."""

import subprocess
import io
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest

from app.fleet.repository import PsycopgFlywheelRepository
from app.observability.models import SessionFilters
from app.observability.repository import PsycopgObservabilityRepository
from app.sync_remote.export import parse_export
from app.sync_remote.importer import import_bundle
from test_daq_observability_postgres import apply, daq_database  # noqa: F401
from test_observability_migration_runner import observability_database  # noqa: F401
from test_voc_observability import bundle_text, source_rows

ROOT = Path(__file__).parents[2]


@pytest.mark.postgres
def test_voc_migration_import_usage_and_session_replay(request, tmp_path):
    from app.sync_remote.voc_export import project_voc_rows

    dsn = request.getfixturevalue("daq_database")
    migration = ROOT / "backend/migrations/016_voc_observability.sql"
    assert migration.exists(), "VOC source mirror is missing"
    apply(dsn, ROOT / "backend/migrations/015_daq_observability.sql")
    with psycopg.connect(dsn) as connection:
        previous = connection.execute("select session_key from platform_read.sessions order by session_key").fetchall()
        oids = connection.execute("select 'platform_read.sessions'::regclass::oid, 'platform_read.turns'::regclass::oid").fetchone()
    credential = tmp_path / "owner-dsn"
    credential.write_text(dsn + "\n")
    credential.chmod(0o600)
    for _ in range(2):
        result = subprocess.run([str(ROOT / "deploy/migrate-observability"), str(credential), str(migration)], text=True, capture_output=True)
        assert result.returncode == 0, result.stderr
        assert "version=016" in result.stdout
    from app.sync_remote.cli import required_voc_schema_available
    def sync_writer_connection(*args, **kwargs):
        connection = psycopg.connect(*args, **kwargs)
        connection.execute("set session authorization platform_sync_writer")
        return connection
    assert required_voc_schema_available(dsn, connection_factory=sync_writer_connection)
    bundle = parse_export("voc", bundle_text(project_voc_rows(source_rows())))
    for _ in range(2):
        result = import_bundle(dsn, bundle)
        assert result.status == "succeeded"
    with psycopg.connect(dsn) as connection:
        assert connection.execute("select count(*) from platform_source_voc.chat_sessions").fetchone() == (1,)
        assert connection.execute("select count(*) from platform_source_voc.chat_turns").fetchone() == (2,)
        assert connection.execute("select session_key from platform_read.sessions where agent_id <> 'voc' order by session_key").fetchall() == previous
        assert connection.execute("select 'platform_read.sessions'::regclass::oid, 'platform_read.turns'::regclass::oid").fetchone() == oids
        assert connection.execute("select has_table_privilege('flywheel_analyst','platform_source_voc.chat_turns','select'),has_table_privilege('platform_sync_writer','platform_source_voc.chat_turns','insert')").fetchone() == (False, True)
    repository = PsycopgObservabilityRepository(dsn)
    agent = repository.get_agent("voc")
    assert agent.source_kind == "voc" and agent.session_count == 1 and agent.total_turns == 1
    page = repository.list_sessions(SessionFilters(agent_id="voc", source_kind="voc"), 50, 0)
    assert page.total == 1
    assert page.items[0].primary_sender_name == "员工一"
    replay = repository.get_session(page.items[0].session_key)
    assert len(replay.turns) == 2
    assert replay.turns[0].answer == "已整理草稿，请确认。"
    assert replay.turns[1].answer == "" and replay.turns[1].outcome == "delivery_pending"
    assert replay.turns[0].trace_key is None
    usage = PsycopgFlywheelRepository(dsn).fetch_usage()
    voc = next(record for record in usage.records if record.bot_id == "voc")
    assert voc.total_conversations == 1 and voc.session_count == 1

    # Exercise the same signed and encrypted transport consumed by production.
    from app.cloud_replica.crypto import BatchSigner, BatchVerifier, FieldCipher
    from app.cloud_replica.exporter import ReplicaExporter
    from app.cloud_replica.protocol import BatchLimits
    from app.cloud_replica.repository import ReplicaObservabilityRepository
    from app.cloud_replica.sanitize import SanitizationPolicy
    from app.cloud_replica.source import ReplicaSource
    from app.cloud_replica.store import ReplicaStore, import_verified_stream
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    with psycopg.connect(dsn) as connection:
        connection.execute("""create table platform_read.attachments (attachment_id uuid, turn_key text,
            direction text, display_name text, mime_type text, size_bytes bigint,
            received_or_generated_at timestamptz, archive_status text,
            delivery_status text, expires_at timestamptz)""")
    apply(dsn, ROOT / "backend/migrations/008_cloud_replica.sql")
    source = ReplicaSource(dsn)
    class SessionsOnly:
        fetch_sessions = source.fetch_sessions
    now = datetime.now(timezone.utc)
    through = now + timedelta(seconds=1)
    key = Ed25519PrivateKey.generate()
    exporter = ReplicaExporter(
        source=SessionsOnly(), policy=SanitizationPolicy(version="voc-test"),
        identity_key=b"i" * 32, signer=BatchSigner(key),
        source_instance_id="voc-test", state_path=tmp_path / "state.json",
        queue_dir=tmp_path / "queue", clock=lambda: through,
    )
    batch = exporter.export_batch(after=now - timedelta(days=30), through=through, limit=100)
    cipher = FieldCipher(b"e" * 32)
    store = ReplicaStore(dsn, cipher=cipher)
    for expected in ("imported", "replayed"):
        imported = import_verified_stream(io.BytesIO(batch.batch_path.read_bytes()), BatchVerifier(key.public_key()), BatchLimits(), store)
        assert imported.status == expected
    cloud = ReplicaObservabilityRepository(dsn, cipher=cipher, now=lambda: through)
    assert cloud.get_agent("voc").source_kind == "voc"
    assert cloud.get_agent("voc").total_turns == 1
    cloud_page = cloud.list_sessions(SessionFilters(agent_id="voc", source_kind="voc"), 50, 0)
    assert cloud_page.total == 1 and cloud_page.items[0].turn_count == 1
    assert cloud_page.items[0].feedback_count is None
    assert cloud_page.items[0].feedback_availability == "unavailable"
    detail = cloud.get_session(cloud_page.items[0].session_key)
    assert len(detail.turns) == 2
    assert detail.turns[1].answer == "" and detail.turns[1].answer_at is None
    assert detail.turns[1].fallback_used is None
    assert detail.turns[1].question_at is None
    assert detail.turns[0].feedback_availability == "unavailable"
    cloud_usage = next(item for item in cloud.usage_snapshot().records if item.bot_id == "voc")
    assert cloud_usage.total_conversations == 1 and cloud_usage.session_count == 1

    # Route contracts use the actual decrypted repository, not canned API rows.
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.observability.service import ObservabilityService
    from test_fleet_service import make_service

    registry, contract = tmp_path / "registry.yaml", tmp_path / "contract.json"
    registry.write_text("version: 1\nagents: []\n")
    contract.write_text('{"bots": []}')
    app = create_app(
        registry_path=str(registry), cluster_contract_path=str(contract), start_poller=False,
        observability_service=ObservabilityService(cloud),
        fleet_service=make_service(cloud_usage, bot_ids=["voc"]),
    )
    client = TestClient(app)
    agents = client.get("/api/agents")
    assert agents.status_code == 200
    assert next(a for a in agents.json() if a["id"] == "voc")["total_turns"] == 1
    sessions = client.get("/api/sessions?agent_id=voc&source_kind=voc")
    assert sessions.status_code == 200 and sessions.json()["total"] == 1
    replay_response = client.get("/api/sessions/" + sessions.json()["items"][0]["session_key"])
    assert replay_response.status_code == 200 and len(replay_response.json()["turns"]) == 2
    overview = client.get("/api/fleet/overview")
    assert overview.status_code == 200
    card = next(a for a in overview.json()["agents"] if a["id"] == "voc")
    assert card["total_conversations"] == 1 and card["session_count"] == 1
