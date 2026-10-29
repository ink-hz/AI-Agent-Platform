from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.cloud_replica.crypto import FieldCipher
from app.cloud_replica.repository import ReplicaObservabilityRepository
from app.control_plane.authorization import AuthorizationService
from app.control_plane.middleware import IdentitySecurityMiddleware
from app.control_plane.models import AuthContext, Role
from app.observability.models import AgentSourceStatus
from app.observability.repository import ObservabilityReadError, PsycopgObservabilityRepository
from app.observability.routes import router
from app.observability.service import ObservabilityService
from test_cloud_repository import _Connection

NOW = datetime(2026, 10, 29, tzinfo=UTC)


def registry(states=None):
    from app.observability.source_catalog import public_source_registry
    return {item.source_id: item.model_dump(mode="json") for item in public_source_registry(states or {})}


def test_native_registry_preserves_owner_and_business_semantics_without_fake_chats():
    records = registry()
    hr, sdk = records["native-hr"], records["native-sdk"]
    assert (hr["agent_id"], hr["unit"], hr["permission_scope"], hr["workspace_url"]) == (
        "hr-bot", "work", "owner_scope", "/hr/")
    assert hr["persistence"] == "platform_hr_agent"
    assert (sdk["agent_id"], sdk["unit"], sdk["permission_scope"], sdk["workspace_url"]) == (
        None, "execution", "business_scope", "/sdk/")
    for item in (hr, sdk):
        assert (item["mode"], item["mode_label"]) == ("native", "无需同步")
        assert item["source_status"] is None
        assert item["runtime_status"] is None
        assert item["source_observed_at"] is None
        assert item["feedback_availability"] == "unavailable"
        assert item["review_availability"] == "unavailable"
        assert "turn_count" not in item and "answer" not in item


def test_cloud_registry_uses_each_agents_proven_source_state():
    states = {
        "ai-fae-agent": AgentSourceStatus(mode="cloud_direct", publisher="cloud", source_kind="fae",
            last_source_success_at=NOW, last_published_at=NOW, lag_seconds=3),
        "ai-admin-agent": AgentSourceStatus(mode="office", publisher="office", source_kind="admin",
            last_source_success_at=NOW-timedelta(days=1), last_error="source_unavailable"),
    }
    records = registry(states)
    fae = records["ai-fae-agent"]
    assert (fae["mode"], fae["mode_label"], fae["unit"]) == ("cloud_direct", "云端直连", "session")
    assert fae["source_observed_at"] == NOW.isoformat().replace("+00:00", "Z")
    assert fae["source_status"]["lag_seconds"] == 3
    assert records["ai-admin-agent"]["mode"] == "office"
    assert records["ai-admin-agent"]["source_status"]["last_error"] == "source_unavailable"
    for source in ("ai-daq-fae-agent", "voc"):
        assert records[source]["source_status"] is None
        assert records[source]["mode"] is None
        assert records[source]["source_observed_at"] is None
    assert records["ai-daq-fae-agent"]["source_kind"] == "fae"


def test_local_registry_does_not_infer_active_instances_from_catalog():
    records = registry()
    local = [item for item in records.values() if item["intended_mode"] == "office"]
    assert {item["source_id"] for item in local} >= {"office-metabot", "marketing-prospecting-bot"}
    for item in local:
        assert item["runtime_status"] is None
        assert item["source_observed_at"] is None
        assert item["source_status"] is None


def test_replica_registry_reads_source_states_once_without_session_or_content_reads():
    connection = _Connection([], NOW)
    connection.agent_sources = [dict(agent_id="ai-fae-agent", mode="cloud_direct", publisher="cloud",
        source_kind="fae", source_instance_id=None, last_source_success_at=NOW,
        last_published_at=NOW, last_failure_at=None, last_error=None, source_sessions=2,
        source_turns=3, last_duration_ms=4)]
    repo = ReplicaObservabilityRepository("unused", cipher=FieldCipher(b"x"*32),
        connect=lambda *a, **k: connection, now=lambda: NOW)
    records = {item.source_id: item for item in repo.data_sources()}
    assert records["ai-fae-agent"].source_status.source_turns == 3
    assert len(connection.statements) == 2
    assert all("platform_replica.sessions" not in sql for sql, _ in connection.statements)


def test_local_registry_explicitly_has_no_proven_source_watermark():
    def no_database(*args, **kwargs):
        pytest.fail("metadata registry must not open business database")
    records = PsycopgObservabilityRepository("unused", connect=no_database).data_sources()
    assert all(item.source_observed_at is None for item in records)


def _client(role):
    class Auth:
        route_prefix = "/"
        cookie_name = "session"
        csrf_cookie_name = "csrf"
        public_base_url = "https://agent.example.test"
        trusted_proxy_networks = ()
        rate_limiter = None
        def authenticate(self, token):
            return (AuthContext(uuid4(), role, uuid4(), False), b"csrf") if token == "valid" and role else None
    class Repo:
        calls = 0
        def data_sources(self):
            self.calls += 1
            from app.observability.source_catalog import public_source_registry
            return public_source_registry({})
    app = FastAPI()
    repo = Repo()
    app.state.observability_service = ObservabilityService(repo)
    app.include_router(router)
    app.add_middleware(IdentitySecurityMiddleware, auth=Auth(), public_assets=frozenset(),
        authorization=AuthorizationService(None, cloud_mode=True), routes=tuple(app.router.routes))
    client = TestClient(app)
    if role:
        client.cookies.set("session", "valid")
    return client, repo


@pytest.mark.parametrize("role,status", [(None,401), (Role.MEMBER,403),
    (Role.MANAGEMENT_VIEWER,403), (Role.PLATFORM_OWNER,200), (Role.PLATFORM_ADMIN,200)])
def test_data_sources_api_keeps_existing_session_management_boundary(role, status):
    client, repo = _client(role)
    response = client.get("/api/data-sources")
    assert response.status_code == status
    assert repo.calls == (1 if status == 200 else 0)
    if status == 200:
        assert {item["source_id"] for item in response.json()} >= {"native-hr", "native-sdk"}
        assert response.headers["cache-control"] == "private, no-store"


def test_data_sources_api_discloses_unavailable_source_state():
    client, repo = _client(Role.PLATFORM_ADMIN)
    def unavailable():
        raise ObservabilityReadError("failed")
    repo.data_sources = unavailable
    response = client.get("/api/data-sources")
    assert (response.status_code, response.json()) == (503, {"detail":"observability unavailable"})
