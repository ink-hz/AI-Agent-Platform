from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.control_room.models import (
    AgentLifecycle,
    AgentRuntime,
    AgentRuntimeView,
    Readiness,
)
from app.main import create_app


NOW = datetime(2026, 7, 24, 8, 0, tzinfo=timezone.utc)


class FakeControlRoomService:
    async def get_runtime(self, agent_id: str):
        if agent_id == "missing-agent":
            return None
        return AgentRuntimeView(
            agent_id=agent_id,
            readiness=Readiness(
                status="Ready",
                reason="Runtime and primary channel are available",
                observed_at=NOW,
                freshness="live",
            ),
            runtime=AgentRuntime(
                engine="claude",
                model="claude-opus-4-8",
                model_source="runtime",
                backend="pty",
                channel="Feishu",
                channel_status="connected",
                active_turns=0,
                process_uptime_seconds=60,
            ),
            lifecycle=AgentLifecycle(
                live_since=NOW,
                last_updated_at=NOW,
                production_runtime_seconds=3600,
            ),
            evidence=[],
        )


def client(tmp_path) -> TestClient:
    registry = tmp_path / "registry.yaml"
    registry.write_text("agents: []\n", encoding="utf-8")
    app = create_app(
        registry_path=str(registry),
        cluster_contract_path=str(tmp_path / "missing-contract.json"),
        start_poller=False,
        control_room_service=FakeControlRoomService(),
    )
    return TestClient(app)


def test_runtime_endpoint_returns_canonical_safe_response(tmp_path):
    response = client(tmp_path).get(
        "/api/agents/marketing-inbound-bot/runtime"
    )

    assert response.status_code == 200
    assert response.json()["runtime"]["model_source"] == "runtime"
    serialized = response.text.lower()
    for forbidden in (
        "appid", "appsecret", "token", "workdir", "statedir",
        "configpath", "providerurl",
    ):
        assert forbidden not in serialized


def test_runtime_endpoint_returns_404_for_unknown_agent(tmp_path):
    response = client(tmp_path).get("/api/agents/missing-agent/runtime")

    assert response.status_code == 404
    assert response.json() == {"detail": "agent not found"}
