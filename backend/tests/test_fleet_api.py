import inspect
import json

from fastapi.testclient import TestClient

from app.fleet.models import (
    DataSourceStatus,
    FleetAgent,
    FleetOverview,
    FleetSummary,
)
from app.fleet.routes import fleet_overview
from app.main import create_app


class StaticFleetService:
    async def overview(self):
        return FleetOverview(
            summary=FleetSummary(
                total_agents=1,
                running_agents=1,
                active_agents=1,
                degraded_agents=0,
                offline_agents=0,
                checking_agents=0,
                total_conversations=14,
                conversations_last_7d=4,
                conversations_previous_7d=2,
                change_percent=100.0,
            ),
            trend=[],
            agents=[
                FleetAgent(
                    id="hr-bot",
                    name="HR 助手",
                    domain="人力资源",
                    description="支持招聘、人事与员工服务流程。",
                    glyph="HR",
                    accent="people",
                    state="active",
                    live_since="2026-07-14T09:36:54.254859+08:00",
                    live_since_basis="earliest_session",
                    last_updated_at="2026-07-17T10:38:57+08:00",
                    last_updated_basis="repository_history",
                    current_runtime_seconds=3600,
                    total_conversations=14,
                    conversations_last_7d=4,
                    last_activity_at="2026-07-21T02:00:00+00:00",
                    recent_summary="入职需要哪些材料？",
                )
            ],
            runtime_source=DataSourceStatus(
                healthy=True,
                checked_at="2026-07-21T02:00:00+00:00",
            ),
            usage_source=DataSourceStatus(
                healthy=True,
                checked_at="2026-07-21T02:00:00+00:00",
            ),
        )


def test_fleet_overview_returns_product_fields_without_technical_details(tmp_path):
    registry_path = tmp_path / "registry.yaml"
    registry_path.write_text("version: 1\nagents: []\n", encoding="utf-8")
    contract_path = tmp_path / "contract.json"
    contract_path.write_text(json.dumps({"bots": []}), encoding="utf-8")
    app = create_app(
        registry_path=str(registry_path),
        cluster_contract_path=str(contract_path),
        start_poller=False,
        fleet_service=StaticFleetService(),
    )

    response = TestClient(app).get("/api/fleet/overview")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "summary",
        "trend",
        "agents",
        "runtime_source",
        "usage_source",
    }
    assert body["agents"][0]["name"] == "HR 助手"
    assert body["agents"][0]["live_since"] == "2026-07-14T09:36:54.254859+08:00"
    assert body["agents"][0]["current_runtime_seconds"] == 3600
    assert "uptime_seconds" not in body["agents"][0]
    for forbidden in ("pm2_name", "port", "latency_ms", "health_url", "workdir"):
        assert forbidden not in response.text


def test_fleet_route_is_async():
    assert inspect.iscoroutinefunction(fleet_overview)
